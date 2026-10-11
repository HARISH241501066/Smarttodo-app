"""Data retention: finished tasks do not keep personal text forever.

Thirty days after a task was completed, the notes you wrote on it and the bodies of the
notifications about it are erased. The task itself (title, deadline, status) stays so history and
counts still make sense; deleting a task or the account removes everything.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Notification, Task

RETENTION = timedelta(days=30)


def purge_completed(session: Session, now: datetime | None = None) -> tuple[int, int]:
    """Returns (notes erased, notification bodies erased). Does not commit."""
    cutoff = (now or datetime.now(UTC)) - RETENTION
    old = select(Task.id).where(Task.status == "done", Task.updated_at < cutoff)
    notes = session.execute(
        update(Task)
        .where(Task.id.in_(old), Task.notes.is_not(None))
        .values(notes=None)
        .execution_options(synchronize_session=False)
    ).rowcount
    bodies = session.execute(
        update(Notification)
        .where(Notification.task_id.in_(old), Notification.body.is_not(None))
        .values(body=None)
        .execution_options(synchronize_session=False)
    ).rowcount
    return notes, bodies
