"""State changes shared by the task routes and the email "Mark as done" page."""

from datetime import UTC, datetime

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import Reminder, Task


def cancel_pending_reminders(session: Session, task_id: int) -> None:
    session.execute(delete(Reminder).where(Reminder.task_id == task_id, Reminder.sent_at.is_(None)))


def close_task(session: Session, task: Task, status: str, now: datetime | None = None) -> None:
    """Mark a task done or dismissed and cancel its pending reminders. Does not commit; the caller
    syncs the Calendar event afterwards."""
    task.status = status
    task.updated_at = now or datetime.now(UTC)
    cancel_pending_reminders(session, task.id)
    session.flush()
