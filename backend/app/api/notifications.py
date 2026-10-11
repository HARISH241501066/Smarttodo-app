"""The in-app bell: list, mark one read, mark all read. Scoped to the session user."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.db import get_session
from app.models import Notification, User
from app.schemas.channels import NotificationList, NotificationOut

router = APIRouter(tags=["notifications"])


@router.get("/notifications", response_model=NotificationList)
def list_notifications(
    unread_only: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> NotificationList:
    """Unread first, then newest first."""
    base = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        base = base.where(Notification.read.is_(False))
    items = session.scalars(
        base.order_by(
            Notification.read.asc(), Notification.created_at.desc(), Notification.id.desc()
        )
        .limit(limit)
        .offset(offset)
    ).all()
    unread = session.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user.id, Notification.read.is_(False))
    )
    return NotificationList(
        unread_count=unread, items=[NotificationOut.model_validate(n) for n in items]
    )


@router.post("/notifications/read-all")
def read_all(
    user: User = Depends(get_current_user), session: Session = Depends(get_session)
) -> dict[str, int]:
    result = session.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read.is_(False))
        .values(read=True)
    )
    session.commit()
    return {"updated": result.rowcount}


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
def mark_read(
    notification_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Notification:
    note = session.scalar(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user.id
        )
    )
    if note is None:
        raise HTTPException(404, "Notification not found")
    note.read = True
    session.commit()
    return note
