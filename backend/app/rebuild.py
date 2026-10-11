"""Rebuild from sources: re-check the last 30 days against the CURRENT sources and rules.

1. Re-run the pipeline over every message the new query finds (decisions are made again).
2. Then remove the tasks that were created automatically and no longer match: their email was
   decided "not a task", or the sender/keyword is no longer in the query.

Never touched, whatever the rules say:
    * manual tasks (source = manual)
    * tasks the user confirmed or edited (user_confirmed)
    * tasks that are done or dismissed (only open / needs-review tasks are ever removed)
    * tasks received before the 30-day window (they cannot be re-checked)

Safe to run twice: a second run finds nothing more to remove or create. Only counts are returned
or logged.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_engine
from app.filters.pipeline import make_filter_sink
from app.ingest.gmail import GmailApi
from app.ingest.query import BACKFILL_DAYS
from app.ingest.sync import SyncInProgress, get_or_create_settings, run_sync
from app.models import IngestFailure, Quarantine, Task, User
from app.notify.calendar import CalendarApi, delete_event_for_removed_task
from app.sources import find_source, load_sources

logger = logging.getLogger(__name__)


def _auto_candidates(user_id: int, since: datetime):
    """Open / review tasks the app created itself, inside the window, that the user has not
    taken ownership of. The ONLY tasks a rebuild may remove."""
    return (
        select(Task)
        .where(
            Task.user_id == user_id,
            Task.source != "manual",
            Task.user_confirmed.is_(False),
            Task.status.in_(("open", "needs_review")),
            Task.source_ref.is_not(None),
            or_(Task.source_received_at.is_(None), Task.source_received_at >= since),
        )
        .order_by(Task.id)
    )


@dataclass
class RebuildPreview:
    window_days: int
    rechecked: int  # automatic tasks that will be looked at again
    will_remove: int  # of those, the ones from senders no longer in any source (certain)
    review_items: int  # automatic review items among them
    protected: int  # tasks that will not be touched (manual, confirmed, done)


def preview(session: Session, user: User, now: datetime | None = None) -> RebuildPreview:
    now = now or datetime.now(UTC)
    since = now - timedelta(days=BACKFILL_DAYS)
    sources = load_sources(get_or_create_settings(session, user.id).sources)
    auto = list(session.scalars(_auto_candidates(user.id, since)))
    # Keyword tasks (no source sender) are re-judged from the message, so only tasks from a
    # source sender that is gone can be called "will be removed" without fetching anything.
    gone = [
        t for t in auto if t.source != "keyword" and find_source(t.sender_email, sources) is None
    ]
    total = session.scalar(select(func.count()).select_from(Task).where(Task.user_id == user.id))
    return RebuildPreview(
        window_days=BACKFILL_DAYS,
        rechecked=len(auto),
        will_remove=len(gone),
        review_items=sum(1 for t in auto if t.status == "needs_review"),
        protected=(total or 0) - len(auto),
    )


@dataclass
class RebuildResult:
    removed: int = 0
    created: int = 0  # tasks that did not exist before
    kept: int = 0  # automatic tasks that still match
    tasks_by_source: dict[str, int] = field(default_factory=dict)  # open tasks now, per source name
    dropped: int = 0
    ignored: int = 0
    suspicious: int = 0
    review: int = 0
    fetched: int = 0
    failed: int = 0
    incomplete: bool = False  # Gmail rate-limited the run: deletions were limited to what was seen


def run_rebuild(
    session: Session,
    user: User,
    gmail: GmailApi,
    calendar: CalendarApi | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RebuildResult:
    started = clock()
    since = started - timedelta(days=BACKFILL_DAYS)
    last_id = session.scalar(select(func.max(Task.id)).where(Task.user_id == user.id)) or 0
    seen: set[str] = set()
    kept_ids: set[str] = set()
    inner = make_filter_sink(session, clock, reprocess=True)

    def sink(u: User, message) -> str:
        decision = inner(u, message)
        if decision != "failed":
            seen.add(message.message_id)
        if decision == "passed":
            kept_ids.add(message.message_id)
        return decision

    sync = run_sync(user, session, gmail, sink, full=True)
    result = RebuildResult(
        fetched=sync.fetched,
        failed=sync.failed,
        dropped=sync.decisions.get("dropped", 0),
        ignored=sync.decisions.get("ignored", 0),
        suspicious=sync.decisions.get("quarantined", 0),
        incomplete=sync.rate_limited,
    )

    # Messages that could not be judged this time must keep their task.
    unjudged = set(
        session.scalars(select(IngestFailure.message_id).where(IngestFailure.user_id == user.id))
    )
    old_tasks = [  # only tasks that existed before this run: nothing it just made is removed
        t for t in session.scalars(_auto_candidates(user.id, since)) if t.id <= last_id
    ]
    for task in old_tasks:
        ref = task.source_ref
        if ref in kept_ids or ref in unjudged:
            result.kept += 1
            continue
        if ref not in seen and sync.rate_limited:  # not looked at: leave it for the next run
            result.kept += 1
            continue
        delete_event_for_removed_task(session, calendar, user.id, task.gcal_event_id)
        session.delete(task)  # reminders and notifications cascade
        result.removed += 1
    session.commit()

    result.created = (
        session.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.user_id == user.id, Task.id > last_id, Task.source != "manual")
        )
        or 0
    )
    result.review = (
        session.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.user_id == user.id, Task.status == "needs_review", Task.source != "manual")
        )
        or 0
    )
    label = func.coalesce(Task.source_name, Task.source)
    rows = session.execute(
        select(label, func.count())
        .where(
            Task.user_id == user.id,
            Task.source != "manual",
            Task.status.in_(("open", "needs_review")),
        )
        .group_by(label)
    )
    result.tasks_by_source = {name: count for name, count in rows}
    # Suspicious rows that are no longer suspicious (their message was re-judged) are cleared by
    # the pipeline; report what is held now.
    result.suspicious = (
        session.scalar(
            select(func.count())
            .select_from(Quarantine)
            .where(Quarantine.user_id == user.id, Quarantine.status == "held")
        )
        or 0
    )
    logger.info(
        "rebuild user=%s removed=%d created=%d kept=%d dropped=%d ignored=%d suspicious=%d "
        "review=%d failed=%d incomplete=%s",
        user.id, result.removed, result.created, result.kept, result.dropped, result.ignored,
        result.suspicious, result.review, result.failed, result.incomplete,
    )  # fmt: skip
    return result


# ---------------------------------------------------------------------------------------------
# Running in the background: a rebuild can take a while (it fetches up to 30 days of mail), so
# the endpoint starts it and the page polls the status. One run per user at a time.
# ---------------------------------------------------------------------------------------------

_status_lock = threading.Lock()
_status: dict[int, dict] = {}


def get_status(user_id: int) -> dict:
    with _status_lock:
        return dict(_status.get(user_id, {"state": "idle"}))


def _set_status(user_id: int, **values) -> None:
    with _status_lock:
        _status[user_id] = values


def start_rebuild(
    user_id: int,
    gmail: GmailApi,
    calendar: CalendarApi | None,
    session_factory: Callable[[], Session] | None = None,
    *,
    wait: bool = False,
) -> dict:
    """Start a rebuild. With wait=True run it in this thread and return the final status."""
    with _status_lock:
        if _status.get(user_id, {}).get("state") == "running":
            raise SyncInProgress("a rebuild is already running")
        _status[user_id] = {"state": "running", "started_at": datetime.now(UTC).isoformat()}
    factory = session_factory or sessionmaker(get_engine(), expire_on_commit=False)

    def work() -> None:
        try:
            with factory() as session:
                user = session.get(User, user_id)
                result = run_rebuild(session, user, gmail, calendar)
            _set_status(user_id, state="done", result=asdict(result))
        except SyncInProgress:
            _set_status(user_id, state="error", error="A sync is running; try again in a minute")
        except Exception as exc:  # report the type only, never message content
            logger.warning("rebuild user=%s failed: %s", user_id, type(exc).__name__)
            _set_status(user_id, state="error", error=f"Rebuild failed ({type(exc).__name__})")

    if wait:
        work()
    else:
        threading.Thread(target=work, name=f"rebuild-{user_id}", daemon=True).start()
    return get_status(user_id)
