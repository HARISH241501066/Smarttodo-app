"""Google Calendar: one event per open task that has a deadline.

The event carries the title, the deadline, the action link in its description (and as its
source), and popup reminders 24h and 10h before. It is created when the task becomes open with a
deadline, updated when the task changes, and deleted when the task is done, dismissed, loses its
deadline or is deleted. Routes sync right away (best effort: a Google outage never fails a task
request); a reconcile pass, run after every polling sync, repairs anything that was missed.

Tasks are identified by gcal_event_id. gcal_synced_at is compared with updated_at to find tasks
that changed since their event was last written.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from fastapi import Depends
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from app.auth.credentials import load_credentials
from app.auth.deps import get_current_user
from app.config import get_settings
from app.db import get_session
from app.models import CalendarOrphan, Task, User
from app.notify.email import _safe_http_url, link_domain

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
EVENT_LENGTH = timedelta(minutes=15)
POPUP_MINUTES = (24 * 60, 10 * 60)
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ORPHAN_ATTEMPTS = 5
RECONCILE_BATCH = 50


class EventGone(Exception):
    """The event no longer exists in the calendar (deleted there, or never saved)."""


@dataclass(frozen=True)
class EventSpec:
    title: str
    deadline: datetime
    link: str | None


class CalendarApi(Protocol):
    def create_event(self, spec: EventSpec) -> str: ...

    def update_event(self, event_id: str, spec: EventSpec) -> None:
        """Raises EventGone if the event no longer exists."""
        ...

    def delete_event(self, event_id: str) -> None:
        """Deleting an event that is already gone is not an error."""
        ...


def event_body(spec: EventSpec) -> dict[str, Any]:
    link = _safe_http_url(spec.link)
    description = "From Smart To-Do"
    if link:
        description = f"Open & Complete: {link}\n({link_domain(link)})\n\n{description}"
    start = spec.deadline.astimezone(IST)
    body: dict[str, Any] = {
        "summary": spec.title[:1000],
        "description": description,
        "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Kolkata"},
        "end": {"dateTime": (start + EVENT_LENGTH).isoformat(), "timeZone": "Asia/Kolkata"},
        "reminders": {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": m} for m in POPUP_MINUTES],
        },
    }
    if link:
        body["source"] = {"title": "Open & Complete", "url": link}
    return body


def _transient(exc: BaseException) -> bool:
    if isinstance(exc, HttpError):
        return exc.resp.status in RETRY_STATUSES
    return isinstance(exc, OSError)


class GoogleCalendarApi:
    def __init__(self, service, *, wait=None):
        self._service = service
        self._wait = wait or wait_exponential(multiplier=1, max=8)

    @classmethod
    def from_credentials(cls, credentials) -> "GoogleCalendarApi":
        return cls(build("calendar", "v3", credentials=credentials, cache_discovery=False))

    def _execute(self, request):
        return Retrying(
            retry=retry_if_exception(_transient),
            stop=stop_after_attempt(3),
            wait=self._wait,
            reraise=True,
        )(request.execute)

    def create_event(self, spec: EventSpec) -> str:
        created = self._execute(
            self._service.events().insert(calendarId="primary", body=event_body(spec))
        )
        return created["id"]

    def update_event(self, event_id: str, spec: EventSpec) -> None:
        try:
            self._execute(
                self._service.events().patch(
                    calendarId="primary", eventId=event_id, body=event_body(spec)
                )
            )
        except HttpError as exc:
            if exc.resp.status in (404, 410):
                raise EventGone(event_id) from exc
            raise

    def delete_event(self, event_id: str) -> None:
        try:
            self._execute(self._service.events().delete(calendarId="primary", eventId=event_id))
        except HttpError as exc:
            if exc.resp.status not in (404, 410):  # already gone is success
                raise


class LazyCalendar:
    """Builds the Google client (which refreshes the access token) only when first used."""

    def __init__(self, user: User, session: Session):
        self._user, self._session = user, session
        self._api: GoogleCalendarApi | None = None

    def _get(self) -> GoogleCalendarApi:
        if self._api is None:
            self._api = GoogleCalendarApi.from_credentials(
                load_credentials(self._user, self._session)
            )
        return self._api

    def create_event(self, spec: EventSpec) -> str:
        return self._get().create_event(spec)

    def update_event(self, event_id: str, spec: EventSpec) -> None:
        self._get().update_event(event_id, spec)

    def delete_event(self, event_id: str) -> None:
        self._get().delete_event(event_id)


def calendar_for(user: User, session: Session) -> CalendarApi | None:
    """None when events are off or this user has no Google token to write with."""
    if not get_settings().calendar_enabled or not user.encrypted_refresh_token:
        return None
    if user.needs_reauth:
        return None
    return LazyCalendar(user, session)


def get_calendar_api(
    user: User = Depends(get_current_user), session: Session = Depends(get_session)
) -> CalendarApi | None:
    return calendar_for(user, session)


# ---- syncing ----


def wants_event(task: Task) -> bool:
    return task.status == "open" and task.deadline is not None


def sync_task_event(session: Session, api: CalendarApi, task: Task, now: datetime | None = None):
    """Make the task's event match the task. Raises on a Google error; callers decide whether
    that matters. Flushes but does not commit."""
    now = now or datetime.now(UTC)
    if wants_event(task):
        spec = EventSpec(task.title, task.deadline, task.link)
        if task.gcal_event_id:
            try:
                api.update_event(task.gcal_event_id, spec)
            except EventGone:
                task.gcal_event_id = api.create_event(spec)
        else:
            task.gcal_event_id = api.create_event(spec)
    elif task.gcal_event_id:
        api.delete_event(task.gcal_event_id)
        task.gcal_event_id = None
    task.gcal_synced_at = now
    session.flush()


def try_sync_task_event(session: Session, api: CalendarApi | None, task: Task) -> bool:
    """Best-effort sync for request handlers. Never raises; a failure is left for reconcile."""
    if api is None:
        return False
    try:
        sync_task_event(session, api, task)
        return True
    except Exception as exc:
        logger.warning("calendar sync failed for task %s: %s", task.id, type(exc).__name__)
        return False


def delete_event_for_removed_task(
    session: Session, api: CalendarApi | None, user_id: int, event_id: str | None
) -> None:
    """Call before the task row is deleted. If Google cannot be reached the event id is parked
    in calendar_orphans so a later reconcile still deletes it."""
    if not event_id:
        return
    try:
        if api is None:
            raise RuntimeError("calendar unavailable")
        api.delete_event(event_id)
    except Exception as exc:
        logger.warning("calendar delete failed (user %s): %s", user_id, type(exc).__name__)
        session.add(CalendarOrphan(user_id=user_id, event_id=event_id))


def reconcile_calendar(
    session_factory: Callable[[], Session],
    api_factory: Callable[[User, Session], CalendarApi | None] = calendar_for,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, int]:
    """Repair drift: create, update and delete events for tasks that changed while Google was
    unreachable or that were created by the sync pipeline, and retry parked deletions."""
    counts = {"synced": 0, "failed": 0, "orphans_deleted": 0}
    with session_factory() as listing:
        user_ids = list(
            listing.scalars(
                select(User.id).where(
                    User.encrypted_refresh_token.is_not(None), User.needs_reauth.is_(False)
                )
            )
        )
    for user_id in user_ids:
        with session_factory() as session:
            user = session.get(User, user_id)
            api = api_factory(user, session)
            if api is None:
                continue
            stale = and_(
                Task.status == "open",
                Task.deadline.is_not(None),
                or_(
                    Task.gcal_event_id.is_(None),
                    Task.gcal_synced_at.is_(None),
                    Task.updated_at > Task.gcal_synced_at,
                ),
            )
            leftover = and_(
                or_(Task.status != "open", Task.deadline.is_(None)),
                Task.gcal_event_id.is_not(None),
            )
            tasks = session.scalars(
                select(Task)
                .where(Task.user_id == user_id, or_(stale, leftover))
                .order_by(Task.id)
                .limit(RECONCILE_BATCH)
            ).all()
            for task in tasks:
                if try_sync_task_event(session, api, task):
                    counts["synced"] += 1
                else:
                    counts["failed"] += 1
                    session.rollback()
                    break  # Google is failing for this user; try again next time
                session.commit()
            for orphan in session.scalars(
                select(CalendarOrphan).where(
                    CalendarOrphan.user_id == user_id, CalendarOrphan.attempts < MAX_ORPHAN_ATTEMPTS
                )
            ).all():
                try:
                    api.delete_event(orphan.event_id)
                    session.delete(orphan)
                    counts["orphans_deleted"] += 1
                except Exception as exc:
                    orphan.attempts += 1
                    logger.warning("orphan event delete failed: %s", type(exc).__name__)
                session.commit()
    if any(counts.values()):
        logger.info("calendar reconcile: %s", counts)
    return counts
