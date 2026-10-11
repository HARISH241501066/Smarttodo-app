"""Re-sort the "Needs review" queue with the current triage rules.

Each review task remembers its Gmail message id, so the message is fetched again (in memory only,
nothing about its body is kept) and triaged afresh. Every item ends up as a task (now open), an
update, ignored, or stays in review. Running it again changes nothing more, because items that
moved are no longer in the queue and the ones left are triaged to the same answer.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.filters.pipeline import record_decision
from app.filters.scam import is_trusted_tracked
from app.filters.task_stage import analyze
from app.filters.triage import classify
from app.ingest.gmail import GmailApi, MessageGone
from app.ingest.mime import parse_message
from app.models import EmailUpdate, Task, User, UserSettings
from app.scheduler.reminders import sync_reminders

logger = logging.getLogger(__name__)


@dataclass
class ReprocessResult:
    total: int = 0  # review items looked at in this batch
    task: int = 0  # became an open task
    update: int = 0  # became an update
    ignore: int = 0  # removed (OTP, receipt, ...)
    review: int = 0  # still need a person
    gone: int = 0  # the email no longer exists in Gmail; left in review
    next_after_id: int | None = None  # pass back as `after_id` to continue (None = finished)


def reprocess_review(
    session: Session,
    user: User,
    gmail: GmailApi,
    *,
    now: datetime | None = None,
    after_id: int = 0,
    limit: int | None = None,
) -> ReprocessResult:
    """Re-triage review tasks with id > after_id, in id order, at most `limit` of them.
    Commits after every item, so a rate limit or crash part way keeps the progress made."""
    now = now or datetime.now(UTC)
    stmt = (
        select(Task)
        .where(Task.user_id == user.id, Task.status == "needs_review", Task.id > after_id)
        .order_by(Task.id)
    )
    if limit:
        stmt = stmt.limit(limit)
    tasks = session.scalars(stmt).all()
    result = ReprocessResult(total=len(tasks))
    if limit and len(tasks) == limit:
        result.next_after_id = tasks[-1].id

    settings = session.get(UserSettings, user.id)
    for task in tasks:
        if not task.source_ref:  # entered by hand or pre-dates message ids: nothing to re-read
            result.review += 1
            continue
        try:
            parsed = parse_message(gmail.get_message(task.source_ref), gmail.get_attachment)
        except MessageGone:
            result.gone += 1
            continue  # (RateLimited propagates; what was done so far is already committed)

        tracked = settings is not None and is_trusted_tracked(parsed, settings.always_track)
        triage = classify(parsed, tracked)

        if triage.kind == "ignore":
            record_decision(session, user.id, task.source_ref, "ignored", triage.reason)
            session.delete(task)
            result.ignore += 1
        elif triage.kind == "update":
            session.execute(
                insert(EmailUpdate)
                .values(
                    user_id=user.id,
                    message_id=task.source_ref,
                    title=task.title,
                    source=task.source if task.source != "manual" else "other",
                    link=task.link,
                    link_type=task.link_type,
                    received_at=task.source_received_at or task.created_at,
                )
                .on_conflict_do_nothing(index_elements=["user_id", "message_id"])
            )
            record_decision(session, user.id, task.source_ref, "passed", triage.reason)
            session.delete(task)
            result.update += 1
        else:
            a = analyze(session, user, parsed, now)
            task.sender_email = parsed.sender_email or task.sender_email
            task.source = a.source
            task.deadline = a.deadline.deadline
            task.priority = a.priority
            if a.link.link_type == "action" or task.link is None:
                task.link, task.link_type = a.link.url, a.link.link_type
                task.link_confidence = a.link.confidence
            stays = (triage.kind == "review" or a.conflict) and not a.tracked
            if stays:
                result.review += 1
            else:
                task.status = "open"
                task.confidence = a.deadline.confidence if a.deadline.deadline else None
                task.updated_at = now
                # An existing task, not a new one: no "new task" notification, just its deadlines.
                sync_reminders(session, task, now=now, created=False)
                record_decision(session, user.id, task.source_ref, "passed", triage.reason)
                result.task += 1
        session.commit()
    logger.info(
        "reprocess user=%s: total=%d task=%d update=%d ignore=%d review=%d gone=%d",
        user.id,
        result.total,
        result.task,
        result.update,
        result.ignore,
        result.review,
        result.gone,
    )  # fmt: skip
    return result
