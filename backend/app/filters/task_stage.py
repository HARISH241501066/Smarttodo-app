"""Stage after the filters: sort a passed message (ignore / update / task / review), then create
the task, store the update, or fold the message into an existing task.

Only the subject (as the title), the extracted deadline, the chosen link and the sender address
are stored; the body is read in memory and dropped. Runs inside the filter pipeline's savepoint,
so it never commits. Only message and task ids are logged.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.filters.categorize import categorize, source_for
from app.filters.deadline import (
    CONF_MANY,
    DATE_RE,
    DeadlineResult,
    extract_deadline,
    priority_for,
)
from app.filters.links import LinkChoice, pick_action_link
from app.filters.pipeline import NextStage, StageOutcome
from app.filters.scam import is_trusted_tracked
from app.filters.triage import classify
from app.filters.triage_rules import DEADLINE_CONFLICT_GAP
from app.ingest.mime import ParsedMessage
from app.models import EmailUpdate, Task, User, UserSettings
from app.scheduler.reminders import sync_reminders

logger = logging.getLogger(__name__)

OPEN_CONFIDENCE = CONF_MANY  # a deadline found with at least this confidence is "sure enough"
MATCH_RATIO = 85  # rapidfuzz ratio must be strictly greater
MATCH_CANDIDATES = 300
TITLE_CHARS = 500

_PREFIX_RE = re.compile(r"^\s*(?:(?:re|fwd?)\s*:\s*)+", re.IGNORECASE)
# Words that differ between an original notice and its reminder/extension.
_NOISE = frozenset(
    "re fwd fw reminder extended extension deadline updated update revised urgent final last "
    "date till until to by on before upto".split()
)


def clean_title(subject: str) -> str:
    return _PREFIX_RE.sub("", subject).strip()[:TITLE_CHARS] or "(no subject)"


def _tokens(subject: str) -> list[str]:
    without_dates = DATE_RE.sub(" ", subject)
    words = re.findall(r"[a-z0-9]+", without_dates.lower())
    return [w for w in words if w not in _NOISE]


def _variants(subject: str) -> list[list[str]]:
    """Token lists for the whole subject and, if it has a "Label: ..." prefix, without the label."""
    variants = [_tokens(subject)]
    if ":" in subject:
        variants.append(_tokens(subject.split(":", 1)[1]))
    return [v for v in variants if v]


def same_topic(a: str, b: str) -> bool:
    """Subjects of the same notice: fuzzy match, and the same numbers (Assignment 3 != 4)."""
    for ta in _variants(a):
        for tb in _variants(b):
            if sorted(t for t in ta if t.isdigit()) != sorted(t for t in tb if t.isdigit()):
                continue
            if fuzz.ratio(" ".join(ta), " ".join(tb)) > MATCH_RATIO:
                return True
    return False


def _find_existing(session: Session, user_id: int, source: str, subject: str) -> Task | None:
    candidates = session.scalars(
        select(Task)
        .where(
            Task.user_id == user_id,
            Task.source == source,
            Task.status.in_(("open", "needs_review")),
        )
        .order_by(Task.created_at.desc())
        .limit(MATCH_CANDIDATES)
    )
    return next((t for t in candidates if same_topic(t.title, subject)), None)


@dataclass(frozen=True)
class Analysis:
    """What the message says about a task: its source, deadline, link and priority."""

    tracked: bool  # authenticated mail from an always-track sender
    category: str
    source: str
    deadline: DeadlineResult
    link: LinkChoice
    priority: str
    conflict: bool  # several different deadlines, so a person should look


def analyze(session: Session, user: User, message: ParsedMessage, now: datetime) -> Analysis:
    settings = session.get(UserSettings, user.id)
    # An authenticated mail from an always-track sender is always a task: it never waits in review.
    tracked = settings is not None and is_trusted_tracked(message, settings.always_track)
    category = categorize(message, settings.allowlist if settings else (), tracked=tracked)
    deadline = extract_deadline(
        f"{message.subject}\n{message.body_text}", message.received_at or now, now
    )
    priority = priority_for(category, deadline.deadline, now)
    if tracked and deadline.deadline is None:
        priority = "important"
    conflict = (
        not tracked
        and deadline.candidates > 1
        and deadline.latest is not None
        and deadline.deadline is not None
        and deadline.latest - deadline.deadline > DEADLINE_CONFLICT_GAP
    )
    return Analysis(
        tracked,
        category,
        source_for(category),
        deadline,
        pick_action_link(message),
        priority,
        conflict,
    )


def ingest_message(
    session: Session, user: User, message: ParsedMessage, now: datetime, *, review: bool = False
) -> tuple[Task | None, bool]:
    """Create a task for the message, or fold it into an existing one. Returns (task, created).

    A task is open even when no deadline was found (it then has deadline=null). It only waits in
    "needs review" when `review` is set (the triage cues conflicted) or the message gives several
    different deadlines. Tracked senders never wait in review."""
    a = analyze(session, user, message, now)
    existing = _find_existing(session, user.id, a.source, message.subject)
    if existing is not None:
        _merge(existing, message, a.category, a.deadline, a.link, now)
        logger.info("message %s merged into task %s", message.message_id, existing.id)
        return existing, False

    status = "needs_review" if (review or a.conflict) and not a.tracked else "open"
    if a.deadline.deadline is not None:
        confidence = a.deadline.confidence
    else:
        confidence = 1.0 if a.tracked else None
    task_id = session.execute(
        insert(Task)
        .values(
            user_id=user.id,
            title=clean_title(message.subject),
            source=a.source,
            source_ref=message.message_id,
            sender_email=message.sender_email or None,
            link=a.link.url,
            link_type=a.link.link_type,
            link_confidence=a.link.confidence,
            priority=a.priority,
            deadline=a.deadline.deadline,
            status=status,
            confidence=confidence,
            source_received_at=message.received_at,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "source_ref"])
        .returning(Task.id)
    ).scalar()
    logger.info(
        "message %s -> task %s source=%s status=%s link_type=%s",
        message.message_id, task_id, a.source, status, a.link.link_type,
    )  # fmt: skip
    if task_id is None:  # already created for this message
        return session.scalar(
            select(Task).where(Task.user_id == user.id, Task.source_ref == message.message_id)
        ), False
    return session.get(Task, task_id), True


def store_update(session: Session, user: User, message: ParsedMessage, now: datetime) -> None:
    """Keep an informational email as an "update": title, source, link and date, never the body."""
    settings = session.get(UserSettings, user.id)
    category = categorize(message, settings.allowlist if settings else ())
    link = pick_action_link(message)
    session.execute(
        insert(EmailUpdate)
        .values(
            user_id=user.id,
            message_id=message.message_id,
            title=clean_title(message.subject),
            source=source_for(category),
            link=link.url,
            link_type=link.link_type,
            received_at=message.received_at or now,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "message_id"])
    )


@dataclass(frozen=True)
class Routed:
    kind: str  # "ignore" | "update" | "task" | "review"
    reason: str
    task: Task | None = None
    created: bool = False


def route_message(session: Session, user: User, message: ParsedMessage, now: datetime) -> Routed:
    settings = session.get(UserSettings, user.id)
    tracked = settings is not None and is_trusted_tracked(message, settings.always_track)
    triage = classify(message, tracked)
    if triage.kind == "ignore":
        return Routed("ignore", triage.reason)
    if triage.kind == "update":
        store_update(session, user, message, now)
        return Routed("update", triage.reason)
    task, created = ingest_message(session, user, message, now, review=triage.kind == "review")
    if task is not None and task.status == "needs_review":
        reason = triage.reason if triage.kind == "review" else "review: several different deadlines"
        return Routed("review", reason, task, created)
    return Routed("task", triage.reason, task, created)


def make_triage_stage(
    session: Session, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
) -> NextStage:
    """The stage the sync uses: triage every passed email, then act on the result."""

    def stage(user: User, message: ParsedMessage) -> StageOutcome:
        now = clock()
        routed = route_message(session, user, message, now)
        if routed.task is not None:  # (re)build the reminder ladder; none for tasks in review
            sync_reminders(session, routed.task, now=now, created=routed.created)
        logger.info("message %s triaged as %s", message.message_id, routed.kind)
        return StageOutcome("ignored" if routed.kind == "ignore" else "passed", routed.reason)

    return stage


def make_task_stage(
    session: Session, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
) -> NextStage:
    """Turn every message into a task, without sorting it first (used where the user has already
    decided, and by tests of the task logic)."""

    def stage(user: User, message: ParsedMessage) -> None:
        now = clock()
        task, created = ingest_message(session, user, message, now)
        if task is not None:  # (re)build the reminder ladder; no-op for tasks that need review
            sync_reminders(session, task, now=now, created=created)

    return stage


def _merge(task, message, category, deadline, link, now) -> None:
    """An update for an existing task (extension, repeated reminder). Never touches the body."""
    newer = (
        task.source_received_at is None
        or message.received_at is None
        or message.received_at >= task.source_received_at
    )
    if not newer:  # an older email processed late must not overwrite newer information
        return
    if deadline.deadline is not None:
        task.deadline = deadline.deadline
        task.confidence = deadline.confidence
        task.priority = priority_for(
            "unknown" if task.source == "other" else task.source, deadline.deadline, now
        )
        if task.status == "needs_review" and deadline.confidence >= OPEN_CONFIDENCE:
            task.status = "open"
    if link.link_type == "action" or task.link is None:
        task.link, task.link_type, task.link_confidence = link.url, link.link_type, link.confidence
    if not task.sender_email and message.sender_email:
        task.sender_email = message.sender_email
    task.source_received_at = message.received_at or task.source_received_at
    task.updated_at = now
