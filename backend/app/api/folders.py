"""Smart folders. Every query is scoped to the session user; other users' ids answer 404."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.api.tasks import owned_task
from app.auth.deps import get_current_user
from app.db import get_session
from app.folders import canonical_rules, contents, counts, grouped
from app.models import Folder, FolderPin, Task, User
from app.priority import complete_expired_email_tasks
from app.schemas.folders import (
    MAX_FOLDERS,
    FolderBody,
    FolderGroup,
    FolderOut,
    FolderPatch,
    FolderPreview,
    FolderReorder,
    FolderRules,
    FolderTasksOut,
)
from app.schemas.tasks import Priority, TaskOut

router = APIRouter(tags=["folders"])


def _owned(session: Session, user: User, folder_id: int) -> Folder:
    folder = session.scalar(select(Folder).where(Folder.id == folder_id, Folder.user_id == user.id))
    if folder is None:
        raise HTTPException(404, "Folder not found")
    return folder


def _rules(session: Session, user: User, rules: FolderRules) -> FolderRules:
    try:
        return canonical_rules(session, user.id, rules)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _out(session: Session, user: User, folder: Folder, now: datetime) -> FolderOut:
    open_count, overdue = counts(session, user.id, folder, now)
    return FolderOut(
        id=folder.id,
        name=folder.name,
        emoji=folder.emoji,
        color=folder.color,
        position=folder.position,
        rules=FolderRules.model_validate(folder.rules),
        show_completed=folder.show_completed,
        open_count=open_count,
        has_overdue=overdue,
    )


def _sweep(session: Session, user: User) -> None:
    """Email tasks past their deadline are completed first, so counts and lists agree."""
    if complete_expired_email_tasks(session, user_id=user.id):
        session.commit()


@router.get("/folders", response_model=list[FolderOut])
def list_folders(
    task_id: int | None = Query(None, description="also say whether this task is pinned"),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[FolderOut]:
    _sweep(session, user)
    now = datetime.now(UTC)
    rows = session.scalars(
        select(Folder).where(Folder.user_id == user.id).order_by(Folder.position, Folder.id)
    ).all()
    out = [_out(session, user, f, now) for f in rows]
    if task_id is not None:
        pinned = set(
            session.scalars(
                select(FolderPin.folder_id).where(
                    FolderPin.task_id == task_id, FolderPin.folder_id.in_([f.id for f in rows])
                )
            )
        )
        for item in out:
            item.pinned = item.id in pinned
    return out


@router.post("/folders", response_model=FolderOut, status_code=201)
def create_folder(
    body: FolderBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FolderOut:
    existing = session.scalar(
        select(func.count()).select_from(Folder).where(Folder.user_id == user.id)
    )
    if (existing or 0) >= MAX_FOLDERS:
        raise HTTPException(422, f"At most {MAX_FOLDERS} folders")
    rules = _rules(session, user, body.rules)
    position = session.scalar(
        select(func.coalesce(func.max(Folder.position), -1)).where(Folder.user_id == user.id)
    )
    folder = Folder(
        user_id=user.id,
        name=body.name,
        emoji=body.emoji,
        color=body.color,
        position=position + 1,
        rules=rules.model_dump(),
        show_completed=body.show_completed,
    )
    session.add(folder)
    session.commit()
    return _out(session, user, folder, datetime.now(UTC))


@router.patch("/folders/reorder", response_model=list[FolderOut])
def reorder_folders(
    body: FolderReorder,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[FolderOut]:
    """`ids` must be exactly the user's folders, in the new order."""
    folders = {f.id: f for f in session.scalars(select(Folder).where(Folder.user_id == user.id))}
    if sorted(body.ids) != sorted(folders):
        raise HTTPException(422, "ids must list every folder exactly once")
    for position, folder_id in enumerate(body.ids):
        folders[folder_id].position = position
    session.commit()
    now = datetime.now(UTC)
    return [_out(session, user, folders[i], now) for i in body.ids]


@router.get("/folders/preview", response_model=FolderPreview)
def preview(
    sources: Annotated[list[str], Query()] = [],  # noqa: B006
    tags: Annotated[list[str], Query()] = [],  # noqa: B006
    priorities: Annotated[list[Priority], Query()] = [],  # noqa: B006
    title_contains: str | None = None,
    show_completed: bool = False,
    folder_id: int | None = Query(None, description="count this folder's pinned tasks too"),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FolderPreview:
    """How many tasks a rules object would hold right now, for the folder editor."""
    try:
        rules = FolderRules(
            sources=sources, tags=tags, priorities=priorities, title_contains=title_contains
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    rules = _rules(session, user, rules)
    if folder_id is not None:
        _owned(session, user, folder_id)
    _sweep(session, user)
    stmt = contents(
        user.id, rules, folder_id=folder_id, show_completed=show_completed, now=datetime.now(UTC)
    ).subquery()
    return FolderPreview(count=session.scalar(select(func.count()).select_from(stmt)) or 0)


@router.patch("/folders/{folder_id}", response_model=FolderOut)
def update_folder(
    folder_id: int,
    body: FolderPatch,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FolderOut:
    folder = _owned(session, user, folder_id)
    sent = body.model_fields_set
    if not sent:
        raise HTTPException(422, "Nothing to update")
    for required in ("name", "color", "rules", "show_completed", "emoji"):
        if required in sent and getattr(body, required) is None:
            raise HTTPException(422, f"{required} cannot be null")
    if "name" in sent:
        folder.name = body.name
    if "emoji" in sent:
        folder.emoji = body.emoji.strip()
    if "color" in sent:
        folder.color = body.color
    if "rules" in sent:
        folder.rules = _rules(session, user, body.rules).model_dump()
    if "show_completed" in sent:
        folder.show_completed = body.show_completed
    session.commit()
    return _out(session, user, folder, datetime.now(UTC))


@router.delete("/folders/{folder_id}", status_code=204)
def delete_folder(
    folder_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Response:
    """Removes the folder and its pins. Tasks are never touched."""
    session.delete(_owned(session, user, folder_id))
    session.commit()
    return Response(status_code=204)


@router.get("/folders/{folder_id}/tasks", response_model=FolderTasksOut)
def folder_tasks(
    folder_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FolderTasksOut:
    folder = _owned(session, user, folder_id)
    _sweep(session, user)
    now = datetime.now(UTC)
    rules = FolderRules.model_validate(folder.rules)
    tasks = list(
        session.scalars(
            contents(
                user.id, rules, folder_id=folder.id, show_completed=folder.show_completed, now=now
            )
        )
    )
    ids = {t.id for t in tasks}
    pinned = set(
        session.scalars(
            select(FolderPin.task_id).where(
                FolderPin.folder_id == folder.id, FolderPin.task_id.in_(ids)
            )
        )
    )
    by_rule: set[int] = set()
    if not rules.empty:
        by_rule = set(
            session.scalars(
                contents(
                    user.id, rules, folder_id=None, show_completed=folder.show_completed, now=now
                ).with_only_columns(Task.id)
            )
        )
    return FolderTasksOut(
        groups=[
            FolderGroup(key=key, label=label, tasks=[TaskOut.from_task(t) for t in items])
            for key, label, items in grouped(tasks, now)
        ],
        pinned_ids=sorted(pinned),
        rule_ids=sorted(by_rule & ids),
    )


@router.post("/folders/{folder_id}/pins/{task_id}", status_code=204)
def pin_task(
    folder_id: int,
    task_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Response:
    folder = _owned(session, user, folder_id)
    task = owned_task(session, user, task_id)
    session.execute(
        insert(FolderPin)
        .values(folder_id=folder.id, task_id=task.id)
        .on_conflict_do_nothing(index_elements=["folder_id", "task_id"])
    )
    session.commit()
    return Response(status_code=204)


@router.delete("/folders/{folder_id}/pins/{task_id}", status_code=204)
def unpin_task(
    folder_id: int,
    task_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Response:
    folder = _owned(session, user, folder_id)
    pin = session.get(FolderPin, (folder.id, task_id))
    if pin is not None:
        session.delete(pin)
        session.commit()
    return Response(status_code=204)
