"""Rebuild from sources: preview what would go, start the rebuild, and poll its status."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session, sessionmaker

from app.api.sync import get_gmail_api
from app.auth.deps import get_current_user
from app.db import get_session
from app.ingest.gmail import GmailApi
from app.ingest.sync import SyncInProgress
from app.models import User
from app.notify.calendar import CalendarApi, get_calendar_api
from app.rebuild import get_status, preview, start_rebuild

router = APIRouter(tags=["sources"])


@router.get("/sources/rebuild/preview")
def rebuild_preview(
    user: User = Depends(get_current_user), session: Session = Depends(get_session)
) -> dict:
    """What a rebuild would look at and remove, from stored data only (nothing is fetched)."""
    return asdict(preview(session, user))


@router.post("/sources/rebuild", status_code=202)
def rebuild(
    response: Response,
    wait: bool = False,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    gmail: GmailApi = Depends(get_gmail_api),
    calendar: CalendarApi | None = Depends(get_calendar_api),
) -> dict:
    """Start a rebuild in the background (`wait=true` runs it before answering, for scripts and
    tests). Manual, confirmed and finished tasks are never touched."""
    try:
        # The background run needs its own session, on the same database as this request.
        factory = sessionmaker(session.get_bind(), expire_on_commit=False)
        status = start_rebuild(user.id, gmail, calendar, factory, wait=wait)
    except SyncInProgress as exc:
        raise HTTPException(409, "A rebuild is already running") from exc
    if wait:
        response.status_code = 200
    return status


@router.get("/sources/rebuild/status")
def rebuild_status(user: User = Depends(get_current_user)) -> dict:
    return get_status(user.id)
