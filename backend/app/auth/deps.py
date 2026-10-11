from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import User
from app.security import unsign

SESSION_COOKIE = "session"
SESSION_SALT = "session"


def get_current_user(request: Request, session: Session = Depends(get_session)) -> User:
    cookie = request.cookies.get(SESSION_COOKIE)
    data = unsign(cookie, SESSION_SALT, get_settings().session_max_age_seconds) if cookie else None
    user = session.get(User, data["uid"]) if data and "uid" in data else None
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user
