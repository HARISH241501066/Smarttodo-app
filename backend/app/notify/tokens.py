"""Signed "Mark as done" tokens for email links.

A token is a capability: whoever holds the link can mark that one task done, once, until the
deadline. It carries the task id, a random nonce (recorded when used, so a link works once) and
the expiry (the task's deadline). Signed with SESSION_SECRET under its own salt, so it can never
be mistaken for a session cookie.
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.security import sign, unsign

SALT = "mark-done"
NO_DEADLINE_TTL = timedelta(days=7)
_SIGNATURE_MAX_AGE = 10 * 365 * 24 * 3600  # the real expiry is the signed "x" claim


class TokenError(Exception):
    """The token is missing, malformed or its signature does not match."""


class TokenExpired(TokenError):
    """The token is genuine but past its expiry (the task's deadline)."""


@dataclass(frozen=True)
class DoneToken:
    task_id: int
    nonce: str
    expires_at: datetime


def make_done_token(task_id: int, deadline: datetime | None, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    expires = deadline or (now + NO_DEADLINE_TTL)
    payload = {"t": task_id, "n": secrets.token_urlsafe(12), "x": int(expires.timestamp())}
    return sign(payload, SALT)


def read_done_token(token: str | None, now: datetime | None = None) -> DoneToken:
    now = now or datetime.now(UTC)
    payload = unsign(token, SALT, _SIGNATURE_MAX_AGE) if token else None
    if payload is None:
        raise TokenError("invalid token")
    task_id, nonce, expiry = payload.get("t"), payload.get("n"), payload.get("x")
    if not (isinstance(task_id, int) and isinstance(nonce, str) and isinstance(expiry, int)):
        raise TokenError("malformed token")
    expires_at = datetime.fromtimestamp(expiry, tz=UTC)
    if now >= expires_at:
        raise TokenExpired("token expired")
    return DoneToken(task_id, nonce, expires_at)
