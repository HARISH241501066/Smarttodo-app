"""Create a task from a decided message, or fold it into an existing task.

Only the subject (as the title), the extracted deadline, the chosen link and the sender address
are stored; the body is read in memory and dropped. Runs inside the filter pipeline's savepoint,
so it never commits. Only message and task ids are logged.
"""

import logging
import re
from datetime import datetime

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.filters.deadline import (
    CONF_MANY,
    DATE_RE,
    extract_deadline,
    priority_for,
)
from app.filters.decide import REVIEW, Decision
from app.filters.links import pick_action_link
from app.filters.urls import unwrap_tracking_url
from app.ingest.mime import ParsedMessage
from app.models import Task, User

logger = logging.getLogger(__name__)

OPEN_CONFIDENCE = CONF_MANY  # a deadline found with at least this confidence is "sure enough"
MATCH_RATIO = 85  # rapidfuzz ratio must be strictly greater
MATCH_CANDIDATES = 300
TITLE_CHARS = 300

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


def _find_existing(
    session: Session, user_id: int, source: str, source_name: str | None, subject: str
) -> Task | None:
    candidates = session.scalars(
        select(Task)
        .where(
            Task.user_id == user_id,
            Task.source == source,
            Task.source_name.is_(source_name)
            if source_name is None
            else Task.source_name == source_name,
            Task.status.in_(("open", "needs_review")),
        )
        .order_by(Task.created_at.desc())
        .limit(MATCH_CANDIDATES)
    )
    return next((t for t in candidates if same_topic(t.title, subject)), None)


def ingest_message(
    session: Session,
    user: User,
    message: ParsedMessage,
    now: datetime,
    decision: Decision | None = None,
    *,
    confirmed: bool = False,
) -> tuple[Task | None, bool]:
    """Create a task for the message, or fold it into an existing one. Returns (task, created).

    `decision` is what filters.decide said (source, deadline, link, review). Without one (the
    user rescued a held message) the deadline and link are worked out here and the task is the
    user's own: `confirmed` marks tasks the user vouched for, which a rebuild never removes."""
    if decision is None:
        deadline = extract_deadline(
            f"{message.subject}\n{message.body_text}", message.received_at or now, now
        )
        link = pick_action_link(message)
        source, source_name, status = "other", None, "open"
    else:
        deadline = decision.deadline or extract_deadline("", now, now)
        link = decision.link or pick_action_link(message)
        source, source_name = decision.source_type, decision.source_name
        status = "needs_review" if decision.kind == REVIEW else "open"
        if status == "open" and deadline.deadline is not None and deadline.deadline < now:
            status = "done"  # deadline already over: straight to Completed

    own = session.scalar(
        select(Task).where(Task.user_id == user.id, Task.source_ref == message.message_id)
    )
    if own is not None:  # this very message again (a re-check): never fold it into another task
        if own.link and unwrap_tracking_url(own.link) != own.link:
            own.link = unwrap_tracking_url(own.link)  # a tracking wrapper that can be decoded now
        if decision is not None and not own.user_confirmed:
            own.source, own.source_name = source, source_name  # follow the source's current name
            if link.link_type == "action":  # e.g. a tracking wrapper that can now be decoded
                own.link, own.link_type, own.link_confidence = (
                    link.url,
                    link.link_type,
                    link.confidence,
                )
            # A better reading of the deadline (after a parser fix) replaces the stored one, unless
            # a newer email has since changed it (an extension): then this older message is not the
            # latest word on it.
            latest_word = own.source_received_at in (None, message.received_at)
            if deadline.deadline is not None and own.deadline != deadline.deadline and latest_word:
                own.deadline, own.confidence = deadline.deadline, deadline.confidence
                own.priority = priority_for(source, deadline.deadline, now)
                if own.status == "done" and deadline.deadline > now:
                    own.status = "open"  # it was only completed automatically on a wrong date
        return own, False
    existing = _find_existing(session, user.id, source, source_name, message.subject)
    if existing is not None:
        _merge(existing, message, deadline, link, now)
        logger.info("message %s merged into task %s", message.message_id, existing.id)
        return existing, False

    priority = priority_for(source, deadline.deadline, now)
    if deadline.deadline is None and decision is not None:
        priority = "important"  # from a source the user chose, so it matters
    task_id = session.execute(
        insert(Task)
        .values(
            user_id=user.id,
            title=clean_title(message.subject),
            source=source,
            source_name=source_name,
            source_ref=message.message_id,
            sender_email=message.sender_email or None,
            link=link.url,
            link_type=link.link_type,
            link_confidence=link.confidence,
            priority=priority,
            deadline=deadline.deadline,
            status=status,
            confidence=deadline.confidence if deadline.deadline is not None else None,
            user_confirmed=confirmed,
            source_received_at=message.received_at,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "source_ref"])
        .returning(Task.id)
    ).scalar()
    logger.info(
        "message %s -> task %s source=%s status=%s link_type=%s",
        message.message_id, task_id, source, status, link.link_type,
    )  # fmt: skip
    if task_id is None:  # created concurrently for this message
        return session.scalar(
            select(Task).where(Task.user_id == user.id, Task.source_ref == message.message_id)
        ), False
    return session.get(Task, task_id), True


def _merge(task, message, deadline, link, now) -> None:
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
        task.priority = priority_for(task.source, deadline.deadline, now)
        if task.status == "needs_review" and deadline.confidence >= OPEN_CONFIDENCE:
            task.status = "open"
    if link.link_type == "action" or task.link is None:
        task.link, task.link_type, task.link_confidence = link.url, link.link_type, link.confidence
    if not task.sender_email and message.sender_email:
        task.sender_email = message.sender_email
    task.source_received_at = message.received_at or task.source_received_at
    task.updated_at = now
