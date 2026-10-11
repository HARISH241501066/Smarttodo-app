"""Heartbeats and the /health answer.

The scheduler's dispatcher writes a beat every minute. /health is 200 only when the database
answers and (when this deployment runs a scheduler) the beat is less than five minutes old, so an
outside monitor notices a stuck scheduler as well as a dead server. The answer names the failing
part and nothing else: no secrets, no user data.
"""

import threading
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db import get_engine
from app.models import Heartbeat

DISPATCHER = "dispatcher"
STALE_AFTER = timedelta(minutes=5)
CACHE_SECONDS = 60  # monitors ping often; the database is asked at most once a minute

_lock = threading.Lock()
_cache: dict = {}  # {"at": monotonic seconds, "db_ok": bool, "last": datetime | None}


def reset_health_cache() -> None:
    with _lock:
        _cache.clear()


def _probe() -> tuple[bool, datetime | None]:
    """One round trip to the database: is it up, and when did the scheduler last beat."""
    try:
        with sessionmaker(get_engine(), expire_on_commit=False)() as session:
            last = session.scalar(
                select(func.max(Heartbeat.last_beat_at)).where(Heartbeat.name == DISPATCHER)
            )
        return True, last
    except Exception:
        return False, None


def _cached_probe() -> tuple[bool, datetime | None]:
    with _lock:
        fresh = _cache and time.monotonic() - _cache["at"] < CACHE_SECONDS
        if fresh:
            return _cache["db_ok"], _cache["last"]
    result = _probe()  # outside the lock: a slow database must not block other pings
    with _lock:
        _cache.update(at=time.monotonic(), db_ok=result[0], last=result[1])
    return result


def beat(session: Session, name: str = DISPATCHER, now: datetime | None = None) -> None:
    """Record "alive now". Commits."""
    now = now or datetime.now(UTC)
    session.execute(
        insert(Heartbeat)
        .values(name=name, last_beat_at=now)
        .on_conflict_do_update(index_elements=["name"], set_={"last_beat_at": now})
    )
    session.commit()


def beat_now(name: str = DISPATCHER) -> None:
    with sessionmaker(get_engine(), expire_on_commit=False)() as session:
        beat(session, name)


def health(now: datetime | None = None) -> tuple[int, dict[str, str]]:
    """(HTTP status, body). The database answer is reused for CACHE_SECONDS."""
    now = now or datetime.now(UTC)
    body = {"status": "ok", "db": "ok", "scheduler": "ok"}
    db_ok, last = _cached_probe()
    if not db_ok:
        return 503, {"status": "fail", "db": "error", "scheduler": "unknown"}
    if not get_settings().scheduler_enabled:
        body["scheduler"] = "disabled"  # development and tests run without one
    elif last is None or now - last > STALE_AFTER:
        body["scheduler"] = "stale"
        body["status"] = "fail"
    return (200 if body["status"] == "ok" else 503), body
