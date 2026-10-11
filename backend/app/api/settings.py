from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.db import get_session
from app.ingest.sync import get_or_create_settings
from app.models import User
from app.schemas.tasks import SettingsBody, SourceBody
from app.sources import load_sources

router = APIRouter(tags=["settings"])


def _out(row) -> SettingsBody:
    return SettingsBody(
        sources=[
            SourceBody(name=s.name, type=s.type, senders=list(s.senders))
            for s in load_sources(row.sources)
        ],
        watch_keywords=list(row.watch_keywords),
        block_senders=list(row.block_senders),
        require_deadline=row.require_deadline,
        digest_hour=row.digest_hour,
    )


@router.get("/settings", response_model=SettingsBody)
def get_settings_route(
    user: User = Depends(get_current_user), session: Session = Depends(get_session)
) -> SettingsBody:
    return _out(get_or_create_settings(session, user.id))


@router.put("/settings", response_model=SettingsBody)
def put_settings(
    body: SettingsBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> SettingsBody:
    """Replaces the settings. Takes effect on the next sync (they decide what the query asks for
    and how each message is sorted). Existing tasks are only changed by "Rebuild from sources"."""
    row = get_or_create_settings(session, user.id)
    row.sources = [s.model_dump() for s in body.sources]
    row.watch_keywords = body.watch_keywords
    row.block_senders = body.block_senders
    row.require_deadline = body.require_deadline
    row.digest_hour = body.digest_hour
    session.commit()
    return _out(row)
