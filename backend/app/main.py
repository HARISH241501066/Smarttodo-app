import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from google.auth.exceptions import GoogleAuthError

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
from app.db import check_db
from app.hardening import SecurityMiddleware
from app.scheduler.core import start_scheduler

_app_logger = logging.getLogger("app")
_app_logger.setLevel(logging.INFO)
if not _app_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
    _app_logger.addHandler(_handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run the background scheduler inside the API process when SCHEDULER_ENABLED is true.
    Jobs live in Postgres, so they survive a restart."""
    scheduler = None
    if get_settings().scheduler_enabled:
        scheduler = start_scheduler()
        app.state.scheduler = scheduler
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=True)
            _app_logger.info("scheduler stopped")


def create_app() -> FastAPI:
    """Built by a function so tests can make one under a different ENV."""
    settings = get_settings()
    prod = settings.is_production
    app = FastAPI(
        title="Smart Inbox To-Do",
        lifespan=lifespan,
        # No interactive docs or schema on a production server.
        docs_url=None if prod else "/docs",
        redoc_url=None if prod else "/redoc",
        openapi_url=None if prod else "/openapi.json",
    )
    for module in (
        auth, me, sync, tasks, folders, quarantine, settings_api, sources, triage,
        notifications, push, actions, dev,
    ):  # fmt: skip
        app.include_router(module.router)

    @app.exception_handler(ReauthRequired)
    async def _reauth_required(request: Request, exc: ReauthRequired) -> JSONResponse:
        return JSONResponse(
            {"detail": "Google access expired; please sign in again at /auth/login"},
            status_code=401,
        )

    @app.exception_handler(GoogleAuthError)
    async def _google_auth_error(request: Request, exc: GoogleAuthError) -> JSONResponse:
        return JSONResponse({"detail": "Could not reach Google; try again"}, status_code=502)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "db": "ok" if check_db() else "error"}

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
