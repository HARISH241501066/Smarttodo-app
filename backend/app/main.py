import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from google.auth.exceptions import GoogleAuthError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import (
    actions,
    auth,
    dev,
    folders,
    me,
    notifications,
    push,
    quarantine,
    sources,
    sync,
    tasks,
    triage,
)
from app.api import (
    settings as settings_api,
)
from app.auth.credentials import ReauthRequired
from app.config import get_settings
from app.hardening import SecurityMiddleware
from app.health import beat_now, health
from app.scheduler.core import acquire_scheduler_lock, start_scheduler
from app.static_site import StaticSite

_app_logger = logging.getLogger("app")
_app_logger.setLevel(logging.INFO)
if not _app_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
    _app_logger.addHandler(_handler)

API_PREFIX = "/api"


LOCK_RETRY_SECONDS = 30  # how often an instance that is waiting for the scheduler tries again


def _start_scheduler_if_free(app: FastAPI) -> bool:
    """Take the scheduler lock and start the scheduler. False when another instance holds it."""
    lock = acquire_scheduler_lock()
    if lock is None:
        return False
    app.state.scheduler_lock = lock
    app.state.scheduler = start_scheduler()
    beat_now()  # alive from the first second, so /health is green as soon as we serve
    return True


def _wait_for_the_scheduler(app: FastAPI, stop: threading.Event) -> None:
    """During a deploy the old and the new instance overlap, and the old one still holds the lock
    for a moment. The new one must not give up: it keeps trying until the old one has gone."""
    while not stop.wait(LOCK_RETRY_SECONDS):
        try:
            if _start_scheduler_if_free(app):
                _app_logger.info("the other instance has gone; this one now runs the scheduler")
                return
        except Exception as exc:  # a database hiccup must not end the retrying
            _app_logger.error("scheduler retry failed: %s", type(exc).__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run the background scheduler inside the API process when SCHEDULER_ENABLED is true.
    Jobs live in Postgres, so they survive a restart. Only the process that holds the scheduler's
    advisory lock runs it, so a second instance can never send reminders twice; one that finds the
    lock taken waits and takes over as soon as it is free."""
    app.state.scheduler = app.state.scheduler_lock = None
    stop = threading.Event()
    waiter = None
    if get_settings().scheduler_enabled and not _start_scheduler_if_free(app):
        _app_logger.warning("another instance already runs the scheduler; waiting for it to stop")
        waiter = threading.Thread(
            target=_wait_for_the_scheduler, args=(app, stop), name="scheduler-wait", daemon=True
        )
        waiter.start()
    try:
        yield
    finally:
        stop.set()
        if waiter is not None:
            waiter.join(timeout=10)
        if app.state.scheduler is not None:
            app.state.scheduler.shutdown(wait=True)
            _app_logger.info("scheduler stopped")
        if app.state.scheduler_lock is not None:
            app.state.scheduler_lock.release()


def create_app(static_dir: str | Path | None = None) -> FastAPI:
    """Built by a function so tests can make one under a different ENV or with a dashboard.

    With a dashboard (static_dir, or FRONTEND_DIST) the API moves under /api (the dashboard calls
    it there) and the pages are served from the same origin; /auth, /action and /health stay at
    the root because Google, emails and monitors use those addresses. Without one (development,
    most tests) everything is at the root, as before."""
    settings = get_settings()
    prod = settings.is_production
    site = (
        StaticSite(Path(static_dir or settings.frontend_dist))
        if (static_dir or settings.frontend_dist)
        else None
    )
    bundled = site is not None and site.ready
    app = FastAPI(
        title="Smart Inbox To-Do",
        lifespan=lifespan,
        # No interactive docs or schema on a production server.
        docs_url=None if prod else "/docs",
        redoc_url=None if prod else "/redoc",
        openapi_url=None if prod else "/openapi.json",
    )
    api_prefix = API_PREFIX if bundled else ""
    app.include_router(auth.router)
    app.include_router(actions.router)
    app.include_router(dev.router)
    if bundled:
        app.include_router(auth.router, prefix=api_prefix)  # the dashboard's login/logout calls
    for module in (
        me, sync, tasks, folders, quarantine, settings_api, sources, triage, notifications, push,
    ):  # fmt: skip
        app.include_router(module.router, prefix=api_prefix)

    @app.exception_handler(ReauthRequired)
    async def _reauth_required(request: Request, exc: ReauthRequired) -> JSONResponse:
        return JSONResponse(
            {"detail": "Google access expired; please sign in again at /auth/login"},
            status_code=401,
        )

    @app.exception_handler(GoogleAuthError)
    async def _google_auth_error(request: Request, exc: GoogleAuthError) -> JSONResponse:
        return JSONResponse({"detail": "Could not reach Google; try again"}, status_code=502)

    @app.api_route("/health", methods=["GET", "HEAD"])  # uptime monitors usually send HEAD
    def health_check() -> Response:
        status, body = health()
        return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})

    if bundled:

        @app.exception_handler(StarletteHTTPException)
        async def _not_found(request: Request, exc: StarletteHTTPException):
            """A GET that matched no route is a dashboard page. A 404 raised by a real route
            (task not found) stays a JSON error."""
            from fastapi.exception_handlers import http_exception_handler

            unmatched = request.scope.get("endpoint") is None
            if exc.status_code == 404 and unmatched and request.method in ("GET", "HEAD"):
                return site.respond(request.url.path)
            return await http_exception_handler(request, exc)

    # Added last = outermost for CORS (so preflights are answered first); the security middleware
    # sits inside it and runs the CSRF origin check and adds the headers.
    app.add_middleware(SecurityMiddleware)
    dashboard = sorted(o for o in settings.allowed_origins if o == _origin(settings.frontend_url))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=dashboard,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
    )
    return app


def _origin(url: str) -> str:
    from urllib.parse import urlparse

    parts = urlparse(url)
    return f"{parts.scheme}://{parts.netloc}"


app = create_app()
