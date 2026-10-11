"""The sync sink: turn every fetched message into exactly one recorded decision.

For each parsed message: skip it if already handled, otherwise run filters.decide and act on the
answer:

    ignore      -> processed_messages decision=ignored
    drop        -> decision=dropped
    suspicious  -> a Suspicious (quarantine) row, decision=quarantined
    task/review -> a task (needs_review if deadlines conflict) + its reminders, decision=passed

processed_messages holds ids and short reasons, never message content. Only message ids and
decisions are logged.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.filters.auth_check import DEFAULT_BRANDS
from app.filters.decide import DROP, IGNORE, SUSPICIOUS, decide
from app.filters.task_stage import ingest_message
from app.ingest.mime import ParsedMessage
from app.ingest.query import normalize_entries
from app.ingest.sync import Sink, get_or_create_settings, record_failure
from app.models import ProcessedMessage, Quarantine, User
from app.scheduler.reminders import sync_reminders
from app.sources import load_sources

logger = logging.getLogger(__name__)

SPOOF_SCORE = 4  # shown score for a source impersonation (the quarantine threshold)


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
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    brands: dict[str, tuple[str, ...]] = DEFAULT_BRANDS,
    *,
    reprocess: bool = False,
) -> Sink:
    """Build a sync sink. Returns the decision so the sync can count it. `reprocess` (used by a
    rebuild) decides again on messages that were handled before."""

    def sink(user: User, message: ParsedMessage) -> str:
        if not reprocess:
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
                now = clock()
                settings = get_or_create_settings(session, user.id)
                result = decide(
                    message,
                    sources=load_sources(settings.sources),
                    keywords=list(settings.watch_keywords or []),
                    block_senders=normalize_entries(settings.block_senders),
                    require_deadline=settings.require_deadline,
                    received_or_now=message.received_at or now,
                    now=now,
                    brands=brands,
                )
                if result.kind == IGNORE:
                    decision = "ignored"
                elif result.kind == DROP:
                    decision = "dropped"
                elif result.kind == SUSPICIOUS:
                    score = result.scam.score if result.scam else SPOOF_SCORE
                    session.execute(
                        insert(Quarantine)
                        .values(
                            user_id=user.id,
                            message_id=message.message_id,
                            subject=message.subject[:500],
                            sender=message.sender[:500],
                            score=score,
                            reasons=result.reasons,
                        )
                        .on_conflict_do_nothing(index_elements=["user_id", "message_id"])
                    )
                    decision = "quarantined"
                else:  # task or review
                    task, created = ingest_message(session, user, message, now, result)
                    if task is not None:  # (re)build the reminder ladder; none while in review
                        sync_reminders(session, task, now=now, created=created)
                    decision = "passed"
                if reprocess and decision != "quarantined":
                    # Re-judged as not suspicious any more: it must not stay in the held queue.
                    session.execute(
                        delete(Quarantine).where(
                            Quarantine.user_id == user.id,
                            Quarantine.message_id == message.message_id,
                            Quarantine.status == "held",
                        )
                    )
                record_decision(session, user.id, message.message_id, decision, result.reason)
        except Exception as exc:
            logger.warning(
                "message %s failed in filter stage: %s", message.message_id, type(exc).__name__
            )
            record_decision(session, user.id, message.message_id, "failed", type(exc).__name__)
            record_failure(session, user.id, message.message_id, exc)
            return "failed"

        logger.info("message %s decision=%s kind=%s", message.message_id, decision, result.kind)
        return decision

    return sink
