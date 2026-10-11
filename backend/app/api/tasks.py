"""Task CRUD, done/dismiss and the needs-review queue; every query is scoped to the user."""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from googleapiclient.errors import HttpError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.sync import get_gmail_api
from app.auth.deps import get_current_user
from app.db import get_session
from app.filters.deadline import priority_for
from app.filters.links import gmail_fallback
from app.ingest.gmail import GmailApi, MessageGone, RateLimited
from app.ingest.mime import parse_message
from app.models import Subtask, Task, User
from app.notify.calendar import (
    CalendarApi,
    delete_event_for_removed_task,
    get_calendar_api,
    try_sync_task_event,
)
from app.priority import complete_expired_email_tasks, recompute_priorities
from app.scheduler.reminders import sync_reminders
from app.schemas.tasks import (
    EmailOut,
    Priority,
    ReviewConfirm,
    Source,
    Status,
    SubtaskChange,
    SubtaskCreate,
    SubtaskOut,
    SubtaskReorder,
    SubtaskUpdate,
    TaskCreate,
    TaskOut,
    TaskUpdate,
    to_utc,
)
from app.task_ops import cancel_pending_reminders, close_task

router = APIRouter(tags=["tasks"])

DEFAULT_LIMIT = 200
MAX_LIMIT = 500
MAX_SUBTASKS = 100
BS = chr(92)  # the LIKE escape character
MAX_EMAIL_CHARS = 60_000


def owned_task(session: Session, user: User, task_id: int) -> Task:
    """404 (not 403) for other users' tasks, so ids can't be probed."""
    task = session.scalar(select(Task).where(Task.id == task_id, Task.user_id == user.id))
    if task is None:
        raise HTTPException(404, "Task not found")
    return task


def _not_snoozed():
    return or_(Task.snoozed_until.is_(None), Task.snoozed_until <= datetime.now(UTC))


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
    origin: Literal["manual", "email"] | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[TaskOut]:
    """Sorted by deadline (soonest first, tasks without one last)."""
    expired = complete_expired_email_tasks(session, user_id=user.id)
    if recompute_priorities(session, user_id=user.id) or expired:
        session.commit()
    stmt = select(Task).where(Task.user_id == user.id, _not_snoozed())
    if status:
        stmt = stmt.where(Task.status == status)
    if priority:
        stmt = stmt.where(Task.priority == priority)
    if source:
        stmt = stmt.where(Task.source == source)
    if origin == "manual":
        stmt = stmt.where(Task.source == "manual")
    elif origin == "email":
        stmt = stmt.where(Task.source != "manual")
    stmt = stmt.order_by(Task.deadline.asc().nulls_last(), Task.created_at.desc(), Task.id.desc())
    return [TaskOut.from_task(t) for t in session.scalars(stmt.limit(limit).offset(offset))]


@router.get("/deadlines", response_model=list[TaskOut])
def deadlines(
    include_done: bool = False,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[TaskOut]:
    """Every open task, manual and email, soonest deadline first, no-deadline last. Overdue tasks
    are included (they sort first). `include_done` adds completed tasks."""
    expired = complete_expired_email_tasks(session, user_id=user.id)
    if recompute_priorities(session, user_id=user.id) or expired:
        session.commit()
    statuses = ("open", "done") if include_done else ("open",)
    stmt = (
        select(Task)
        .where(Task.user_id == user.id, Task.status.in_(statuses), _not_snoozed())
        .order_by(Task.deadline.asc().nulls_last(), Task.created_at.desc(), Task.id.desc())
    )
    return [TaskOut.from_task(t) for t in session.scalars(stmt)]


@router.get("/search", response_model=list[TaskOut])
def search(
    q: str = Query(..., min_length=1, max_length=100),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[TaskOut]:
    """Tasks (open or done, both origins) whose title, notes, tag or source name contain `q`."""
    needle = " ".join(q.split())
    if not needle:
        raise HTTPException(422, "Type something to search for")
    esc = needle.replace(BS, BS + BS).replace("%", BS + "%").replace("_", BS + "_")
    like = f"%{esc}%"
    match = or_(
        *(
            col.ilike(like, escape=BS)
            for col in (Task.title, Task.notes, Task.tag, Task.source_name)
        )
    )
    stmt = (
        select(Task)
        .where(Task.user_id == user.id, Task.status.in_(("open", "done")), _not_snoozed(), match)
        .order_by(Task.status.desc(), Task.deadline.asc().nulls_last(), Task.id.desc())
        .limit(50)
    )
    return [TaskOut.from_task(x) for x in session.scalars(stmt)]


@router.get("/tags", response_model=list[str])
def tags(
    user: User = Depends(get_current_user), session: Session = Depends(get_session)
) -> list[str]:
    """Tags already used, for suggestions in the task form."""
    rows = session.scalars(
        select(Task.tag)
        .where(Task.user_id == user.id, Task.tag.is_not(None))
        .group_by(Task.tag)
        .order_by(func.count().desc(), Task.tag)
    )
    return list(rows)


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
        user_confirmed=True,
        tag=body.tag,
        reminders_enabled=body.reminders_enabled,
        deadline=deadline,
        priority=body.priority or priority_for("manual", deadline, now),
        status="open",
        confidence=1.0,
    )
    _set_link(task, body.link)
    task.subtasks = [Subtask(title=title, position=i) for i, title in enumerate(body.subtasks)]
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


@router.get("/tasks/{task_id}/email", response_model=EmailOut)
def task_email(
    task_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    gmail: GmailApi = Depends(get_gmail_api),
) -> EmailOut:
    """The full email behind an email task, read from Gmail right now. Nothing is stored: the app
    never keeps message bodies, so this fetches it again each time it is asked for."""
    task = owned_task(session, user, task_id)
    if task.source == "manual" or not task.source_ref:
        raise HTTPException(404, "This task did not come from an email")
    try:
        parsed = parse_message(gmail.get_message(task.source_ref), gmail.get_attachment)
    except MessageGone as exc:
        raise HTTPException(410, "That email no longer exists in Gmail") from exc
    except RateLimited as exc:
        raise HTTPException(429, "Gmail rate limit reached; try again in a minute") from exc
    except HttpError as exc:
        raise HTTPException(502, "Gmail API error") from exc
    body = parsed.body_text or ""
    return EmailOut(
        subject=parsed.subject,
        sender=parsed.sender,
        received_at=parsed.received_at,
        body_text=body[:MAX_EMAIL_CHARS],
        truncated=len(body) > MAX_EMAIL_CHARS,
        gmail_url=gmail_fallback(task.source_ref).url,
    )


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
    task.user_confirmed = True  # the user has touched it: a rebuild must leave it alone
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
    if "snoozed_until" in sent:
        task.snoozed_until = to_utc(body.snoozed_until)
    if "tag" in sent:
        task.tag = body.tag
    if "reminders_enabled" in sent:
        if body.reminders_enabled is None:
            raise HTTPException(422, "reminders_enabled cannot be null")
        task.reminders_enabled = body.reminders_enabled
    if "status" in sent:
        task.status = body.status
        if body.status in ("done", "dismissed"):
            cancel_pending_reminders(session, task.id)
    task.updated_at = now
    if {"deadline", "status", "reminders_enabled"} & sent:  # regenerate the unsent reminders
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
    task.user_confirmed = True
    task.updated_at = now
    session.flush()
    sync_reminders(session, task, now=now, created=True)
    session.commit()
    _sync_calendar(session, calendar, task)
    return TaskOut.from_task(task)


# ---------- subtasks (on manual and email tasks alike) ----------


def _change(session: Session, task: Task, subtask: Subtask | None) -> SubtaskChange:
    session.commit()
    session.refresh(task)
    done = bool(task.subtasks) and all(s.done for s in task.subtasks)
    return SubtaskChange(
        subtask=SubtaskOut.model_validate(subtask) if subtask is not None else None,
        all_subtasks_done=done,
        task=TaskOut.from_task(task),
    )


def _owned_subtask(task: Task, sub_id: int) -> Subtask:
    sub = next((s for s in task.subtasks if s.id == sub_id), None)
    if sub is None:
        raise HTTPException(404, "Subtask not found")
    return sub


@router.post("/tasks/{task_id}/subtasks", response_model=SubtaskChange, status_code=201)
def add_subtask(
    task_id: int,
    body: SubtaskCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> SubtaskChange:
    task = owned_task(session, user, task_id)
    if len(task.subtasks) >= MAX_SUBTASKS:
        raise HTTPException(422, f"At most {MAX_SUBTASKS} subtasks per task")
    position = max((s.position for s in task.subtasks), default=-1) + 1
    sub = Subtask(title=body.title, position=position)
    task.subtasks.append(sub)
    session.flush()
    return _change(session, task, sub)


@router.patch("/tasks/{task_id}/subtasks/reorder", response_model=SubtaskChange)
def reorder_subtasks(
    task_id: int,
    body: SubtaskReorder,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> SubtaskChange:
    """`ids` must be exactly the task's subtask ids, in the new order."""
    task = owned_task(session, user, task_id)
    by_id = {s.id: s for s in task.subtasks}
    if sorted(body.ids) != sorted(by_id):
        raise HTTPException(422, "ids must list every subtask of the task exactly once")
    for position, sub_id in enumerate(body.ids):
        by_id[sub_id].position = position
    return _change(session, task, None)


@router.patch("/tasks/{task_id}/subtasks/{sub_id}", response_model=SubtaskChange)
def update_subtask(
    task_id: int,
    sub_id: int,
    body: SubtaskUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> SubtaskChange:
    """Rename and/or tick. Ticking the last one sets all_subtasks_done; never completes the task."""
    task = owned_task(session, user, task_id)
    sub = _owned_subtask(task, sub_id)
    sent = body.model_fields_set
    if not sent:
        raise HTTPException(422, "Nothing to update")
    if "title" in sent:
        if body.title is None:
            raise HTTPException(422, "title cannot be null")
        sub.title = body.title
    if "done" in sent:
        if body.done is None:
            raise HTTPException(422, "done cannot be null")
        sub.done = body.done
    return _change(session, task, sub)


@router.delete("/tasks/{task_id}/subtasks/{sub_id}", response_model=SubtaskChange)
def delete_subtask(
    task_id: int,
    sub_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> SubtaskChange:
    task = owned_task(session, user, task_id)
    task.subtasks.remove(_owned_subtask(task, sub_id))
    return _change(session, task, None)
