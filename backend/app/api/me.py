import logging

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.auth import google
from app.auth.credentials import forget_access_token
from app.auth.deps import SESSION_COOKIE, get_current_user
from app.db import get_session
from app.models import User
from app.security import decrypt_token

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return {"email": user.email, "needs_reauth": user.needs_reauth}


@router.delete("/me")
def delete_me(
    user: User = Depends(get_current_user), session: Session = Depends(get_session)
) -> JSONResponse:
    """Revoke the Google grant (best effort) and delete the user and all their data.

    Revocation failing never blocks deletion: the user's right to erase their data
    comes first, and the response reports whether Google confirmed the revoke.
    """
    revoked = False
    if user.encrypted_refresh_token:
        try:
            revoked = google.revoke_token(decrypt_token(user.encrypted_refresh_token))
        except InvalidToken:
            logger.warning("user %s token undecryptable; skipping revoke", user.id)
    user_id = user.id
    # Child tables are ON DELETE CASCADE (tasks -> reminders as well).
    session.execute(delete(User).where(User.id == user_id))
    session.commit()
    forget_access_token(user_id)
    logger.info("user %s deleted (google_revoked=%s)", user_id, revoked)
    response = JSONResponse({"deleted": True, "google_revoked": revoked})
    response.delete_cookie(SESSION_COOKIE)
    return response
