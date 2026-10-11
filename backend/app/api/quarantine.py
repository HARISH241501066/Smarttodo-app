"""The Suspicious queue: list held messages and rescue a false positive into a task."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from googleapiclient.errors import HttpError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.api.sync import get_gmail_api
from app.auth.deps import get_current_user
from app.db import get_session
from app.filters.task_stage import ingest_message
from app.ingest.gmail import GmailApi, MessageGone, RateLimited
from app.ingest.mime import parse_message
from app.models import ProcessedMessage, Quarantine, User
from app.schemas.tasks import QuarantineOut, RescueOut, TaskOut

logger = logging.getLogger(__name__)
router = APIRouter(tags=["quarantine"])


def _owned(session: Session, user: User, quarantine_id: int) -> Quarantine:
    row = session.scalar(
        select(Quarantine).where(Quarantine.id == quarantine_id, Quarantine.user_id == user.id)
    )
    if row is None:
        raise HTTPException(404, "Quarantined message not found")
    return row


@router.get("/quarantine", response_model=list[QuarantineOut])
def list_quarantine(
    status: str = "held",
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[Quarantine]:
    if status not in ("held", "rescued", "confirmed_scam", "all"):
        raise HTTPException(422, "status must be held, rescued, confirmed_scam or all")
    stmt = select(Quarantine).where(Quarantine.user_id == user.id)
    if status != "all":
        stmt = stmt.where(Quarantine.status == status)
    return list(session.scalars(stmt.order_by(Quarantine.created_at.desc(), Quarantine.id.desc())))


@router.post("/quarantine/{quarantine_id}/rescue", response_model=RescueOut)
def rescue(
    quarantine_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    gmail: GmailApi = Depends(get_gmail_api),
) -> RescueOut:
    """Re-fetch the message and run it through the task pipeline, skipping the scam filter."""
    row = _owned(session, user, quarantine_id)
    if row.status != "held":
        raise HTTPException(409, f"Message is already {row.status}")
    try:
        parsed = parse_message(gmail.get_message(row.message_id), gmail.get_attachment)
    except MessageGone as exc:
        raise HTTPException(410, "The message no longer exists in Gmail") from exc
    except RateLimited as exc:
        raise HTTPException(429, "Gmail rate limit reached; try again in a minute") from exc
    except HttpError as exc:
        logger.warning("gmail api error during rescue: status=%s", exc.resp.status)
        raise HTTPException(502, "Gmail API error") from exc

    task, created = ingest_message(session, user, parsed, datetime.now(UTC))
    row.status = "rescued"
    stmt = insert(ProcessedMessage).values(
        user_id=user.id, message_id=row.message_id, decision="passed", reason="rescued"
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["user_id", "message_id"],
            set_={"decision": "passed", "reason": "rescued"},
        )
    )
    session.commit()
    logger.info("quarantine %s rescued (message %s)", row.id, row.message_id)
    return RescueOut(
        quarantine=QuarantineOut.model_validate(row),
        task=TaskOut.from_task(task) if task else None,
        task_created=created,
    )
