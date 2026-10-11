import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from google.auth.exceptions import GoogleAuthError

from app.api import (
    actions,
    auth,
    dev,
    me,
    notifications,
    push,
    quarantine,
    settings,
    sources,
    sync,
    tasks,
    triage,
)
from app.auth.credentials import ReauthRequired
from app.config import get_settings
from app.db import check_db
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


app = FastAPI(title="Smart Inbox To-Do", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(me.router)
app.include_router(sync.router)
app.include_router(tasks.router)
app.include_router(quarantine.router)
app.include_router(settings.router)
app.include_router(sources.router)
app.include_router(triage.router)
app.include_router(notifications.router)
app.include_router(push.router)
app.include_router(actions.router)
app.include_router(dev.router)


@app.exception_handler(ReauthRequired)
async def _reauth_required(request: Request, exc: ReauthRequired) -> JSONResponse:
    return JSONResponse(
        {"detail": "Google access expired; please sign in again at /auth/login"}, status_code=401
    )


@app.exception_handler(GoogleAuthError)
async def _google_auth_error(request: Request, exc: GoogleAuthError) -> JSONResponse:
    return JSONResponse({"detail": "Could not reach Google; try again"}, status_code=502)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "db": "ok" if check_db() else "error"}
