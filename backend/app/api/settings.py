from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.db import get_session
from app.ingest.sync import get_or_create_settings
from app.models import User
from app.schemas.tasks import SettingsBody

router = APIRouter(tags=["settings"])


def _out(row) -> SettingsBody:
    return SettingsBody(
        denylist=list(row.denylist),
        allowlist=list(row.allowlist),
        digest_hour=row.digest_hour,
        always_track=list(row.always_track),
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
    """Replaces the settings. Takes effect on the next sync (the denylist feeds the query)."""
    row = get_or_create_settings(session, user.id)
    always_track = body.always_track if body.always_track is not None else list(row.always_track)
    both = sorted(set(always_track) & set(body.denylist))
    if both:
        raise HTTPException(
            422,
            f"{', '.join(both)} can't be in both 'Always turn into tasks' and "
            "'Never scan mail from'. Remove it from one of them.",
        )
    row.always_track = always_track
    row.denylist = body.denylist
    row.allowlist = body.allowlist
    row.digest_hour = body.digest_hour
    session.commit()
    return _out(row)
