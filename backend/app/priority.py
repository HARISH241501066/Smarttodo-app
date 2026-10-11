"""Priority recompute: open tasks become urgent once their deadline is within 48 hours."""

from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.filters.deadline import URGENT_WINDOW
from app.models import Reminder, Task


def recompute_priorities(
    session: Session, now: datetime | None = None, user_id: int | None = None
) -> int:
    """Upgrade open tasks (never downgrade) and return how many changed. Does not commit.

    Overdue open tasks count as "within 48 hours". `user_id` limits the sweep to one user.
    """
    now = now or datetime.now(UTC)
    stmt = (
        update(Task)
        .where(
            Task.status == "open",
            Task.priority != "urgent",
            Task.deadline.is_not(None),
            Task.deadline <= now + URGENT_WINDOW,
        )
        .values(priority="urgent", updated_at=now)
    )
    if user_id is not None:
        stmt = stmt.where(Task.user_id == user_id)
    return session.execute(stmt).rowcount


def complete_expired_email_tasks(
    session: Session, now: datetime | None = None, user_id: int | None = None
) -> int:
    """Email tasks whose deadline has passed go to Completed by themselves (tasks you created
    yourself stay open and show as overdue). Their unsent reminders are removed. Does not commit."""
    now = now or datetime.now(UTC)
    stmt = select(Task.id).where(
        Task.status == "open",
        Task.source != "manual",
        Task.deadline.is_not(None),
        Task.deadline < now,
    )
    if user_id is not None:
        stmt = stmt.where(Task.user_id == user_id)
    ids = list(session.scalars(stmt))
    if not ids:
        return 0
    session.execute(delete(Reminder).where(Reminder.task_id.in_(ids), Reminder.sent_at.is_(None)))
    session.execute(update(Task).where(Task.id.in_(ids)).values(status="done", updated_at=now))
    return len(ids)
