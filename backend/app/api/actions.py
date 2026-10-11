"""The "Mark as done" link from reminder emails.

GET only ever shows a confirmation page; it never changes anything, so link scanners and
prefetchers are harmless. The page's button POSTs, and only the POST marks the task done.
The token is signed, expires at the task's deadline, and each link works once.
"""

from dataclasses import dataclass
from html import escape

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Task, UsedActionToken, User
from app.notify.calendar import calendar_for, try_sync_task_event
from app.notify.tokens import DoneToken, TokenError, TokenExpired, read_done_token
from app.task_ops import close_task

router = APIRouter(tags=["actions"], include_in_schema=True)

_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",  # the token is in the URL
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'",
}


def _page(heading: str, message: str, status: int = 200, button_url: str | None = None):
    form = ""
    if button_url:
        form = (
            f'<form method="post" action="{escape(button_url, quote=True)}">'
            '<button type="submit">Mark as done</button></form>'
        )
    body = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex"><title>Smart To-Do</title><style>'
        "body{font-family:Arial,Helvetica,sans-serif;max-width:480px;margin:12vh auto;"
        "padding:0 20px;text-align:center;color:#202124}"
        "button{background:#188038;color:#fff;border:0;border-radius:8px;font-size:20px;"
        "font-weight:bold;padding:16px 40px;cursor:pointer;margin-top:20px}"
        f"</style></head><body><h1>{escape(heading)}</h1><p>{escape(message)}</p>{form}</body></html>"
    )
    return HTMLResponse(body, status_code=status, headers=_HEADERS)


@dataclass
class Resolution:
    state: str  # invalid | expired | gone | done | used | not_open | ready
    task: Task | None = None
    token: DoneToken | None = None


def _resolve(session: Session, raw_token: str | None) -> Resolution:
    try:
        token = read_done_token(raw_token)
    except TokenExpired:
        return Resolution("expired")
    except TokenError:
        return Resolution("invalid")
    task = session.get(Task, token.task_id)
    if task is None:
        return Resolution("gone", token=token)
    if task.status == "done":
        return Resolution("done", task, token)
    if session.get(UsedActionToken, token.nonce) is not None:
        return Resolution("used", task, token)
    if task.status != "open":
        return Resolution("not_open", task, token)
    return Resolution("ready", task, token)


def _message_page(res: Resolution):
    if res.state == "invalid":
        return _page(
            "Link not valid", "This link is not valid. Open the app to manage the task.", 400
        )
    if res.state == "expired":
        return _page("Link expired", "This link expired when the task's deadline passed.", 410)
    if res.state == "gone":
        return _page("Task not found", "This task no longer exists.", 404)
    if res.state == "done":
        return _page("Already done", f"“{res.task.title}” is already marked as done.")
    if res.state == "used":
        return _page(
            "Link already used", "This link was already used. Open the app to change the task.", 410
        )
    return _page(
        "Not open", "This task is no longer open, so it can't be marked as done here.", 409
    )


@router.get("/action/done", response_class=HTMLResponse)
def confirm_page(t: str | None = None, session: Session = Depends(get_session)):
    """Shows what will happen. Read-only: never marks anything done."""
    res = _resolve(session, t)
    if res.state != "ready":
        return _message_page(res)
    return _page(
        "Mark as done?",
        f"“{res.task.title}” will be marked as done and its reminders stopped.",
        button_url=f"/action/done?t={t}",
    )


@router.post("/action/done", response_class=HTMLResponse)
def mark_done(t: str | None = None, session: Session = Depends(get_session)):
    res = _resolve(session, t)
    if res.state != "ready":
        return _message_page(res)

    claimed = session.execute(
        insert(UsedActionToken)
        .values(nonce=res.token.nonce, task_id=res.task.id)
        .on_conflict_do_nothing(index_elements=["nonce"])
        .returning(UsedActionToken.nonce)
    ).scalar()
    if claimed is None:  # a parallel click used the link first
        session.rollback()
        return _message_page(_resolve(session, t))

    close_task(session, res.task, "done")
    session.commit()

    owner = session.scalar(select(User).where(User.id == res.task.user_id))
    if owner is not None:  # remove the Calendar event; failures are repaired by the reconcile job
        if try_sync_task_event(session, calendar_for(owner, session), res.task):
            session.commit()
    return _page("Done", f"“{res.task.title}” is marked as done.")
