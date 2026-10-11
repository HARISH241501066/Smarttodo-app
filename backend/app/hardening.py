"""Request-level safety: security headers, the CSRF origin check, and rate limits.

* Every response gets nosniff, no-referrer, frame-ancestors 'none' and a locked-down CSP (the
  "Mark as done" page sets its own, slightly looser one for its inline style).
* Every POST/PUT/PATCH/DELETE that carries an Origin (or Referer) from somewhere other than the
  dashboard or this server's public URL is refused with 403. Browsers always send Origin on such
  requests, so a hostile page cannot ride on a signed-in session; requests with neither header
  come from scripts and tools, not from a browser, and are not a CSRF risk.
* Rate limits are per client address in memory (one API process). They slow down guessing and
  abuse; they are not a substitute for a gateway in front of a big deployment.
"""

import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"


def _origin_of(url: str) -> str | None:
    parts = urlparse(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else None


class SecurityMiddleware:
    """Pure ASGI middleware: the CSRF origin check, then headers on whatever comes back."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        settings = get_settings()
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        if scope["method"] in UNSAFE_METHODS:
            origin = headers.get("origin") or (
                _origin_of(headers["referer"]) if headers.get("referer") else None
            )
            if origin is not None and origin not in settings.allowed_origins:
                response = JSONResponse({"detail": "Cross-site request refused"}, status_code=403)
                await self._send_with_headers(response, scope, receive, send, settings)
                return

        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                present = {k.lower() for k, _ in message["headers"]}
                for key, value in self._headers(settings).items():
                    if key.lower().encode() not in present:
                        message["headers"].append((key.lower().encode(), value.encode()))
            await send(message)

        await self.app(scope, receive, wrapped)

    @staticmethod
    def _headers(settings) -> dict[str, str]:
        out = {
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": API_CSP,
            "Cross-Origin-Resource-Policy": "same-site",
        }
        if settings.is_production:
            out["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return out

    async def _send_with_headers(self, response, scope, receive, send, settings) -> None:
        for key, value in self._headers(settings).items():
            response.headers.setdefault(key, value)
        await response(scope, receive, send)


# ---------------------------------------------------------------------------------------------
# Rate limits
# ---------------------------------------------------------------------------------------------

# name -> (allowed requests, per this many seconds)
RATE_LIMITS: dict[str, tuple[int, int]] = {
    "auth": (30, 60),
    "mark-done": (20, 60),
    "push-subscribe": (20, 60),
    "sync": (10, 60),
    "rebuild": (5, 600),
}

_hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def reset_rate_limits() -> None:
    with _lock:
        _hits.clear()


def rate_limit(name: str):
    """A FastAPI dependency: 429 once a client has used up `name`'s allowance."""

    def check(request: Request) -> None:
        allowed, window = RATE_LIMITS[name]
        key = (name, request.client.host if request.client else "unknown")
        now = time.monotonic()
        with _lock:
            hits = _hits[key]
            while hits and now - hits[0] > window:
                hits.popleft()
            if len(hits) >= allowed:
                retry = max(1, int(window - (now - hits[0])))
                raise HTTPException(
                    429, "Too many requests; slow down", headers={"Retry-After": str(retry)}
                )
            hits.append(now)

    return check
