"""Priority recompute: open tasks become urgent once their deadline is within 48 hours."""

from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.filters.deadline import URGENT_WINDOW
from app.models import Task


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
