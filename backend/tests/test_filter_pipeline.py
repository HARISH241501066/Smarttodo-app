"""Filter pipeline: idempotency, failure isolation, privacy of what gets stored."""

import json
from pathlib import Path

import pytest
from sqlalchemy import func, inspect, select, text

from app.filters.pipeline import make_filter_sink
from app.ingest.mime import parse_message
from app.ingest.query import build_query
from app.ingest.sync import MAX_RETRY_ATTEMPTS, run_sync
from app.models import IngestFailure, ProcessedMessage, Quarantine, User, UserSettings
from tests.gmail_fakes import FakeGmailApi

FIXTURES = Path(__file__).parent / "fixtures" / "filter_emails"


def raw_fixtures(kind: str) -> dict[str, dict]:
    out = {}
    for path in sorted((FIXTURES / kind).glob("*.json")):
        raw = json.loads(path.read_text())
        out[raw["id"]] = raw
    return out


@pytest.fixture
def user(db_session):
    u = User(email="me@example.test")
    db_session.add(u)
    db_session.commit()
    return u


class NextStage:
    def __init__(self, fail_on=()):
        self.seen: list[str] = []
        self.fail_on = set(fail_on)

    def __call__(self, user, message):
        if message.message_id in self.fail_on:
            raise RuntimeError("next stage exploded")
        self.seen.append(message.message_id)


def decisions(session, user) -> dict[str, str]:
    rows = session.scalars(select(ProcessedMessage).where(ProcessedMessage.user_id == user.id))
    return {r.message_id: r.decision for r in rows}


def count(session, model, user) -> int:
    return session.scalar(select(func.count()).select_from(model).where(model.user_id == user.id))


# ---------- end to end over all fixtures ----------


def test_all_fixtures_through_a_sync_give_expected_decisions(db_session, user):
    messages = {**raw_fixtures("scam"), **raw_fixtures("legit")}
    nxt = NextStage()
    result = run_sync(user, db_session, FakeGmailApi(messages), make_filter_sink(db_session, nxt))

    assert result.decisions == {"quarantined": 10, "passed": 15}
    assert result.failed == 0
    assert set(nxt.seen) == set(raw_fixtures("legit"))  # scams never reach the next stage
    assert count(db_session, Quarantine, user) == 10
    assert count(db_session, ProcessedMessage, user) == 25
    for row in db_session.scalars(select(Quarantine)):
        assert row.message_id.startswith("s")
        assert row.score >= 4
        assert len(row.reasons) >= 1
        assert row.status == "held"


# ---------- idempotency ----------


def test_processing_the_same_message_twice_leaves_one_row_each(db_session, user):
    raw = raw_fixtures("scam")["s01"]
    sink = make_filter_sink(db_session, NextStage())
    parsed = parse_message(raw)

    assert sink(user, parsed) == "quarantined"
    assert sink(user, parsed) == "skipped"
    db_session.commit()

    assert count(db_session, ProcessedMessage, user) == 1
    assert count(db_session, Quarantine, user) == 1


def test_a_replayed_backfill_does_not_reprocess_or_duplicate(db_session, user):
    messages = {**raw_fixtures("scam"), **raw_fixtures("legit")}
    nxt = NextStage()
    sink = make_filter_sink(db_session, nxt)
    run_sync(user, db_session, FakeGmailApi(messages), sink)

    db_session.execute(text("UPDATE sync_state SET history_id = NULL"))  # force a second backfill
    db_session.commit()
    again = run_sync(user, db_session, FakeGmailApi(messages), sink)

    assert again.decisions == {"skipped": 25}
    assert count(db_session, ProcessedMessage, user) == 25
    assert count(db_session, Quarantine, user) == 10
    assert len(nxt.seen) == 15  # each legitimate message reached the next stage exactly once


def test_unique_constraint_blocks_a_second_quarantine_row(db_session, user):
    db_session.add(Quarantine(user_id=user.id, message_id="dup", reasons=[]))
    db_session.commit()
    db_session.add(Quarantine(user_id=user.id, message_id="dup", reasons=[]))
    with pytest.raises(Exception, match="uq_quarantine_user_id_message_id"):
        db_session.commit()
    db_session.rollback()


# ---------- failure isolation ----------


def test_exception_records_failed_and_the_batch_continues(db_session, user):
    legit = raw_fixtures("legit")
    ids = sorted(legit)
    bad = ids[1]
    nxt = NextStage(fail_on=[bad])
    result = run_sync(user, db_session, FakeGmailApi(legit), make_filter_sink(db_session, nxt))

    assert result.failed == 1
    assert result.decisions["passed"] == 14
    assert decisions(db_session, user)[bad] == "failed"
    failure = db_session.scalar(select(IngestFailure).where(IngestFailure.message_id == bad))
    assert failure is not None and failure.attempts == 1
    assert "RuntimeError" in failure.error
    assert bad not in nxt.seen and len(nxt.seen) == 14


def test_failed_message_is_retried_on_the_next_sync_and_then_passes(db_session, user):
    legit = raw_fixtures("legit")
    bad = sorted(legit)[0]
    gmail = FakeGmailApi(legit)
    run_sync(user, db_session, gmail, make_filter_sink(db_session, NextStage(fail_on=[bad])))

    healthy = NextStage()
    retry = run_sync(user, db_session, gmail, make_filter_sink(db_session, healthy))

    assert retry.decisions == {"passed": 1}
    assert healthy.seen == [bad]
    assert decisions(db_session, user)[bad] == "passed"
    assert count(db_session, ProcessedMessage, user) == 15
    assert db_session.scalar(select(IngestFailure).where(IngestFailure.message_id == bad)) is None


def test_failure_attempts_are_bounded(db_session, user):
    legit = {k: v for k, v in raw_fixtures("legit").items() if k == "l01"}
    gmail = FakeGmailApi(legit)
    sink = make_filter_sink(db_session, NextStage(fail_on=["l01"]))
    for _ in range(MAX_RETRY_ATTEMPTS + 2):
        run_sync(user, db_session, gmail, sink)
    failure = db_session.scalar(select(IngestFailure))
    assert failure.attempts == MAX_RETRY_ATTEMPTS


# ---------- denylist ----------


def test_denylist_update_is_reflected_in_the_gmail_query(db_session, user):
    first = FakeGmailApi({})
    run_sync(user, db_session, first, make_filter_sink(db_session))
    assert "-from:spammy.example" not in next(c[1] for c in first.calls if c[0] == "list")

    settings = db_session.get(UserSettings, user.id)
    settings.denylist = ["linkedin.com", "Spammy.Example"]
    db_session.execute(text("UPDATE sync_state SET history_id = NULL"))
    db_session.commit()

    second = FakeGmailApi({})
    run_sync(user, db_session, second, make_filter_sink(db_session))
    queries = [c[1] for c in second.calls if c[0] == "list"]
    assert queries == [build_query(["linkedin.com", "spammy.example"])]
    assert "-from:spammy.example" in queries[0]


# ---------- privacy ----------

BODYISH = {"body", "text", "content", "snippet", "html", "raw", "payload", "message"}


def test_no_column_in_processed_messages_or_quarantine_can_hold_body_text(engine):
    insp = inspect(engine)
    processed = {c["name"] for c in insp.get_columns("processed_messages")}
    quarantine = {c["name"] for c in insp.get_columns("quarantine")}

    assert processed == {"user_id", "message_id", "decision", "reason", "processed_at"}
    assert quarantine == {
        "id",
        "user_id",
        "message_id",
        "subject",
        "sender",
        "score",
        "reasons",
        "status",
        "created_at",
    }
    assert not (BODYISH & (processed | quarantine))
    # the only free-text column in processed_messages is the short reason
    for col in insp.get_columns("processed_messages"):
        if col["name"] == "reason":
            assert col["type"].length == 255


def test_sample_rows_never_contain_the_body(db_session, user):
    marker = "ZXQ-UNIQUE-BODY-MARKER-9981"
    raw = raw_fixtures("scam")["s01"]
    parsed = parse_message(raw)
    object.__setattr__(parsed, "body_text", parsed.body_text + f" {marker}")
    sink = make_filter_sink(db_session, NextStage())
    assert sink(user, parsed) == "quarantined"
    db_session.commit()

    dumped = []
    for table in ("processed_messages", "quarantine"):
        rows = db_session.execute(text(f"SELECT * FROM {table}")).mappings().all()
        assert len(rows) == 1
        dumped.append(json.dumps(dict(rows[0]), default=str))
    assert marker not in " ".join(dumped)
    assert "Rs. 1499" not in " ".join(dumped)  # nor fragments of the original body


def test_logs_contain_ids_and_decisions_only(db_session, user, caplog):
    import logging

    raw = raw_fixtures("scam")["s01"]
    parsed = parse_message(raw)
    with caplog.at_level(logging.DEBUG):
        make_filter_sink(db_session)(user, parsed)
    assert "s01" in caplog.text and "quarantined" in caplog.text
    assert parsed.subject not in caplog.text
    assert "UPI" not in caplog.text


def test_processed_messages_rejects_unknown_decision(engine):
    with pytest.raises(Exception, match="ck_processed_messages_decision"):
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO users (email) VALUES ('x@example.test')"))
            conn.execute(
                text(
                    "INSERT INTO processed_messages (user_id, message_id, decision) "
                    "SELECT id, 'm', 'bogus' FROM users"
                )
            )
