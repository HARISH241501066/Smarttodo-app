"""Outbox dispatcher: sends due reminders exactly once and records an in-app notification.

Each reminder is claimed with FOR UPDATE SKIP LOCKED, sent, then marked sent together with
its notifications row in one commit, so two passes (or two processes) never send it twice.
If a send succeeds but that commit fails, the next pass sends it again (at-least-once);
providers that support idempotency keys should be given the reminder id.
Only ids and counts are logged.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Notification, Reminder, Task
from app.notify.channels import Outgoing, Sender, default_senders
from app.notify.render import render_reminder

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
BATCH_SIZE = 200
_DEADLINE_RELATIVE_KINDS = ("t48", "t24", "t10", "t1", "immediate")


@dataclass
class DispatchResult:
    sent: int = 0
    failed: int = 0
    skipped: int = 0  # task no longer open, or the deadline has passed


def dispatch_due(
    session: Session, *, now: datetime | None = None, senders: dict[str, Sender] | None = None
) -> DispatchResult:
    now = now or datetime.now(UTC)
    senders = senders or default_senders()
    result = DispatchResult()

    due_ids = list(
        session.scalars(
            select(Reminder.id)
            .where(
                Reminder.fire_at <= now,
                Reminder.sent_at.is_(None),
                Reminder.attempts < MAX_ATTEMPTS,
            )
            .order_by(Reminder.fire_at, Reminder.id)
            .limit(BATCH_SIZE)
        )
    )
    session.rollback()  # end the read transaction before claiming rows one by one

    for reminder_id in due_ids:
        reminder = session.scalar(
            select(Reminder)
            .where(Reminder.id == reminder_id, Reminder.sent_at.is_(None))
            .with_for_update(skip_locked=True)
        )
        if reminder is None:  # sent or claimed elsewhere in the meantime
            session.rollback()
            continue
        task = session.get(Task, reminder.task_id, populate_existing=True)
        if task is None or task.status != "open" or _past_deadline(reminder, task, now):
            session.delete(reminder)  # moot; regenerated if the task is reopened or changed
            session.commit()
            result.skipped += 1
            continue

        title, body = render_reminder(reminder.kind, task.title, task.deadline)
        message = Outgoing(
            task.user_id,
            task.id,
            reminder.id,
            reminder.channel,
            reminder.kind or "",
            title,
            body,
            task.link,
        )
        try:
            senders[reminder.channel].send(message)
        except Exception as exc:
            reminder.attempts += 1
            session.commit()
            result.failed += 1
            logger.warning(
                "reminder %s (%s) failed: %s, attempt %d/%d",
                reminder.id, reminder.channel, type(exc).__name__, reminder.attempts, MAX_ATTEMPTS,
            )  # fmt: skip
            continue

        reminder.sent_at = now
        session.add(
            Notification(
                user_id=task.user_id,
                task_id=task.id,
                reminder_id=reminder.id,
                kind="reminder",
                title=title,
                body=body,
                link=task.link,
                created_at=now,
            )
        )
        session.commit()
        result.sent += 1

    if result.sent or result.failed or result.skipped:
        logger.info(
            "dispatch: sent=%d failed=%d skipped=%d", result.sent, result.failed, result.skipped
        )
    return result


def _past_deadline(reminder: Reminder, task: Task, now: datetime) -> bool:
    return (
        reminder.kind in _DEADLINE_RELATIVE_KINDS
        and task.deadline is not None
        and task.deadline < now
    )
