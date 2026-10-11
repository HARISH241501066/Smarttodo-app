"""Task CRUD, done/dismiss and the needs-review queue; every query is scoped to the user."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.db import get_session
from app.filters.deadline import priority_for
from app.filters.links import gmail_fallback
from app.models import Task, User
from app.notify.calendar import (
    CalendarApi,
    delete_event_for_removed_task,
    get_calendar_api,
    try_sync_task_event,
)
from app.priority import recompute_priorities
from app.scheduler.reminders import sync_reminders
from app.schemas.tasks import (
    Priority,
    ReviewConfirm,
    Source,
    Status,
    TaskCreate,
    TaskOut,
    TaskUpdate,
    to_utc,
)
from app.task_ops import cancel_pending_reminders, close_task

router = APIRouter(tags=["tasks"])

DEFAULT_LIMIT = 200
MAX_LIMIT = 500


def owned_task(session: Session, user: User, task_id: int) -> Task:
    """404 (not 403) for other users' tasks, so ids can't be probed."""
    task = session.scalar(select(Task).where(Task.id == task_id, Task.user_id == user.id))
    if task is None:
        raise HTTPException(404, "Task not found")
    return task


def _category(task: Task) -> str:
    return "unknown" if task.source in ("other", "manual") else task.source


def _set_link(task: Task, link: str | None) -> None:
    """A user-supplied link is an action link; clearing it falls back to Gmail where possible."""
    if link:
        task.link, task.link_type, task.link_confidence = link, "action", 1.0
    elif task.source_ref:
        fallback = gmail_fallback(task.source_ref)
        task.link, task.link_type, task.link_confidence = fallback.url, "gmail", 0.0
    else:
        task.link, task.link_type, task.link_confidence = None, None, None


def _sync_calendar(session: Session, calendar: CalendarApi | None, task: Task) -> None:
    """Write the task's Calendar event after the database change is saved. Best effort."""
    if try_sync_task_event(session, calendar, task):
        session.commit()


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(
    status: Status | None = None,
    priority: Priority | None = None,
    source: Source | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[TaskOut]:
    """Sorted by deadline (soonest first, tasks without one last)."""
    if recompute_priorities(session, user_id=user.id):
        session.commit()
    stmt = select(Task).where(Task.user_id == user.id)
    if status:
        stmt = stmt.where(Task.status == status)
    if priority:
        stmt = stmt.where(Task.priority == priority)
    if source:
        stmt = stmt.where(Task.source == source)
    stmt = stmt.order_by(Task.deadline.asc().nulls_last(), Task.created_at.desc(), Task.id.desc())
    return [TaskOut.from_task(t) for t in session.scalars(stmt.limit(limit).offset(offset))]


@router.post("/tasks", response_model=TaskOut, status_code=201)
def create_task(
    body: TaskCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    calendar: CalendarApi | None = Depends(get_calendar_api),
) -> TaskOut:
    now = datetime.now(UTC)
    deadline = to_utc(body.deadline)
    task = Task(
        user_id=user.id,
        title=body.title,
        notes=body.notes,
        source="manual",
        deadline=deadline,
        priority=body.priority or priority_for("manual", deadline, now),
        status="open",
        confidence=1.0,
    )
    _set_link(task, body.link)
    session.add(task)
    session.flush()
    sync_reminders(session, task, now=now, created=True)
    session.commit()
    _sync_calendar(session, calendar, task)
    return TaskOut.from_task(task)


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(
    task_id: int, user: User = Depends(get_current_user), session: Session = Depends(get_session)
) -> TaskOut:
    return TaskOut.from_task(owned_task(session, user, task_id))


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def update_task(
    task_id: int,
    body: TaskUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    calendar: CalendarApi | None = Depends(get_calendar_api),
) -> TaskOut:
    task = owned_task(session, user, task_id)
    sent = body.model_fields_set
    if not sent:
        raise HTTPException(422, "Nothing to update")
    for required in ("title", "priority", "status"):  # these cannot be cleared with null
        if required in sent and getattr(body, required) is None:
            raise HTTPException(422, f"{required} cannot be null")

    now = datetime.now(UTC)
    if "title" in sent:
        task.title = body.title
    if "notes" in sent:
        task.notes = body.notes
    if "deadline" in sent:
        task.deadline = to_utc(body.deadline)
        task.confidence = 1.0 if task.deadline else task.confidence
        if "priority" not in sent:
            task.priority = priority_for(_category(task), task.deadline, now)
    if "priority" in sent:
        task.priority = body.priority
    if "link" in sent:
        _set_link(task, body.link)
    if "status" in sent:
        task.status = body.status
        if body.status in ("done", "dismissed"):
            cancel_pending_reminders(session, task.id)
    task.updated_at = now
    if "deadline" in sent or "status" in sent:  # regenerate the unsent reminders
        sync_reminders(session, task, now=now)
    session.commit()
    _sync_calendar(session, calendar, task)
    return TaskOut.from_task(task)


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(
    task_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    calendar: CalendarApi | None = Depends(get_calendar_api),
) -> Response:
    task = owned_task(session, user, task_id)
    delete_event_for_removed_task(session, calendar, user.id, task.gcal_event_id)
    session.delete(task)  # reminders cascade
    session.commit()
    return Response(status_code=204)


def _close(
    task_id: int, status: str, user: User, session: Session, calendar: CalendarApi | None
) -> TaskOut:
    task = owned_task(session, user, task_id)
    close_task(session, task, status)
    session.commit()
    _sync_calendar(session, calendar, task)  # removes the event
    return TaskOut.from_task(task)


@router.post("/tasks/{task_id}/done", response_model=TaskOut)
def mark_done(
    task_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    calendar: CalendarApi | None = Depends(get_calendar_api),
) -> TaskOut:
    return _close(task_id, "done", user, session, calendar)


@router.post("/tasks/{task_id}/dismiss", response_model=TaskOut)
def dismiss(
    task_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    calendar: CalendarApi | None = Depends(get_calendar_api),
) -> TaskOut:
    return _close(task_id, "dismissed", user, session, calendar)


@router.get("/review", response_model=list[TaskOut])
def review_list(
    user: User = Depends(get_current_user), session: Session = Depends(get_session)
) -> list[TaskOut]:
    stmt = (
        select(Task)
        .where(Task.user_id == user.id, Task.status == "needs_review")
        .order_by(Task.deadline.asc().nulls_last(), Task.created_at.desc(), Task.id.desc())
    )
    return [TaskOut.from_task(t) for t in session.scalars(stmt)]


@router.post("/review/{task_id}/confirm", response_model=TaskOut)
def review_confirm(
    task_id: int,
    body: ReviewConfirm | None = None,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    calendar: CalendarApi | None = Depends(get_calendar_api),
) -> TaskOut:
    """Accept a needs-review task as it is, or with edits to title, deadline and link."""
    task = owned_task(session, user, task_id)
    if task.status != "needs_review":
        raise HTTPException(409, "Task is not waiting for review")
    now = datetime.now(UTC)
    sent = body.model_fields_set if body else set()
    if "title" in sent and body.title:
        task.title = body.title
    if "deadline" in sent and body.deadline is not None:
        task.deadline = to_utc(body.deadline)
        task.priority = priority_for(_category(task), task.deadline, now)
    if "link" in sent and body.link:
        _set_link(task, body.link)
    task.status = "open"
    task.confidence = 1.0  # confirmed by the user
    task.updated_at = now
    session.flush()
    sync_reminders(session, task, now=now, created=True)
    session.commit()
    _sync_calendar(session, calendar, task)
    return TaskOut.from_task(task)
