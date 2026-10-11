"""Job entry points. Module-level functions with no arguments, so the Postgres job store can
persist them by reference. Each opens its own session and never lets an error escape the
scheduler thread."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from googleapiclient.errors import HttpError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.credentials import ReauthRequired, load_credentials
from app.db import get_engine
from app.filters.pipeline import make_filter_sink
from app.ingest.gmail import GmailApi, GoogleGmailApi, RateLimited
from app.ingest.sync import SyncInProgress, run_sync
from app.models import User
from app.notify.calendar import reconcile_calendar
from app.priority import complete_expired_email_tasks, recompute_priorities
from app.scheduler.digest import run_digest
from app.scheduler.dispatcher import dispatch_due

logger = logging.getLogger(__name__)

GmailFactory = Callable[[User, Session], GmailApi]


def _session() -> Session:
    return sessionmaker(get_engine(), expire_on_commit=False)()


def _default_gmail(user: User, session: Session) -> GmailApi:
    return GoogleGmailApi.from_credentials(load_credentials(user, session))


def poll_sync(
    session_factory: Callable[[], Session] = _session,
    gmail_factory: GmailFactory = _default_gmail,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[int, str]:
    """Sync every signed-in user. One user's failure never stops the others.
    Returns user id -> outcome, for tests and logs."""
    with session_factory() as listing:
        user_ids = list(
            listing.scalars(
                select(User.id).where(
                    User.encrypted_refresh_token.is_not(None), User.needs_reauth.is_(False)
                )
            )
        )
    outcomes: dict[int, str] = {}
    for user_id in user_ids:
        with session_factory() as session:
            user = session.get(User, user_id)
            try:
                gmail = gmail_factory(user, session)
                result = run_sync(user, session, gmail, make_filter_sink(session, now))
                outcomes[user_id] = f"ok delivered={result.delivered} failed={result.failed}"
            except SyncInProgress:
                outcomes[user_id] = "busy"
            except ReauthRequired:
                outcomes[user_id] = "needs_reauth"
            except RateLimited:
                outcomes[user_id] = "rate_limited"
            except HttpError as exc:
                outcomes[user_id] = f"gmail_error_{exc.resp.status}"
            except Exception as exc:  # keep the scheduler alive; log the type only
                session.rollback()
                outcomes[user_id] = f"error_{type(exc).__name__}"
            logger.info("poll sync user=%s: %s", user_id, outcomes[user_id])
    return outcomes


def _guarded(name: str, work: Callable[[Session], object]) -> None:
    try:
        with _session() as session:
            work(session)
    except Exception as exc:
        logger.error("job %s failed: %s", name, type(exc).__name__)


def dispatch_job() -> None:
    _guarded("dispatch", lambda s: dispatch_due(s))


def poll_sync_job() -> None:
    try:
        poll_sync()
    except Exception as exc:
        logger.error("job poll_sync failed: %s", type(exc).__name__)
    try:  # tasks the sync created (or that Google missed earlier) get their Calendar event
        reconcile_calendar(_session)
    except Exception as exc:
        logger.error("calendar reconcile failed: %s", type(exc).__name__)


def priority_recompute_job() -> None:
    def work(session: Session) -> None:
        expired = complete_expired_email_tasks(session)
        changed = recompute_priorities(session)
        session.commit()
        logger.info("priority recompute: %d upgraded, %d expired completed", changed, expired)

    _guarded("priority_recompute", work)


def digest_job() -> None:
    _guarded("digest", lambda s: run_digest(s))
