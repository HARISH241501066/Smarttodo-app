"""Filter stage that sits between Gmail ingestion and task creation.

For every parsed message: skip it if already handled, otherwise score it, then either
quarantine it or hand it (in memory only) to the next stage. The decision is recorded in
processed_messages, which holds ids and short reasons, never message content.
Only message ids and decisions are logged.

The next stage may answer with a StageOutcome to say the message was not a keeper after all
(decision "ignored") or to add a short note to the record; returning nothing means "passed".
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.filters.auth_check import DEFAULT_BRANDS
from app.filters.scam import score_message
from app.ingest.mime import ParsedMessage
from app.ingest.sync import Sink, record_failure
from app.models import ProcessedMessage, Quarantine, User, UserSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StageOutcome:
    decision: str = "passed"  # "passed" | "ignored"
    note: str | None = None  # e.g. "task: assignment is live/due"; stored in processed_messages


NextStage = Callable[[User, ParsedMessage], StageOutcome | None]


def discard_next_stage(user: User, message: ParsedMessage) -> None:
    """Placeholder until Module 5 (task extraction). Must be idempotent on message id."""


def record_decision(
    session: Session, user_id: int, message_id: str, decision: str, reason: str
) -> None:
    stmt = insert(ProcessedMessage).values(
        user_id=user_id, message_id=message_id, decision=decision, reason=reason[:255]
    )
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["user_id", "message_id"],
            set_={"decision": decision, "reason": reason[:255], "processed_at": func.now()},
        )
    )


def make_filter_sink(
    session: Session,
    next_stage: NextStage = discard_next_stage,
    brands: dict[str, tuple[str, ...]] = DEFAULT_BRANDS,
) -> Sink:
    """Build a sync sink. Returns the decision so the sync can count it."""

    def sink(user: User, message: ParsedMessage) -> str:
        previous = session.scalar(
            select(ProcessedMessage.decision).where(
                ProcessedMessage.user_id == user.id,
                ProcessedMessage.message_id == message.message_id,
            )
        )
        if previous is not None and previous != "failed":  # failed ones are retried
            return "skipped"

        try:
            with session.begin_nested():
                settings = session.get(UserSettings, user.id)
                verdict = score_message(message, brands, settings.always_track if settings else ())
                if verdict.quarantine:
                    session.execute(
                        insert(Quarantine)
                        .values(
                            user_id=user.id,
                            message_id=message.message_id,
                            subject=message.subject[:500],
                            sender=message.sender[:500],
                            score=verdict.score,
                            reasons=verdict.reasons,
                        )
                        .on_conflict_do_nothing(index_elements=["user_id", "message_id"])
                    )
                    decision = "quarantined"
                    stage_note = None
                else:
                    outcome = next_stage(user, message)
                    decision = outcome.decision if outcome else "passed"
                    stage_note = outcome.note if outcome else None
                note = f"score={verdict.score}"
                if verdict.trusted_tracked:
                    note = "tracked sender"
                elif verdict.tracked:
                    note += "; tracked sender failed authentication"
                if stage_note:
                    note = f"{stage_note}; {note}"
                record_decision(session, user.id, message.message_id, decision, note)
        except Exception as exc:
            logger.warning(
                "message %s failed in filter stage: %s", message.message_id, type(exc).__name__
            )
            record_decision(session, user.id, message.message_id, "failed", type(exc).__name__)
            record_failure(session, user.id, message.message_id, exc)
            return "failed"

        logger.info("message %s decision=%s score=%d", message.message_id, decision, verdict.score)
        return decision

    return sink
