"""Gmail sync: backfill, incremental sync via history.list, per-message isolation.

Parsed messages are handed to a `sink`. Persisting them (tasks etc.) is the job of
later modules; the sink runs inside a SAVEPOINT so a failing sink never leaves
partial writes, and it must not commit. Duplicate protection here is the history
cursor, an in-run id set and a per-user lock. A replayed backfill (after an
expired history_id) can redeliver ids, so sinks must be idempotent on message id
(tasks use UNIQUE(user_id, source_ref)).

Privacy: only message ids and counts are logged, never subjects, senders or bodies.
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.ingest.gmail import GmailApi, HistoryExpired, MessageGone, RateLimited
from app.ingest.mime import ParsedMessage, parse_message
from app.ingest.query import (
    DEFAULT_DENYLIST,
    build_query,
    labels_allowed,
    normalize_denylist,
    sender_matches,
    sender_matches_denylist,
)
from app.models import IngestFailure, SyncState, User, UserSettings

logger = logging.getLogger(__name__)

MAX_RETRY_ATTEMPTS = 3
LOCK_NAMESPACE = 0x53594E43  # "SYNC"
ERROR_MAX_CHARS = 200

# A sink returns None (plain delivery) or the decision it took: "passed" | "quarantined" |
# "skipped" (already handled) | "failed" (the sink recorded the failure itself and swallowed it).
Sink = Callable[[User, ParsedMessage], str | None]


class SyncInProgress(Exception):
    """Another sync is already running for this user."""


@dataclass
class SyncResult:
    mode: str  # "backfill" | "incremental"
    history_reset: bool = False  # True when an expired history_id forced a backfill
    discovered: int = 0  # unique message ids found (plus retried failures)
    fetched: int = 0  # messages actually retrieved from Gmail
    delivered: int = 0  # handed to the sink
    skipped: int = 0  # filtered out, or deleted before fetch
    failed: int = 0  # recorded in ingest_failures this run
    deferred: int = 0  # not attempted because Gmail rate-limited us; retried next sync
    rate_limited: bool = False
    decisions: dict[str, int] = field(default_factory=dict)  # sink decisions, when it reports them
    history_id: str | None = None
    delivered_ids: list[str] = field(default_factory=list)


def discard_sink(user: User, message: ParsedMessage) -> str | None:
    """Placeholder sink until the filtering/task modules exist."""


@contextmanager
def user_sync_lock(session: Session, user_id: int) -> Iterator[None]:
    """Postgres advisory lock held on a dedicated connection for the whole sync."""
    key = LOCK_NAMESPACE * 2**32 + user_id % 2**32
    with session.get_bind().connect() as conn:
        acquired = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar_one()
        conn.commit()
        if not acquired:
            raise SyncInProgress(f"sync already running for user {user_id}")
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
            conn.commit()


def get_or_create_settings(session: Session, user_id: int) -> UserSettings:
    row = session.get(UserSettings, user_id)
    if row is None:
        row = UserSettings(user_id=user_id, denylist=list(DEFAULT_DENYLIST))
        session.add(row)
        session.commit()
    return row


def _get_or_create_state(session: Session, user_id: int) -> SyncState:
    row = session.get(SyncState, user_id)
    if row is None:
        row = SyncState(user_id=user_id)
        session.add(row)
        session.commit()
    return row


def _backfill_ids(gmail: GmailApi, query: str) -> dict[str, list[str] | None]:
    ids: dict[str, list[str] | None] = {}
    token = None
    while True:
        page, token = gmail.list_messages(query, token)
        for message_id in page:
            ids.setdefault(message_id, None)
        if not token:
            return ids


def _incremental_ids(
    gmail: GmailApi, start_history_id: str
) -> tuple[dict[str, list[str] | None], str | None]:
    ids: dict[str, list[str] | None] = {}
    latest = None
    token = None
    while True:
        records, token, history_id = gmail.list_history(start_history_id, token)
        latest = history_id or latest
        for record in records:
            for added in record.get("messagesAdded", []):
                message = added["message"]
                ids.setdefault(message["id"], message.get("labelIds"))
        if not token:
            return ids, latest


def record_failure(session: Session, user_id: int, message_id: str, exc: Exception) -> None:
    error = f"{type(exc).__name__}: {exc}"[:ERROR_MAX_CHARS]
    row = session.scalar(
        select(IngestFailure).where(
            IngestFailure.user_id == user_id, IngestFailure.message_id == message_id
        )
    )
    if row:
        row.attempts += 1
        row.error = error
    else:
        session.add(IngestFailure(user_id=user_id, message_id=message_id, error=error, attempts=1))


def _clear_failure(session: Session, user_id: int, message_id: str) -> None:
    for row in session.scalars(
        select(IngestFailure).where(
            IngestFailure.user_id == user_id, IngestFailure.message_id == message_id
        )
    ):
        session.delete(row)


def _defer(session: Session, user_id: int, message_ids: list[str]) -> None:
    """Queue messages for the next sync without consuming a retry attempt (attempts=0)."""
    existing = set(
        session.scalars(
            select(IngestFailure.message_id).where(
                IngestFailure.user_id == user_id, IngestFailure.message_id.in_(message_ids)
            )
        )
    )
    for message_id in message_ids:
        if message_id not in existing:
            session.add(
                IngestFailure(
                    user_id=user_id,
                    message_id=message_id,
                    error="RateLimited: deferred to the next sync",
                    attempts=0,
                )
            )
    session.commit()


def run_sync(
    user: User, session: Session, gmail: GmailApi, sink: Sink = discard_sink
) -> SyncResult:
    with user_sync_lock(session, user.id):
        return _run(user, session, gmail, sink)


def _run(user: User, session: Session, gmail: GmailApi, sink: Sink) -> SyncResult:
    settings = get_or_create_settings(session, user.id)
    denylist = normalize_denylist(settings.denylist)
    always_track = normalize_denylist(settings.always_track)
    state = _get_or_create_state(session, user.id)

    result = SyncResult(mode="incremental")
    candidates: dict[str, list[str] | None] | None = None
    new_history_id: str | None = None

    if state.history_id:
        try:
            candidates, new_history_id = _incremental_ids(gmail, state.history_id)
        except HistoryExpired:
            logger.warning("user %s history_id expired; falling back to backfill", user.id)
            result.history_reset = True
            state.history_id = None
    if candidates is None:
        result.mode = "backfill"
        # Capture the cursor BEFORE listing so mail arriving mid-backfill isn't missed.
        new_history_id = gmail.profile_history_id()
        candidates = _backfill_ids(gmail, build_query(denylist, always_track))

    # Earlier failures get another chance (bounded), without double-handling.
    for message_id in session.scalars(
        select(IngestFailure.message_id).where(
            IngestFailure.user_id == user.id, IngestFailure.attempts < MAX_RETRY_ATTEMPTS
        )
    ):
        candidates.setdefault(message_id, None)

    result.discovered = len(candidates)
    pending = list(candidates.items())
    for position, (message_id, stub_labels) in enumerate(pending):
        try:
            _process_message(
                user, session, gmail, sink, denylist, message_id, stub_labels, result, always_track
            )
        except RateLimited:
            # Quota isn't message-specific: park the rest instead of failing each one.
            rest = [mid for mid, _ in pending[position:]]
            _defer(session, user.id, rest)
            result.deferred, result.rate_limited = len(rest), True
            logger.warning("user %s rate limited; deferred %d messages", user.id, len(rest))
            break

    state.history_id = new_history_id or state.history_id
    state.last_success_at = datetime.now(UTC)
    session.commit()
    result.history_id = state.history_id

    logger.info(
        "sync user=%s mode=%s history_reset=%s discovered=%d fetched=%d delivered=%d "
        "skipped=%d failed=%d deferred=%d",
        user.id, result.mode, result.history_reset, result.discovered, result.fetched,
        result.delivered, result.skipped, result.failed, result.deferred,
    )  # fmt: skip
    logger.info("sync user=%s delivered ids (first 20): %s", user.id, result.delivered_ids[:20])
    logger.debug("sync user=%s all delivered ids: %s", user.id, result.delivered_ids)
    return result


def _process_message(
    user: User,
    session: Session,
    gmail: GmailApi,
    sink: Sink,
    denylist: list[str],
    message_id: str,
    stub_labels: list[str] | None,
    result: SyncResult,
    always_track: list[str] = (),  # type: ignore[assignment]
) -> None:
    try:
        # History stubs carry labels but no sender, so with tracked senders configured a message
        # from the Promotions tab must be fetched to find out whether it is one of them.
        if stub_labels is not None and not labels_allowed(stub_labels, tracked=bool(always_track)):
            outcome = "skipped"
        else:
            raw = gmail.get_message(message_id)
            result.fetched += 1
            parsed = parse_message(raw, gmail.get_attachment)
            tracked = sender_matches(parsed.sender, always_track)
            if not labels_allowed(parsed.labels, tracked=tracked) or (
                not tracked and sender_matches_denylist(parsed.sender, denylist)
            ):
                outcome = "skipped"
            else:
                with session.begin_nested():
                    decision = sink(user, parsed)
                if decision:
                    result.decisions[decision] = result.decisions.get(decision, 0) + 1
                outcome = {"skipped": "skipped", "failed": "failed"}.get(decision, "delivered")
    except MessageGone:
        outcome = "skipped"
    except RateLimited:
        raise
    except Exception as exc:
        logger.warning("message %s failed: %s", message_id, type(exc).__name__)
        record_failure(session, user.id, message_id, exc)
        session.commit()
        result.failed += 1
        return

    if outcome == "failed":  # the sink already wrote processed_messages + ingest_failures
        session.commit()
        result.failed += 1
        return

    _clear_failure(session, user.id, message_id)
    session.commit()
    if outcome == "delivered":
        result.delivered += 1
        result.delivered_ids.append(message_id)
    else:
        result.skipped += 1
