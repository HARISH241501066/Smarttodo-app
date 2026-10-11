import logging
import os
from dataclasses import dataclass

import httpx
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow

from app.config import get_settings

# Google may return the granted scopes in a different form/order than requested
# (e.g. "email" -> ".../userinfo.email"); we check scopes ourselves below.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

logger = logging.getLogger(__name__)

AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"

GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
CALENDAR_EVENTS = "https://www.googleapis.com/auth/calendar.events"
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    GMAIL_READONLY,
    CALENDAR_EVENTS,
]
REQUIRED_SCOPES = frozenset({GMAIL_READONLY, CALENDAR_EVENTS})


class LoginError(Exception):
    """The Google sign-in could not be completed; message is safe to show the user."""


@dataclass(frozen=True)
class GoogleLogin:
    email: str
    refresh_token: str | None
    granted_scopes: frozenset[str]


def _flow(code_verifier: str) -> Flow:
    s = get_settings()
    config = {
        "web": {
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret.get_secret_value(),
            "auth_uri": AUTH_URI,
            "token_uri": TOKEN_URI,
            "redirect_uris": [s.google_redirect_uri],
        }
    }
    return Flow.from_client_config(
        config, scopes=SCOPES, redirect_uri=s.google_redirect_uri, code_verifier=code_verifier
    )


def build_login_url(state: str, code_verifier: str) -> str:
    url, _ = _flow(code_verifier).authorization_url(
        access_type="offline", prompt="consent", state=state
    )
    return url


def exchange_code(code: str, code_verifier: str) -> GoogleLogin:
    """Exchange the authorization code and read the verified email from the ID token."""
    flow = _flow(code_verifier)
    flow.fetch_token(code=code)
    creds = flow.credentials
    info = id_token.verify_oauth2_token(creds.id_token, Request(), get_settings().google_client_id)
    if not info.get("email") or not info.get("email_verified"):
        raise LoginError("Your Google email address is not verified.")
    return GoogleLogin(
        email=info["email"].lower(),
        refresh_token=creds.refresh_token,
        granted_scopes=frozenset(creds.granted_scopes or creds.scopes or []),
    )


def revoke_token(token: str) -> bool:
    """Best-effort revoke at Google. True only if Google confirmed (HTTP 200)."""
    try:
        resp = httpx.post(REVOKE_URI, data={"token": token}, timeout=10)
    except httpx.HTTPError as exc:
        logger.warning("google token revoke failed: %s", type(exc).__name__)
        return False
    return resp.status_code == 200
