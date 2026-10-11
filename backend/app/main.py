import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from google.auth.exceptions import GoogleAuthError

from app.api import auth, me, sync
from app.auth.credentials import ReauthRequired
from app.db import check_db

_app_logger = logging.getLogger("app")
_app_logger.setLevel(logging.INFO)
if not _app_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
    _app_logger.addHandler(_handler)

app = FastAPI(title="Smart Inbox To-Do")
app.include_router(auth.router)
app.include_router(me.router)
app.include_router(sync.router)


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
