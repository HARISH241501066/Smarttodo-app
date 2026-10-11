"""Wording of a reminder, chosen by the ladder step (kind) it belongs to."""

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

_TITLES = {
    "created": "New task: {t}",
    "t48": "Due in 2 days: {t}",
    "t24": "Complete immediately: {t}",
    "immediate": "Complete immediately: {t}",
    "t10": "Final reminder: {t}",
    "t1": "Due in 1 hour: {t}",
}


def display_ist(moment: datetime) -> str:
    return moment.astimezone(IST).strftime("%a, %d %b %Y, %I:%M %p IST")


def render_reminder(
    kind: str | None, task_title: str, deadline: datetime | None
) -> tuple[str, str]:
    title = _TITLES.get(kind or "created", "Reminder: {t}").format(t=task_title)
    body = f"Due {display_ist(deadline)}" if deadline else "No deadline set"
    return title, body
