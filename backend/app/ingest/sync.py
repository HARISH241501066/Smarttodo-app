"""Gmail sync: one source-based search, per-message isolation.

The only mail ever listed is what `build_query` asks for (mail from the user's sources, or
mentioning a watch keyword). Nothing outside that query is fetched. Messages already handled are
skipped *before* fetching, so a repeated sync only downloads new mail.

Parsed messages are handed to a `sink`. Persisting them (tasks etc.) is the job of the filter
pipeline; the sink runs inside a SAVEPOINT so a failing sink never leaves partial writes, and it
must not commit. Sinks must be idempotent on message id (tasks use UNIQUE(user_id, source_ref)).

Privacy: only message ids and counts are logged, never subjects, senders or bodies.
"""

import logging
import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.ingest.gmail import GmailApi, MessageGone, RateLimited
from app.ingest.mime import ParsedMessage, parse_message
from app.ingest.query import BACKFILL_DAYS, build_query, labels_allowed
from app.models import IngestFailure, ProcessedMessage, SyncState, User, UserSettings
from app.sources import DEFAULT_KEYWORDS, DEFAULT_SOURCES, load_sources

logger = logging.getLogger(__name__)

MAX_RETRY_ATTEMPTS = 3
LOCK_NAMESPACE = 0x53594E43  # "SYNC"
ERROR_MAX_CHARS = 200
OVERLAP_DAYS = 2  # a later sync re-lists a little of the past, in case of clock/indexing lag

# A sink returns None (plain delivery) or the decision it took: "passed" | "quarantined" |
# "dropped" | "ignored" | "skipped" (already handled) | "failed" (the sink recorded the failure
# itself and swallowed it).
Sink = Callable[[User, ParsedMessage], str | None]


class SyncInProgress(Exception):
    """Another sync is already running for this user."""


@dataclass
class SyncResult:
    mode: str  # "backfill" (first run, or a rebuild) | "incremental"
    query: str | None = None  # the Gmail search that was run (None: nothing to look for)
    discovered: int = 0  # unique message ids found (plus retried failures)
    fetched: int = 0  # messages actually retrieved from Gmail
    delivered: int = 0  # handed to the sink
    skipped: int = 0  # already handled, spam/trash, or deleted before fetch
    failed: int = 0  # recorded in ingest_failures this run
    deferred: int = 0  # not attempted because Gmail rate-limited us; retried next sync
    rate_limited: bool = False
    decisions: dict[str, int] = field(default_factory=dict)  # sink decisions, when it reports them
    delivered_ids: list[str] = field(default_factory=list)


def discard_sink(user: User, message: ParsedMessage) -> str | None:
    """Placeholder sink for callers that only want the fetching."""


@contextmanager
def user_sync_lock(session: Session, user_id: int) -> Iterator[None]:
    """Postgres advisory lock held on a dedicated connection for the whole sync."""
    key = LOCK_NAMESPACE * 2**32 + user_id % 2**32
    with session.get_bind().connect() as conn:
        acquired = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar_one()
        conn.commit()
        if not acquired:
            raise SyncInProgress(f"sync already running for user {user_id}")
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
            conn.commit()


def get_or_create_settings(session: Session, user_id: int) -> UserSettings:
    row = session.get(UserSettings, user_id)
    if row is None:
        row = UserSettings(
            user_id=user_id,
            sources=[dict(s, senders=list(s["senders"])) for s in DEFAULT_SOURCES],
            watch_keywords=list(DEFAULT_KEYWORDS),
        )
        session.add(row)
        session.commit()
    return row


def _get_or_create_state(session: Session, user_id: int) -> SyncState:
    row = session.get(SyncState, user_id)
    if row is None:
        row = SyncState(user_id=user_id)
        session.add(row)
        session.commit()
    return row


def _window_days(state: SyncState, full: bool) -> int:
    """30 days on the first run or a rebuild; afterwards just since the last good sync."""
    if full or state.last_success_at is None:
        return BACKFILL_DAYS
    elapsed = (datetime.now(UTC) - state.last_success_at).total_seconds() / 86400
    return max(OVERLAP_DAYS, min(BACKFILL_DAYS, math.ceil(elapsed) + OVERLAP_DAYS))


def _list_ids(gmail: GmailApi, query: str) -> list[str]:
    ids: dict[str, None] = {}
    token = None
    while True:
        page, token = gmail.list_messages(query, token)
        for message_id in page:
            ids.setdefault(message_id, None)
        if not token:
            return list(ids)


def record_failure(session: Session, user_id: int, message_id: str, exc: Exception) -> None:
    error = f"{type(exc).__name__}: {exc}"[:ERROR_MAX_CHARS]
    row = session.scalar(
        select(IngestFailure).where(
            IngestFailure.user_id == user_id, IngestFailure.message_id == message_id
        )
    )
    if row:
        row.attempts += 1
        row.error = error
    else:
        session.add(IngestFailure(user_id=user_id, message_id=message_id, error=error, attempts=1))


def _clear_failure(session: Session, user_id: int, message_id: str) -> None:
    for row in session.scalars(
        select(IngestFailure).where(
            IngestFailure.user_id == user_id, IngestFailure.message_id == message_id
        )
    ):
        session.delete(row)


def _defer(session: Session, user_id: int, message_ids: list[str]) -> None:
    """Queue messages for the next sync without consuming a retry attempt (attempts=0)."""
    existing = set(
        session.scalars(
            select(IngestFailure.message_id).where(
                IngestFailure.user_id == user_id, IngestFailure.message_id.in_(message_ids)
            )
        )
    )
    for message_id in message_ids:
        if message_id not in existing:
            session.add(
                IngestFailure(
                    user_id=user_id,
                    message_id=message_id,
                    error="RateLimited: deferred to the next sync",
                    attempts=0,
                )
            )
    session.commit()


def run_sync(
    user: User,
    session: Session,
    gmail: GmailApi,
    sink: Sink = discard_sink,
    *,
    full: bool = False,
) -> SyncResult:
    """`full=True` (a rebuild) looks back the whole 30 days and re-handles mail already seen."""
    with user_sync_lock(session, user.id):
        return _run(user, session, gmail, sink, full)


def _handled_ids(session: Session, user_id: int, ids: list[str]) -> set[str]:
    """Ids that already have a final decision (a recorded failure is not final)."""
    done: set[str] = set()
    for start in range(0, len(ids), 500):
        done.update(
            session.scalars(
                select(ProcessedMessage.message_id).where(
                    ProcessedMessage.user_id == user_id,
                    ProcessedMessage.message_id.in_(ids[start : start + 500]),
                    ProcessedMessage.decision != "failed",
                )
            )
        )
    return done


def _run(user: User, session: Session, gmail: GmailApi, sink: Sink, full: bool) -> SyncResult:
    settings = get_or_create_settings(session, user.id)
    state = _get_or_create_state(session, user.id)
    days = _window_days(state, full)
    query = build_query(load_sources(settings.sources), settings.watch_keywords, days)

    result = SyncResult(mode="backfill" if days == BACKFILL_DAYS else "incremental", query=query)
    candidates: list[str] = _list_ids(gmail, query) if query else []

    # Earlier failures get another chance, but only MAX_RETRY_ATTEMPTS times: the query lists the
    # same mail every run, so one that keeps failing must not be fetched forever.
    exhausted = set(
        session.scalars(
            select(IngestFailure.message_id).where(
                IngestFailure.user_id == user.id, IngestFailure.attempts >= MAX_RETRY_ATTEMPTS
            )
        )
    )
    candidates = [mid for mid in candidates if mid not in exhausted]
    seen = set(candidates)
    for message_id in session.scalars(
        select(IngestFailure.message_id).where(
            IngestFailure.user_id == user.id, IngestFailure.attempts < MAX_RETRY_ATTEMPTS
        )
    ):
        if message_id not in seen:
            candidates.append(message_id)
            seen.add(message_id)

    result.discovered = len(candidates)
    if not full:
        handled = _handled_ids(session, user.id, candidates)
        result.skipped += len(handled)
        pending = [mid for mid in candidates if mid not in handled]
    else:
        pending = candidates

    for position, message_id in enumerate(pending):
        try:
            _process_message(user, session, gmail, sink, message_id, result)
        except RateLimited:
            # Quota isn't message-specific: park the rest instead of failing each one.
            rest = pending[position:]
            _defer(session, user.id, rest)
            result.deferred, result.rate_limited = len(rest), True
            logger.warning("user %s rate limited; deferred %d messages", user.id, len(rest))
            break

    if not result.rate_limited:
        state.last_success_at = datetime.now(UTC)
    session.commit()

    logger.info(
        "sync user=%s mode=%s discovered=%d fetched=%d delivered=%d skipped=%d failed=%d "
        "deferred=%d decisions=%s",
        user.id, result.mode, result.discovered, result.fetched, result.delivered,
        result.skipped, result.failed, result.deferred, result.decisions,
    )  # fmt: skip
    logger.info("sync user=%s delivered ids (first 20): %s", user.id, result.delivered_ids[:20])
    logger.debug("sync user=%s all delivered ids: %s", user.id, result.delivered_ids)
    return result


def _process_message(
    user: User,
    session: Session,
    gmail: GmailApi,
    sink: Sink,
    message_id: str,
    result: SyncResult,
) -> None:
    try:
        raw = gmail.get_message(message_id)
        result.fetched += 1
        parsed = parse_message(raw, gmail.get_attachment)
        if not labels_allowed(parsed.labels):
            outcome = "skipped"
        else:
            with session.begin_nested():
                decision = sink(user, parsed)
            if decision:
                result.decisions[decision] = result.decisions.get(decision, 0) + 1
            outcome = {"skipped": "skipped", "failed": "failed"}.get(decision, "delivered")
    except MessageGone:
        outcome = "skipped"
    except RateLimited:
        raise
    except Exception as exc:
        logger.warning("message %s failed: %s", message_id, type(exc).__name__)
        record_failure(session, user.id, message_id, exc)
        session.commit()
        result.failed += 1
        return

    if outcome == "failed":  # the sink already wrote processed_messages + ingest_failures
        session.commit()
        result.failed += 1
        return

    _clear_failure(session, user.id, message_id)
    session.commit()
    if outcome == "delivered":
        result.delivered += 1
        result.delivered_ids.append(message_id)
    else:
        result.skipped += 1
