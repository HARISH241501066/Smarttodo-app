import logging
from typing import Literal

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth import google
from app.auth.credentials import forget_access_token
from app.auth.deps import SESSION_COOKIE, get_current_user
from app.db import get_session
from app.models import CalendarOrphan, Task, User
from app.notify.calendar import calendar_for
from app.security import decrypt_token

logger = logging.getLogger(__name__)
router = APIRouter()


class DeleteAccount(BaseModel):
    """The user types DELETE to confirm; nothing is deleted without it."""

    confirm: Literal["DELETE"]


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return {"email": user.email, "needs_reauth": user.needs_reauth}


def _delete_calendar_events(session: Session, user: User) -> int:
    """Remove this user's events from their Google Calendar (best effort); returns how many."""
    event_ids = [
        e
        for e in session.scalars(
            select(Task.gcal_event_id).where(
                Task.user_id == user.id, Task.gcal_event_id.is_not(None)
            )
        )
    ]
    event_ids += list(
        session.scalars(select(CalendarOrphan.event_id).where(CalendarOrphan.user_id == user.id))
    )
    api = calendar_for(user, session)
    if api is None:
        return 0
    deleted = 0
    for event_id in event_ids:
        try:
            api.delete_event(event_id)
            deleted += 1
        except Exception as exc:  # a Google problem never blocks erasing the data
            logger.warning("calendar delete failed (user %s): %s", user.id, type(exc).__name__)
    return deleted


@router.post("/me/delete")
def delete_me(
    body: DeleteAccount,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> JSONResponse:
    """Delete the user and all their data: Calendar events, the Google grant (best effort), then
    every row (tasks, subtasks, folders, pins, reminders, notifications, sources and settings,
    push subscriptions, processed messages, quarantine, tokens).

    Google failing never blocks deletion: the right to erase comes first, and the response says
    what Google confirmed.
    """
    events = _delete_calendar_events(session, user)
    revoked = False
    if user.encrypted_refresh_token:
        try:
            revoked = google.revoke_token(decrypt_token(user.encrypted_refresh_token))
        except InvalidToken:
            logger.warning("user %s token undecryptable; skipping revoke", user.id)
    user_id = user.id
    # Every child table is ON DELETE CASCADE from users (tasks -> reminders, subtasks, pins too).
    session.execute(delete(User).where(User.id == user_id))
    session.commit()
    forget_access_token(user_id)
    logger.info("user %s deleted (google_revoked=%s calendar_events=%d)", user_id, revoked, events)
    response = JSONResponse(
        {"deleted": True, "google_revoked": revoked, "calendar_events_deleted": events}
    )
    response.delete_cookie(SESSION_COOKIE)
    return response
