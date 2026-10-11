import logging

from fastapi import APIRouter, Depends, HTTPException
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from app.auth.credentials import load_credentials
from app.auth.deps import get_current_user
from app.db import get_session
from app.filters.pipeline import make_filter_sink
from app.ingest.gmail import GmailApi, GoogleGmailApi, RateLimited
from app.ingest.sync import SyncInProgress, run_sync
from app.models import User

logger = logging.getLogger(__name__)
router = APIRouter()


def get_gmail_api(
    user: User = Depends(get_current_user), session: Session = Depends(get_session)
) -> GmailApi:
    return GoogleGmailApi.from_credentials(load_credentials(user, session))


@router.post("/sync/run")
def sync_run(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    gmail: GmailApi = Depends(get_gmail_api),
) -> dict:
    try:
        result = run_sync(user, session, gmail, make_filter_sink(session))
    except SyncInProgress as exc:
        raise HTTPException(409, "A sync is already running") from exc
    except RateLimited as exc:
        raise HTTPException(429, "Gmail rate limit reached; try again in a minute") from exc
    except HttpError as exc:
        logger.warning("gmail api error during sync: status=%s", exc.resp.status)
        raise HTTPException(502, "Gmail API error") from exc
    return {
        "mode": result.mode,
        "discovered": result.discovered,
        "fetched": result.fetched,
        "delivered": result.delivered,
        "passed": result.decisions.get("passed", 0),
        "quarantined": result.decisions.get("quarantined", 0),
        "ignored": result.decisions.get("ignored", 0),
        "dropped": result.decisions.get("dropped", 0),
        "already_processed": result.decisions.get("skipped", 0),
        "skipped": result.skipped,
        "failed": result.failed,
        "deferred": result.deferred,
        "rate_limited": result.rate_limited,
    }
