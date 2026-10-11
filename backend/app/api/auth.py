import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import google
from app.auth.deps import SESSION_COOKIE, SESSION_SALT
from app.config import get_settings
from app.db import get_session
from app.hardening import rate_limit
from app.models import User
from app.security import encrypt_token, sign, unsign

logger = logging.getLogger(__name__)
router = APIRouter()

STATE_COOKIE = "oauth_state"
STATE_SALT = "oauth-state"
STATE_MAX_AGE = 600


def _cookie_kwargs() -> dict:
    return {"httponly": True, "samesite": "lax", "secure": get_settings().secure_cookies}


@router.get("/auth/login", dependencies=[Depends(rate_limit("auth"))])
def login() -> RedirectResponse:
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)  # PKCE code verifier (86 chars)
    response = RedirectResponse(google.build_login_url(state, verifier), status_code=302)
    response.set_cookie(
        STATE_COOKIE,
        sign({"state": state, "verifier": verifier}, STATE_SALT),
        max_age=STATE_MAX_AGE,
        **_cookie_kwargs(),
    )
    return response


@router.get("/auth/callback", dependencies=[Depends(rate_limit("auth"))])
def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    session: Session = Depends(get_session),
) -> RedirectResponse:
    if error:
        raise HTTPException(400, "Google sign-in was cancelled or denied")
    cookie = request.cookies.get(STATE_COOKIE)
    saved = unsign(cookie, STATE_SALT, STATE_MAX_AGE) if cookie else None
    if (
        not state
        or not saved
        or not secrets.compare_digest(state.encode(), str(saved.get("state", "")).encode())
    ):
        raise HTTPException(400, "Missing or invalid OAuth state")
    if not code:
        raise HTTPException(400, "Missing authorization code")

    try:
        login = google.exchange_code(code, saved["verifier"])
    except google.LoginError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.warning("google code exchange failed: %s", type(exc).__name__)
        raise HTTPException(400, "Google rejected the sign-in; please try again") from exc

    missing = google.REQUIRED_SCOPES - login.granted_scopes
    if missing:
        raise HTTPException(400, "Gmail and Calendar access are both required; please allow them")

    user = session.scalar(select(User).where(User.email == login.email))
    if user is None:
        if not login.refresh_token:
            raise HTTPException(400, "Google did not return a refresh token; please try again")
        user = User(email=login.email)
        session.add(user)
    if login.refresh_token:
        user.encrypted_refresh_token = encrypt_token(login.refresh_token)
    elif not user.encrypted_refresh_token:
        raise HTTPException(400, "Google did not return a refresh token; please try again")
    user.needs_reauth = False
    session.commit()
    logger.info("user %s signed in", user.id)

    s = get_settings()
    response = RedirectResponse(s.post_login_redirect, status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        sign({"uid": user.id}, SESSION_SALT),
        max_age=s.session_max_age_seconds,
        **_cookie_kwargs(),
    )
    response.delete_cookie(STATE_COOKIE)
    return response


@router.post("/auth/logout", dependencies=[Depends(rate_limit("auth"))])
def logout() -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE)
    return response
