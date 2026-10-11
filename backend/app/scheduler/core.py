"""APScheduler with a Postgres job store (table apscheduler_jobs, created by APScheduler).

Jobs have stable ids and are registered with replace_existing, so restarting the app (or
running several times) never duplicates them, and jobs already stored survive a restart.
"""

import logging

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from app.config import get_settings

logger = logging.getLogger(__name__)

TIMEZONE = "Asia/Kolkata"
JOBSTORE_TABLE = "apscheduler_jobs"

# id -> (function reference, trigger factory)
JOBS = {
    "dispatch": ("app.scheduler.jobs:dispatch_job", lambda: IntervalTrigger(minutes=1)),
    "poll_sync": ("app.scheduler.jobs:poll_sync_job", lambda: IntervalTrigger(minutes=15)),
    "priority_recompute": (
        "app.scheduler.jobs:priority_recompute_job",
        lambda: IntervalTrigger(minutes=30),
    ),
    # Hourly because every user has their own digest hour; run_digest handles who is due.
    "digest": ("app.scheduler.jobs:digest_job", lambda: CronTrigger(minute=0, timezone=TIMEZONE)),
}


SCHEDULER_LOCK_KEY = 0x53434844  # "SCHD"


class SchedulerLock:
    """A Postgres advisory lock held on its own connection for as long as this process lives.
    Whoever holds it runs the scheduler, so a second instance (an overlapping deploy, a stray
    local run against the same database) never sends reminders twice."""

    def __init__(self, engine, connection) -> None:
        self._engine, self._connection = engine, connection

    def release(self) -> None:
        try:
            self._connection.execute(
                text("SELECT pg_advisory_unlock(:k)"), {"k": SCHEDULER_LOCK_KEY}
            )
            self._connection.commit()
        finally:
            self._connection.close()
            self._engine.dispose()


def acquire_scheduler_lock(database_url: str | None = None) -> SchedulerLock | None:
    """The lock, or None when another process already holds it. Needs a direct (not pooled)
    database connection: advisory locks do not survive transaction-pooling proxies."""
    from sqlalchemy import create_engine

    engine = create_engine(database_url or get_settings().database_url, poolclass=NullPool)
    connection = engine.connect()
    got = connection.execute(
        text("SELECT pg_try_advisory_lock(:k)"), {"k": SCHEDULER_LOCK_KEY}
    ).scalar_one()
    connection.commit()
    if not got:
        connection.close()
        engine.dispose()
        return None
    return SchedulerLock(engine, connection)


def build_scheduler(database_url: str | None = None) -> BackgroundScheduler:
    url = database_url or get_settings().database_url
    return BackgroundScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=url, tablename=JOBSTORE_TABLE)},
        timezone=TIMEZONE,
        job_defaults={
            "max_instances": 1,  # never overlap a job with itself
            "coalesce": True,  # many missed runs collapse into one
            "misfire_grace_time": 300,
        },
    )


def register_jobs(scheduler: BackgroundScheduler) -> None:
    for job_id, (func, trigger) in JOBS.items():
        scheduler.add_job(
            func, trigger=trigger(), id=job_id, replace_existing=True, max_instances=1
        )


def start_scheduler(
    database_url: str | None = None, *, paused: bool = False
) -> BackgroundScheduler:
    scheduler = build_scheduler(database_url)
    register_jobs(scheduler)
    scheduler.start(paused=paused)
    logger.info("scheduler started with jobs: %s", ", ".join(sorted(JOBS)))
    return scheduler
