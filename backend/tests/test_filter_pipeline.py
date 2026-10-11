"""Filter pipeline: one decision per message, failures isolated, nothing private stored."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, inspect, select, text

from app.filters import pipeline
from app.filters.pipeline import make_filter_sink
from app.ingest.mime import parse_message
from app.ingest.sync import MAX_RETRY_ATTEMPTS, run_sync
from app.models import IngestFailure, ProcessedMessage, Quarantine, Task, User, UserSettings
from tests.gmail_fakes import FakeGmailApi

FIXTURES = Path(__file__).parent / "fixtures" / "source_emails"
MANIFEST = json.loads((FIXTURES / "expected.manifest").read_text())
NOW = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)


def raw_fixtures() -> dict[str, dict]:
    out = {}
    for path in sorted(FIXTURES.glob("*.json")):
        raw = json.loads(path.read_text())
        out[raw["id"]] = raw
    return out


def ids_of(kind: str) -> set[str]:
    return {v["id"] for v in MANIFEST.values() if v["kind"] == kind}


@pytest.fixture
def user(db_session):
    u = User(email="me@example.test")
    db_session.add(u)
    db_session.flush()
    db_session.add(
        UserSettings(
            user_id=u.id,
            sources=[
                {"name": "NPTEL", "type": "nptel", "senders": ["nptel.ac.in"]},
                {"name": "Digii", "type": "digii", "senders": ["digii.example"]},
            ],
            block_senders=["blocked.example"],
        )
    )
    db_session.commit()
    return u


def sink_for(session, **kwargs):
    return make_filter_sink(session, lambda: NOW, **kwargs)


def decisions(session, user) -> dict[str, str]:
    rows = session.scalars(select(ProcessedMessage).where(ProcessedMessage.user_id == user.id))
    return {r.message_id: r.decision for r in rows}


def count(session, model, user) -> int:
    return session.scalar(select(func.count()).select_from(model).where(model.user_id == user.id))


# ---------- end to end over all fixtures ----------


def test_all_fixtures_through_a_sync_give_expected_decisions(db_session, user):
    result = run_sync(user, db_session, FakeGmailApi(raw_fixtures()), sink_for(db_session))

    assert result.failed == 0
    got = decisions(db_session, user)
    assert len(got) == len(MANIFEST)
    for entry in MANIFEST.values():
        expected = {
            "task": "passed",
            "review": "passed",
            "drop": "dropped",
            "ignore": "ignored",
            "suspicious": "quarantined",
        }[entry["kind"]]
        assert got[entry["id"]] == expected, entry["id"]

    tasks = {t.source_ref: t for t in db_session.scalars(select(Task))}
    assert set(tasks) == ids_of("task") | ids_of("review")
    assert {t.source_ref for t in tasks.values() if t.status == "needs_review"} == ids_of("review")
    held = {q.message_id for q in db_session.scalars(select(Quarantine))}
    assert held == ids_of("suspicious")
    for row in db_session.scalars(select(Quarantine)):
        assert row.score >= 4 and len(row.reasons) >= 1 and row.status == "held"


def test_tasks_carry_the_source_name_and_are_not_user_confirmed(db_session, user):
    run_sync(user, db_session, FakeGmailApi(raw_fixtures()), sink_for(db_session))
    by_name = {}
    for task in db_session.scalars(select(Task)):
        by_name.setdefault(task.source_name, []).append(task.source)
        assert task.user_confirmed is False
    assert set(by_name) == {"Digii", "NPTEL", "Hackathon"}
    assert set(by_name["Hackathon"]) == {"keyword"}
    assert set(by_name["Digii"]) == {"digii"}


# ---------- idempotency ----------


def test_processing_the_same_message_twice_leaves_one_row_each(db_session, user):
    fixtures = raw_fixtures()
    suspicious = parse_message(fixtures[sorted(ids_of("suspicious"))[0]])
    sink = sink_for(db_session)

    assert sink(user, suspicious) == "quarantined"
    assert sink(user, suspicious) == "skipped"
    db_session.commit()

    assert count(db_session, ProcessedMessage, user) == 1
    assert count(db_session, Quarantine, user) == 1


def test_a_second_sync_does_not_reprocess_or_duplicate(db_session, user):
    gmail = FakeGmailApi(raw_fixtures())
    run_sync(user, db_session, gmail, sink_for(db_session))
    tasks_before = count(db_session, Task, user)
    gmail.calls.clear()

    again = run_sync(user, db_session, gmail, sink_for(db_session))

    assert gmail.gets() == []  # handled mail is not even downloaded again
    assert again.delivered == 0
    assert count(db_session, ProcessedMessage, user) == len(MANIFEST)
    assert count(db_session, Task, user) == tasks_before


def test_reprocess_decides_again_and_clears_held_rows_that_are_no_longer_suspicious(
    db_session, user
):
    gmail = FakeGmailApi(raw_fixtures())
    run_sync(user, db_session, gmail, sink_for(db_session))
    held_before = count(db_session, Quarantine, user)
    assert held_before == len(ids_of("suspicious"))

    # The user adds the look-alike domain as a real source: that mail is no longer an impostor.
    settings = db_session.get(UserSettings, user.id)
    settings.sources = [
        *settings.sources,
        {"name": "Odd", "type": "custom", "senders": ["fakenptel.ac.in", "nptel-help.example"]},
    ]
    db_session.commit()

    run_sync(user, db_session, gmail, sink_for(db_session, reprocess=True), full=True)

    assert count(db_session, Quarantine, user) < held_before


def test_unique_constraint_blocks_a_second_quarantine_row(db_session, user):
    db_session.add(Quarantine(user_id=user.id, message_id="dup", reasons=[]))
    db_session.commit()
    db_session.add(Quarantine(user_id=user.id, message_id="dup", reasons=[]))
    with pytest.raises(Exception, match="uq_quarantine_user_id_message_id"):
        db_session.commit()
    db_session.rollback()


# ---------- failure isolation ----------


@pytest.fixture
def exploding(monkeypatch):
    """Make task creation fail for chosen message ids."""
    real = pipeline.ingest_message
    state = {"fail_on": set()}

    def boom(session, user, message, now, decision=None, **kw):
        if message.message_id in state["fail_on"]:
            raise RuntimeError("task stage exploded")
        return real(session, user, message, now, decision, **kw)

    monkeypatch.setattr(pipeline, "ingest_message", boom)
    return state


def test_exception_records_failed_and_the_batch_continues(db_session, user, exploding):
    task_ids = sorted(ids_of("task"))
    bad = task_ids[0]
    exploding["fail_on"] = {bad}
    result = run_sync(user, db_session, FakeGmailApi(raw_fixtures()), sink_for(db_session))

    assert result.failed == 1
    assert decisions(db_session, user)[bad] == "failed"
    failure = db_session.scalar(select(IngestFailure).where(IngestFailure.message_id == bad))
    assert failure is not None and failure.attempts == 1 and "RuntimeError" in failure.error
    created = {t.source_ref for t in db_session.scalars(select(Task))}
    assert bad not in created and set(task_ids[1:]) <= created


def test_failed_message_is_retried_on_the_next_sync_and_then_passes(db_session, user, exploding):
    bad = sorted(ids_of("task"))[0]
    exploding["fail_on"] = {bad}
    gmail = FakeGmailApi(raw_fixtures())
    run_sync(user, db_session, gmail, sink_for(db_session))

    exploding["fail_on"] = set()
    retry = run_sync(user, db_session, gmail, sink_for(db_session))

    assert retry.decisions == {"passed": 1}
    assert decisions(db_session, user)[bad] == "passed"
    assert db_session.scalar(select(IngestFailure).where(IngestFailure.message_id == bad)) is None
    assert db_session.scalar(select(Task).where(Task.source_ref == bad)) is not None


def test_failure_attempts_are_bounded(db_session, user, exploding):
    bad = sorted(ids_of("task"))[0]
    exploding["fail_on"] = {bad}
    gmail = FakeGmailApi({bad: raw_fixtures()[bad]})
    for _ in range(MAX_RETRY_ATTEMPTS + 2):
        run_sync(user, db_session, gmail, sink_for(db_session))
    failure = db_session.scalar(select(IngestFailure))
    assert failure.attempts == MAX_RETRY_ATTEMPTS


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
    for col in insp.get_columns("processed_messages"):
        if col["name"] == "reason":
            assert col["type"].length == 255


def test_sample_rows_never_contain_the_body(db_session, user):
    marker = "ZXQ-UNIQUE-BODY-MARKER-9981"
    raw = raw_fixtures()[sorted(ids_of("suspicious"))[1]]  # the fee/UPI hackathon mail
    parsed = parse_message(raw)
    object.__setattr__(parsed, "body_text", parsed.body_text + f" {marker}")
    assert sink_for(db_session)(user, parsed) == "quarantined"
    db_session.commit()

    dumped = []
    for table in ("processed_messages", "quarantine"):
        rows = db_session.execute(text(f"SELECT * FROM {table}")).mappings().all()
        assert len(rows) == 1
        dumped.append(json.dumps(dict(rows[0]), default=str))
    assert marker not in " ".join(dumped)
    assert "9876543210" not in " ".join(dumped)  # nor fragments of the original body


def test_logs_contain_ids_and_decisions_only(db_session, user, caplog):
    raw = raw_fixtures()[sorted(ids_of("suspicious"))[1]]
    parsed = parse_message(raw)
    with caplog.at_level(logging.DEBUG):
        sink_for(db_session)(user, parsed)
    assert parsed.message_id in caplog.text and "quarantined" in caplog.text
    assert parsed.subject not in caplog.text
    assert "UPI" not in caplog.text and "9876543210" not in caplog.text


def test_processed_messages_accepts_dropped_and_rejects_unknown_decision(engine):
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (email) VALUES ('x@example.test')"))
        conn.execute(
            text(
                "INSERT INTO processed_messages (user_id, message_id, decision) "
                "SELECT id, 'm', 'dropped' FROM users"
            )
        )
    with pytest.raises(Exception, match="ck_processed_messages_decision"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO processed_messages (user_id, message_id, decision) "
                    "SELECT id, 'm2', 'bogus' FROM users"
                )
            )
