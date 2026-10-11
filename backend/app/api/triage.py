"""Per-sender bulk actions on the review queue. Every query is scoped to the session user."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.db import get_session
from app.models import Task, User
from app.scheduler.reminders import sync_reminders
from app.schemas.triage import BulkOut, BulkReview
from app.task_ops import close_task

router = APIRouter(tags=["triage"])


@router.post("/review/bulk", response_model=BulkOut)
def review_bulk(
    body: BulkReview,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> BulkOut:
    """Confirm or dismiss every review item from one sender address."""
    tasks = session.scalars(
        select(Task).where(
            Task.user_id == user.id, Task.status == "needs_review", Task.sender_email == body.sender
        )
    ).all()
    now = datetime.now(UTC)
    for task in tasks:
        if body.action == "confirm":
            task.status = "open"
            task.confidence = 1.0
            task.user_confirmed = True
            task.updated_at = now
            session.flush()
            # No "new task" notification per item: the user just did this on purpose, in bulk.
            sync_reminders(session, task, now=now, created=False)
        else:
            close_task(session, task, "dismissed", now)
    session.commit()
    return BulkOut(updated=len(tasks))
