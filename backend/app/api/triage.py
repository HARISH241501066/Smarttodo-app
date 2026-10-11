"""Updates (informational emails), re-sorting the review queue, and per-sender bulk actions.
Every query is scoped to the session user."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from googleapiclient.errors import HttpError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.sync import get_gmail_api
from app.auth.deps import get_current_user
from app.db import get_session
from app.filters.deadline import priority_for
from app.filters.reprocess import reprocess_review
from app.ingest.gmail import GmailApi, RateLimited
from app.ingest.sync import SyncInProgress, user_sync_lock
from app.models import EmailUpdate, Task, User
from app.scheduler.reminders import sync_reminders
from app.schemas.tasks import TaskOut, link_domain
from app.schemas.triage import BulkOut, BulkReview, ReprocessOut, UpdateOut
from app.task_ops import close_task

logger = logging.getLogger(__name__)
router = APIRouter(tags=["triage"])

REPROCESS_MAX_BATCH = 100


def _update_out(row: EmailUpdate) -> UpdateOut:
    out = UpdateOut.model_validate(row)
    out.link_domain = link_domain(row.link)
    return out


def _owned_update(session: Session, user: User, update_id: int) -> EmailUpdate:
    row = session.scalar(
        select(EmailUpdate).where(EmailUpdate.id == update_id, EmailUpdate.user_id == user.id)
    )
    if row is None:
        raise HTTPException(404, "Update not found")
    return row


@router.get("/updates", response_model=list[UpdateOut])
def list_updates(
    limit: int = Query(200, ge=1, le=500),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[UpdateOut]:
    rows = session.scalars(
        select(EmailUpdate)
        .where(EmailUpdate.user_id == user.id)
        .order_by(EmailUpdate.received_at.desc().nulls_last(), EmailUpdate.id.desc())
        .limit(limit)
    )
    return [_update_out(r) for r in rows]


@router.post("/updates/{update_id}/task", response_model=TaskOut)
def update_to_task(
    update_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> TaskOut:
    """The user decided this one is a to-do after all: it becomes an open task with no deadline."""
    row = _owned_update(session, user, update_id)
    existing = session.scalar(
        select(Task).where(Task.user_id == user.id, Task.source_ref == row.message_id)
    )
    task = existing
    if task is None:
        task = Task(
            user_id=user.id,
            title=row.title,
            source=row.source,
            source_ref=row.message_id,
            link=row.link,
            link_type=row.link_type,
            link_confidence=1.0 if row.link_type == "action" else 0.0,
            priority=priority_for(row.source, None, datetime.now(UTC)),
            status="open",
            confidence=1.0,
            source_received_at=row.received_at,
        )
        session.add(task)
    session.delete(row)
    session.flush()
    sync_reminders(session, task, created=False)  # no deadline: nothing to schedule
    session.commit()
    return TaskOut.from_task(task)


@router.post("/updates/{update_id}/dismiss", status_code=204)
def dismiss_update(
    update_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Response:
    session.delete(_owned_update(session, user, update_id))
    session.commit()  # the message stays recorded as handled, so it does not come back
    return Response(status_code=204)


@router.post("/review/reprocess", response_model=ReprocessOut)
def reprocess(
    after_id: int = Query(0, ge=0),
    limit: int | None = Query(None, ge=1, le=REPROCESS_MAX_BATCH),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    gmail: GmailApi = Depends(get_gmail_api),
) -> ReprocessOut:
    """Re-sort the Needs-review queue with the current triage rules. Each email is fetched again
    in memory only. Pass `limit` to work in batches and call again with the returned
    `next_after_id`; without it everything is done in one call."""
    try:
        with user_sync_lock(session, user.id):
            result = reprocess_review(session, user, gmail, after_id=after_id, limit=limit)
    except SyncInProgress as exc:
        raise HTTPException(409, "A sync is running; try again in a minute") from exc
    except RateLimited as exc:
        raise HTTPException(429, "Gmail rate limit reached; try again in a minute") from exc
    except HttpError as exc:
        logger.warning("gmail api error during reprocess: status=%s", exc.resp.status)
        raise HTTPException(502, "Gmail API error") from exc
    return ReprocessOut(
        total=result.total,
        task=result.task,
        update=result.update,
        ignore=result.ignore,
        review=result.review,
        gone=result.gone,
        next_after_id=result.next_after_id,
    )


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
            task.updated_at = now
            session.flush()
            # No "new task" notification per item: the user just did this on purpose, in bulk.
            sync_reminders(session, task, now=now, created=False)
        else:
            close_task(session, task, "dismissed", now)
    session.commit()
    return BulkOut(updated=len(tasks))
