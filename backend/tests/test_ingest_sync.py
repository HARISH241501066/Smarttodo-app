import logging
from datetime import UTC, datetime, timedelta

import pytest
from googleapiclient.errors import HttpError
from sqlalchemy import select, text

from app.filters.pipeline import record_decision
from app.ingest.gmail import RateLimited
from app.ingest.query import build_query
from app.ingest.sync import (
    LOCK_NAMESPACE,
    MAX_RETRY_ATTEMPTS,
    SyncInProgress,
    get_or_create_settings,
    run_sync,
)
from app.models import IngestFailure, SyncState, Task, User, UserSettings
from app.sources import DEFAULT_KEYWORDS, DEFAULT_SOURCES, load_sources
from tests.gmail_fakes import FakeGmailApi, fixtures_by_number
from tests.test_ingest_gmail import http_error


@pytest.fixture
def user(db_session):
    u = User(email="me@example.test")
    db_session.add(u)
    db_session.commit()
    return u


class Collector:
    """A sink that records delivered message ids (and, like the real sink, the decision)."""

    def __init__(self, fail_on=(), record=False, session=None):
        self.ids: list[str] = []
        self.fail_on = set(fail_on)
        self.record = record
        self.session = session

    def __call__(self, user, message):
        if message.message_id in self.fail_on:
            raise ValueError("sink exploded")
        self.ids.append(message.message_id)
        if self.record:
            record_decision(self.session, user.id, message.message_id, "dropped", "test")


def failures(session, user) -> dict[str, IngestFailure]:
    rows = session.scalars(select(IngestFailure).where(IngestFailure.user_id == user.id))
    return {r.message_id: r for r in rows}


def queries_of(api) -> list[str]:
    return [c[1] for c in api.calls if c[0] == "list"]


# ---------- the query ----------


def test_first_sync_looks_back_30_days_and_creates_default_settings(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1, 2, 3))
    sink = Collector()

    result = run_sync(user, db_session, api, sink)

    assert result.mode == "backfill"
    assert (result.discovered, result.fetched, result.delivered) == (3, 3, 3)
    assert sink.ids == ["msg001", "msg002", "msg003"]
    assert db_session.get(SyncState, user.id).last_success_at is not None
    settings = db_session.get(UserSettings, user.id)
    assert settings.watch_keywords == DEFAULT_KEYWORDS
    assert [s["name"] for s in settings.sources] == [s["name"] for s in DEFAULT_SOURCES]
    assert queries_of(api) == [
        "newer_than:30d -in:spam -in:trash (from:nptel.ac.in OR hackathon OR ideathon OR datathon)"
    ]


def test_query_holds_only_source_senders_and_keywords(db_session, user):
    db_session.add(
        UserSettings(
            user_id=user.id,
            sources=[
                {"name": "NPTEL", "type": "nptel", "senders": ["Nptel.ac.in"]},
                {"name": "Digii", "type": "digii", "senders": ["digiicampus.example", "a@b.test"]},
                {"name": "Bad", "type": "custom", "senders": ["x OR in:anywhere", "-in:inbox"]},
            ],
            watch_keywords=["Hackathon", "smart india", "evil) OR (from:boss@work.test"],
            block_senders=["spam.test"],
        )
    )
    db_session.commit()
    api = FakeGmailApi(fixtures_by_number(1))
    run_sync(user, db_session, api, Collector())
    query = queries_of(api)[0]
    assert query == (
        "newer_than:30d -in:spam -in:trash (from:nptel.ac.in OR from:digiicampus.example OR "
        'from:a@b.test OR hackathon OR "smart india")'
    )
    for absent in ("category", "promotions", "inbox", "spam.test", "boss"):
        assert absent not in query


def test_nothing_to_look_for_means_nothing_is_fetched(db_session, user):
    db_session.add(UserSettings(user_id=user.id, sources=[], watch_keywords=[]))
    db_session.commit()
    api = FakeGmailApi(fixtures_by_number(1, 2))
    sink = Collector()
    result = run_sync(user, db_session, api, sink)
    assert api.calls == [] and sink.ids == [] and result.discovered == 0
    assert result.query is None


def test_build_query_is_none_without_senders_or_keywords():
    assert (
        build_query(load_sources([{"name": "Digii", "type": "digii", "senders": []}]), []) is None
    )


def test_get_or_create_settings_is_stable(db_session, user):
    first = get_or_create_settings(db_session, user.id)
    again = get_or_create_settings(db_session, user.id)
    assert first is again


def test_backfill_follows_pagination(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1, 2, 3, 4, 5), page_size=2)
    sink = Collector()
    result = run_sync(user, db_session, api, sink)
    assert [c[2] for c in api.calls if c[0] == "list"] == [None, "2", "4"]
    assert sorted(sink.ids) == ["msg001", "msg002", "msg003", "msg004", "msg005"]
    assert result.delivered == 5


def test_spam_is_never_delivered_even_if_listed(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1, 15, 16))  # 16 has the SPAM label
    sink = Collector()
    result = run_sync(user, db_session, api, sink)
    assert "msg016" not in sink.ids and "msg001" in sink.ids
    assert result.skipped >= 1


def test_message_deleted_before_fetch_is_skipped_not_failed(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1), listing=["msg001", "ghost"])
    result = run_sync(user, db_session, api, Collector())
    assert (result.delivered, result.skipped, result.failed) == (1, 1, 0)
    assert failures(db_session, user) == {}


# ---------- repeat syncs ----------


def test_second_sync_skips_handled_mail_before_fetching(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1, 2, 3))
    sink = Collector(record=True, session=db_session)
    run_sync(user, db_session, api, sink)
    db_session.commit()
    api.calls.clear()

    second = run_sync(user, db_session, api, sink)

    assert api.gets() == []  # nothing downloaded again
    assert (second.discovered, second.fetched, second.delivered, second.skipped) == (3, 0, 0, 3)
    assert sorted(sink.ids) == ["msg001", "msg002", "msg003"]  # each exactly once


def test_new_mail_is_the_only_thing_fetched_next_time(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1, 2, 3, 4), listing=["msg001", "msg002", "msg003"])
    sink = Collector(record=True, session=db_session)
    run_sync(user, db_session, api, sink)
    db_session.commit()
    api.listing.append("msg004")
    api.calls.clear()
    result = run_sync(user, db_session, api, sink)
    assert api.gets() == ["msg004"] and result.delivered == 1


def test_full_run_handles_mail_that_was_already_decided(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1, 2))
    sink = Collector(record=True, session=db_session)
    run_sync(user, db_session, api, sink)
    db_session.commit()
    result = run_sync(user, db_session, api, sink, full=True)
    assert result.delivered == 2 and result.mode == "backfill"


def test_later_syncs_look_back_only_as_far_as_needed(db_session, user):
    db_session.add(
        SyncState(user_id=user.id, last_success_at=datetime.now(UTC) - timedelta(hours=3))
    )
    db_session.commit()
    api = FakeGmailApi(fixtures_by_number(1))
    result = run_sync(user, db_session, api, Collector())
    assert result.mode == "incremental"
    assert queries_of(api)[0].startswith("newer_than:3d ")  # 1 day + 2 days of overlap


def test_a_long_gap_is_capped_at_30_days(db_session, user):
    db_session.add(
        SyncState(user_id=user.id, last_success_at=datetime.now(UTC) - timedelta(days=90))
    )
    db_session.commit()
    api = FakeGmailApi(fixtures_by_number(1))
    run_sync(user, db_session, api, Collector())
    assert queries_of(api)[0].startswith("newer_than:30d ")


def test_api_error_on_listing_aborts_and_records_nothing(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1))
    api.list_error = http_error(500)
    with pytest.raises(HttpError):
        run_sync(user, db_session, api, Collector())
    assert db_session.get(SyncState, user.id).last_success_at is None
    assert failures(db_session, user) == {}


# ---------- per-message isolation ----------


def test_failing_messages_land_in_ingest_failures_and_batch_completes(db_session, user):
    api = FakeGmailApi(
        fixtures_by_number(1, 2, 3, 4, 5, 11),
        listing=["msg001", "msg002", "msg003", "msg011", "msg004", "msg005"],
    )
    api.get_errors["msg003"] = RuntimeError("boom")  # fetch blows up
    sink = Collector(fail_on={"msg004"})  # sink blows up
    # msg011 has corrupt base64: the parser blows up

    result = run_sync(user, db_session, api, sink)

    assert sink.ids == ["msg001", "msg002", "msg005"]  # the rest of the batch completed
    assert (result.delivered, result.failed) == (3, 3)
    rows = failures(db_session, user)
    assert set(rows) == {"msg003", "msg004", "msg011"}
    assert rows["msg003"].error.startswith("RuntimeError")
    assert rows["msg004"].error.startswith("ValueError")
    assert rows["msg011"].error.startswith("MessageParseError")
    assert all(r.attempts == 1 and r.user_id == user.id for r in rows.values())
    assert db_session.get(SyncState, user.id).last_success_at is not None  # run still finished


def test_failure_messages_never_contain_email_content(db_session, user):
    api = FakeGmailApi(fixtures_by_number(11))
    run_sync(user, db_session, api, Collector())
    error = failures(db_session, user)["msg011"].error
    assert len(error) <= 200 and "QUJDR" not in error


def test_failed_message_is_retried_next_sync_and_failure_cleared(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1, 2))
    api.get_errors["msg002"] = RuntimeError("temporary")
    sink = Collector(record=True, session=db_session)
    run_sync(user, db_session, api, sink)
    assert set(failures(db_session, user)) == {"msg002"}

    del api.get_errors["msg002"]
    result = run_sync(user, db_session, api, sink)

    assert result.delivered == 1
    assert sink.ids == ["msg001", "msg002"]
    assert failures(db_session, user) == {}


def test_persistent_failure_counts_attempts_and_stops_after_the_limit(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1, 2))
    api.get_errors["msg002"] = RuntimeError("always")
    sink = Collector(record=True, session=db_session)
    for expected in range(1, MAX_RETRY_ATTEMPTS + 1):
        run_sync(user, db_session, api, sink)
        assert failures(db_session, user)["msg002"].attempts == expected

    api.calls.clear()
    run_sync(user, db_session, api, sink)  # past the limit: no more retries
    assert "msg002" not in api.gets()
    assert failures(db_session, user)["msg002"].attempts == MAX_RETRY_ATTEMPTS
    assert sink.ids == ["msg001"]  # never duplicated


def test_retried_message_that_was_deleted_is_cleaned_up(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1, 2))
    api.get_errors["msg002"] = RuntimeError("temporary")
    run_sync(user, db_session, api, Collector())
    del api.get_errors["msg002"]
    del api.messages["msg002"]  # now gone
    run_sync(user, db_session, api, Collector())
    assert failures(db_session, user) == {}


def test_failing_sink_rolls_back_its_own_writes_only(db_session, user):
    def sink(u, message):
        db_session.add(Task(user_id=u.id, title="t", source_ref=message.message_id))
        db_session.flush()
        if message.message_id == "msg002":
            raise ValueError("after writing")

    api = FakeGmailApi(fixtures_by_number(1, 2, 3))
    result = run_sync(user, db_session, api, sink)

    refs = set(db_session.scalars(select(Task.source_ref)))
    assert refs == {"msg001", "msg003"}  # msg002's partial write was rolled back
    assert (result.delivered, result.failed) == (2, 1)
    assert set(failures(db_session, user)) == {"msg002"}


# ---------- rate limiting ----------


def test_rate_limit_defers_the_rest_without_failing_or_losing_anything(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1, 2, 3, 4, 5))
    api.get_errors["msg003"] = RateLimited("quota")
    sink = Collector(record=True, session=db_session)

    result = run_sync(user, db_session, api, sink)

    assert sink.ids == ["msg001", "msg002"]
    assert (result.delivered, result.failed, result.deferred) == (2, 0, 3)
    assert result.rate_limited is True
    assert api.gets() == ["msg001", "msg002", "msg003"]  # stopped hammering at the first 429
    rows = failures(db_session, user)
    assert set(rows) == {"msg003", "msg004", "msg005"}
    assert all(r.attempts == 0 and "deferred" in r.error for r in rows.values())
    assert db_session.get(SyncState, user.id).last_success_at is None  # not a clean run

    del api.get_errors["msg003"]
    second = run_sync(user, db_session, api, sink)
    assert second.deferred == 0
    assert sink.ids == ["msg001", "msg002", "msg003", "msg004", "msg005"]  # each exactly once
    assert failures(db_session, user) == {}


def test_repeated_rate_limiting_never_consumes_retry_attempts(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1, 2))
    api.get_errors["msg001"] = RateLimited("quota")
    for _ in range(MAX_RETRY_ATTEMPTS + 2):
        result = run_sync(user, db_session, api, Collector())
        assert result.deferred == 2
    assert {r.attempts for r in failures(db_session, user).values()} == {0}


def test_deferred_message_that_then_really_fails_starts_counting_attempts(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1))
    api.get_errors["msg001"] = RateLimited("quota")
    run_sync(user, db_session, api, Collector())
    api.get_errors["msg001"] = RuntimeError("real problem")
    run_sync(user, db_session, api, Collector())
    assert failures(db_session, user)["msg001"].attempts == 1


def test_rate_limit_while_listing_aborts_and_leaves_cursor_alone(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1))
    api.list_error = RateLimited("quota")
    with pytest.raises(RateLimited):
        run_sync(user, db_session, api, Collector())
    assert db_session.get(SyncState, user.id).last_success_at is None
    assert failures(db_session, user) == {}


# ---------- locking ----------


def _lock_key(user_id: int) -> int:
    return LOCK_NAMESPACE * 2**32 + user_id % 2**32


def test_concurrent_sync_for_same_user_is_rejected(db_session, user, engine):
    api = FakeGmailApi(fixtures_by_number(1))
    with engine.connect() as other:
        other.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _lock_key(user.id)})
        with pytest.raises(SyncInProgress):
            run_sync(user, db_session, api, Collector())
        assert api.calls == []  # nothing happened
        other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _lock_key(user.id)})
    assert run_sync(user, db_session, api, Collector()).delivered == 1


def test_lock_is_released_even_when_sync_aborts(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1))
    api.list_error = http_error(500)
    with pytest.raises(HttpError):
        run_sync(user, db_session, api, Collector())
    api.list_error = None
    assert run_sync(user, db_session, api, Collector()).delivered == 1


# ---------- privacy ----------


def test_logs_contain_ids_and_counts_but_never_content(db_session, user, caplog):
    api = FakeGmailApi(fixtures_by_number(1, 2, 3, 4, 5, 8, 11))
    api.get_errors["msg005"] = RuntimeError("boom")
    with caplog.at_level(logging.DEBUG, logger="app"):
        run_sync(user, db_session, api, Collector())

    text_logged = caplog.text
    assert "msg001" in text_logged and "delivered=" in text_logged
    for secret in (
        "NPTEL", "Assignment", "internship", "noreply@", "example.test",
        "portal", "tomorrow", "Hall ticket", "docs.google.com", "SYNTH123",
    ):  # fmt: skip
        assert secret not in text_logged, secret
