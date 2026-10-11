"""Loads Google credentials for background jobs, refreshing expired access tokens.

Only the refresh token is persisted (encrypted). Access tokens live in a small
in-process cache so repeated calls don't hit Google's token endpoint every time.
"""

import logging
from datetime import datetime

from cryptography.fernet import InvalidToken
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from sqlalchemy.orm import Session

from app.auth.google import TOKEN_URI
from app.config import get_settings
from app.models import User
from app.security import decrypt_token

logger = logging.getLogger(__name__)

# user id -> (access token, naive-UTC expiry as used by google-auth)
_access_cache: dict[int, tuple[str, datetime | None]] = {}


class ReauthRequired(Exception):
    """The stored refresh token is unusable; the user must sign in again."""


def forget_access_token(user_id: int) -> None:
    _access_cache.pop(user_id, None)


def _mark_reauth(user: User, session: Session) -> None:
    forget_access_token(user.id)
    user.needs_reauth = True
    session.commit()
    logger.warning("user %s needs re-authentication", user.id)


def load_credentials(user: User, session: Session, request=None) -> Credentials:
    """Return credentials with a valid access token, refreshing if it is missing or expired.

    `request` is a google-auth transport request callable (injectable for tests).
    Raises ReauthRequired (and sets user.needs_reauth) if Google rejects the refresh
    token or it cannot be decrypted.
    """
    if not user.encrypted_refresh_token:
        _mark_reauth(user, session)
        raise ReauthRequired("no refresh token stored")
    try:
        refresh_token = decrypt_token(user.encrypted_refresh_token)
    except InvalidToken as exc:
        _mark_reauth(user, session)
        raise ReauthRequired("stored refresh token cannot be decrypted") from exc

    s = get_settings()
    cached_token, cached_expiry = _access_cache.get(user.id, (None, None))
    creds = Credentials(
        token=cached_token,
        expiry=cached_expiry,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=s.google_client_id,
        client_secret=s.google_client_secret.get_secret_value(),
    )
    if not creds.valid:
        try:
            creds.refresh(request or Request())
        except RefreshError as exc:
            if any(code in str(exc) for code in ("invalid_grant", "unauthorized_client")):
                _mark_reauth(user, session)
                raise ReauthRequired("Google rejected the refresh token") from exc
            raise
        _access_cache[user.id] = (creds.token, creds.expiry)
        if user.needs_reauth:
            user.needs_reauth = False
            session.commit()
    return creds
