"""Keep a task's unsent reminders in step with its deadline and status."""

from datetime import UTC, datetime

from sqlalchemy import delete, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Reminder, Task
from app.scheduler.ladder import build_ladder


def sync_reminders(
    session: Session,
    task: Task,
    *,
    now: datetime | None = None,
    created: bool = False,
    sms_enabled: bool | None = None,
) -> int:
    """Delete the task's unsent reminders and regenerate them. Returns how many were queued.

    Sent reminders are history and are left alone; a regenerated step that matches one of
    them (same task, time and channel) is not queued again. Tasks that are not open get no
    reminders. Does not commit. `created=True` adds the on-create inapp + push pair.
    """
    now = now or datetime.now(UTC)
    if sms_enabled is None:
        sms_enabled = get_settings().sms_enabled

    session.execute(delete(Reminder).where(Reminder.task_id == task.id, Reminder.sent_at.is_(None)))
    if task.status != "open" or not task.reminders_enabled:
        return 0

    email_sent = session.scalar(
        select(
            exists().where(
                Reminder.task_id == task.id,
                Reminder.channel == "email",
                Reminder.sent_at.is_not(None),
            )
        )
    )
    steps = build_ladder(
        task.deadline,
        now,
        created=created,
        sms_enabled=sms_enabled,
        email_already_sent=bool(email_sent),
    )
    if not steps:
        return 0
    result = session.execute(
        insert(Reminder)
        .values(
            [
                {"task_id": task.id, "fire_at": s.fire_at, "channel": s.channel, "kind": s.kind}
                for s in steps
            ]
        )
        .on_conflict_do_nothing(index_elements=["task_id", "fire_at", "channel"])
    )
    return result.rowcount
