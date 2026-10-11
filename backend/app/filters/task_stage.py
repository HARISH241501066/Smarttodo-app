"""Stage after the filters: turn a passed message into a task, or fold it into an existing one.

Only the subject (as the title), the extracted deadline and the chosen link are stored; the
body is read in memory and dropped. Runs inside the filter pipeline's savepoint, so it never
commits. Only message and task ids are logged.
"""

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.filters.categorize import categorize, source_for
from app.filters.deadline import CONF_MANY, DATE_RE, extract_deadline, priority_for
from app.filters.links import pick_action_link
from app.filters.pipeline import NextStage
from app.ingest.mime import ParsedMessage
from app.models import Task, User, UserSettings

logger = logging.getLogger(__name__)

OPEN_CONFIDENCE = CONF_MANY  # >= this -> "open", otherwise "needs_review"
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


def ingest_message(
    session: Session, user: User, message: ParsedMessage, now: datetime
) -> tuple[Task | None, bool]:
    """Create a task for the message, or fold it into an existing one. Returns (task, created)."""
    settings = session.get(UserSettings, user.id)
    category = categorize(message, settings.allowlist if settings else ())
    source = source_for(category)
    deadline = extract_deadline(
        f"{message.subject}\n{message.body_text}", message.received_at or now, now
    )
    link = pick_action_link(message)
    status = "open" if deadline.confidence >= OPEN_CONFIDENCE else "needs_review"

    existing = _find_existing(session, user.id, source, message.subject)
    if existing is not None:
        _merge(existing, message, category, deadline, link, now)
        logger.info("message %s merged into task %s", message.message_id, existing.id)
        return existing, False

    task_id = session.execute(
        insert(Task)
        .values(
            user_id=user.id,
            title=clean_title(message.subject),
            source=source,
            source_ref=message.message_id,
            link=link.url,
            link_type=link.link_type,
            link_confidence=link.confidence,
            priority=priority_for(category, deadline.deadline, now),
            deadline=deadline.deadline,
            status=status,
            confidence=deadline.confidence,
            source_received_at=message.received_at,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "source_ref"])
        .returning(Task.id)
    ).scalar()
    logger.info(
        "message %s -> task %s source=%s status=%s link_type=%s",
        message.message_id, task_id, source, status, link.link_type,
    )  # fmt: skip
    if task_id is None:  # already created for this message
        return session.scalar(
            select(Task).where(Task.user_id == user.id, Task.source_ref == message.message_id)
        ), False
    return session.get(Task, task_id), True


def make_task_stage(
    session: Session, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
) -> NextStage:
    def stage(user: User, message: ParsedMessage) -> None:
        ingest_message(session, user, message, clock())

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
    task.source_received_at = message.received_at or task.source_received_at
    task.updated_at = now
