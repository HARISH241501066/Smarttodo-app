import logging

import pytest
from googleapiclient.errors import HttpError
from sqlalchemy import select, text

from app.ingest.gmail import HistoryExpired, RateLimited
from app.ingest.query import BASE_QUERY, DEFAULT_DENYLIST, build_query
from app.ingest.sync import (
    LOCK_NAMESPACE,
    MAX_RETRY_ATTEMPTS,
    SyncInProgress,
    run_sync,
)
from app.models import IngestFailure, SyncState, Task, User, UserSettings
from tests.gmail_fakes import FakeGmailApi, fixtures_by_number, history_page
from tests.test_ingest_gmail import http_error


@pytest.fixture
def user(db_session):
    u = User(email="me@example.test")
    db_session.add(u)
    db_session.commit()
    return u


class Collector:
    """A sink that records delivered message ids."""

    def __init__(self, fail_on=()):
        self.ids: list[str] = []
        self.fail_on = set(fail_on)

    def __call__(self, user, message):
        if message.message_id in self.fail_on:
            raise ValueError("sink exploded")
        self.ids.append(message.message_id)


def set_history_id(session, user, value):
    session.add(SyncState(user_id=user.id, history_id=value))
    session.commit()


def failures(session, user) -> dict[str, IngestFailure]:
    rows = session.scalars(select(IngestFailure).where(IngestFailure.user_id == user.id))
    return {r.message_id: r for r in rows}


# ---------- backfill ----------


def test_first_sync_is_a_backfill_that_stores_the_cursor(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1, 2, 3))
    sink = Collector()

    result = run_sync(user, db_session, api, sink)

    assert result.mode == "backfill" and result.history_reset is False
    assert (result.discovered, result.fetched, result.delivered) == (3, 3, 3)
    assert sink.ids == ["msg001", "msg002", "msg003"]
    state = db_session.get(SyncState, user.id)
    assert state.history_id == "1000" and state.last_success_at is not None
    assert result.history_id == "1000"


def test_backfill_captures_cursor_before_listing(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1, 2))
    run_sync(user, db_session, api, Collector())
    kinds = [c[0] for c in api.calls]
    assert kinds.index("profile") < kinds.index("list")


def test_backfill_uses_default_denylist_and_creates_settings_row(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1))
    run_sync(user, db_session, api, Collector())
    queries = [c[1] for c in api.calls if c[0] == "list"]
    assert queries == [build_query(DEFAULT_DENYLIST)]
    assert "-from:linkedin.com" in queries[0] and "-from:naukri.com" in queries[0]
    assert db_session.get(UserSettings, user.id).denylist == list(DEFAULT_DENYLIST)


def test_backfill_honours_users_custom_denylist(db_session, user):
    db_session.add(UserSettings(user_id=user.id, denylist=["Foo.com", "x OR in:inbox", 7]))
    db_session.commit()
    api = FakeGmailApi(fixtures_by_number(1))
    run_sync(user, db_session, api, Collector())
    query = next(c[1] for c in api.calls if c[0] == "list")
    assert query == f"{BASE_QUERY} -from:foo.com"


def test_user_who_cleared_denylist_gets_no_from_clauses(db_session, user):
    db_session.add(UserSettings(user_id=user.id, denylist=[]))
    db_session.commit()
    api = FakeGmailApi(fixtures_by_number(1))
    run_sync(user, db_session, api, Collector())
    assert next(c[1] for c in api.calls if c[0] == "list") == BASE_QUERY


def test_backfill_follows_pagination(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1, 2, 3, 4, 5), page_size=2)
    sink = Collector()
    result = run_sync(user, db_session, api, sink)
    assert [c[2] for c in api.calls if c[0] == "list"] == [None, "2", "4"]
    assert sorted(sink.ids) == ["msg001", "msg002", "msg003", "msg004", "msg005"]
    assert result.delivered == 5


def test_backfill_post_filter_is_a_safety_net(db_session, user):
    # 15 = promotions label, 16 = spam label, 17/21 = denylisted senders, 24 = subdomain of one.
    api = FakeGmailApi(fixtures_by_number(1, 15, 16, 17, 21, 24))
    sink = Collector()
    result = run_sync(user, db_session, api, sink)
    assert sink.ids == ["msg001"]
    assert (result.delivered, result.skipped) == (1, 5)


def test_message_deleted_before_fetch_is_skipped_not_failed(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1), listing=["msg001", "ghost"])
    result = run_sync(user, db_session, api, Collector())
    assert (result.delivered, result.skipped, result.failed) == (1, 1, 0)
    assert failures(db_session, user) == {}


# ---------- incremental ----------


def test_second_sync_is_incremental_and_creates_no_duplicates(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1, 2, 3))
    sink = Collector()

    first = run_sync(user, db_session, api, sink)
    api.history_now = "1001"
    second = run_sync(user, db_session, api, sink)
    third = run_sync(user, db_session, api, sink)

    assert first.mode == "backfill" and second.mode == "incremental"
    assert second.delivered == 0 and third.delivered == 0
    assert sorted(sink.ids) == ["msg001", "msg002", "msg003"]  # each exactly once
    assert len(sink.ids) == len(set(sink.ids))
    assert [c for c in api.calls if c[0] == "history"][0][1] == "1000"
    assert db_session.get(SyncState, user.id).history_id == "1001"
    assert sum(1 for c in api.calls if c[0] == "list") == 1  # only the first run listed


def test_incremental_picks_up_only_new_messages(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1, 2, 3))
    sink = Collector()
    run_sync(user, db_session, api, sink)

    api.messages.update(fixtures_by_number(4))
    api.history_pages = [history_page(["msg004"], "1010")]
    result = run_sync(user, db_session, api, sink)

    assert result.mode == "incremental" and result.delivered == 1
    assert sink.ids == ["msg001", "msg002", "msg003", "msg004"]
    assert db_session.get(SyncState, user.id).history_id == "1010"


def test_incremental_dedupes_repeated_ids_and_follows_pagination(db_session, user):
    set_history_id(db_session, user, "500")
    api = FakeGmailApi(fixtures_by_number(1, 2, 3))
    api.history_pages = [
        history_page(["msg001", "msg002", "msg001"], "600"),
        history_page(["msg002", "msg003"], "700"),
    ]
    sink = Collector()
    result = run_sync(user, db_session, api, sink)

    assert api.gets() == ["msg001", "msg002", "msg003"]  # one fetch per unique id
    assert sink.ids == ["msg001", "msg002", "msg003"]
    assert [c[2] for c in api.calls if c[0] == "history"] == [None, "1"]
    assert result.history_id == "700"
    assert not any(c[0] in ("profile", "list") for c in api.calls)


def test_incremental_skips_excluded_labels_without_fetching(db_session, user):
    set_history_id(db_session, user, "500")
    api = FakeGmailApi(fixtures_by_number(1, 15, 16, 17, 24))
    api.history_pages = [
        history_page(
            [
                ("msg001", ["INBOX"]),
                ("msg015", ["INBOX", "CATEGORY_PROMOTIONS"]),
                ("msg016", ["SPAM"]),
                ("msg017", ["INBOX"]),  # linkedin: only knowable after fetching
                ("msg024", ["INBOX"]),  # linkedin subdomain
            ],
            "600",
        )
    ]
    sink = Collector()
    result = run_sync(user, db_session, api, sink)

    assert sink.ids == ["msg001"]
    assert "msg015" not in api.gets() and "msg016" not in api.gets()
    assert {"msg017", "msg024"} <= set(api.gets())
    assert (result.delivered, result.skipped) == (1, 4)


# ---------- expired history ----------


def test_history_404_falls_back_to_backfill(db_session, user):
    set_history_id(db_session, user, "500")
    api = FakeGmailApi(fixtures_by_number(1, 2), history_id="2000")
    api.history_error = HistoryExpired("500")
    sink = Collector()

    result = run_sync(user, db_session, api, sink)

    assert result.mode == "backfill" and result.history_reset is True
    assert sink.ids == ["msg001", "msg002"]
    kinds = [c[0] for c in api.calls]
    assert kinds[0] == "history" and kinds.index("profile") < kinds.index("list")
    assert db_session.get(SyncState, user.id).history_id == "2000"  # reset to the fresh cursor


def test_history_404_from_real_wrapper_falls_back_to_backfill(db_session, user):
    """End to end through GoogleGmailApi: HttpError 404 -> HistoryExpired -> backfill."""
    from tests.test_ingest_gmail import make_api

    set_history_id(db_session, user, "500")
    api, service, _ = make_api(
        [
            http_error(404),  # history.list
            {"historyId": "3000"},  # getProfile
            {"messages": [{"id": "msg001"}]},  # messages.list
            fixtures_by_number(1)["msg001"],  # messages.get
        ]
    )
    sink = Collector()
    result = run_sync(user, db_session, api, sink)
    assert result.history_reset and sink.ids == ["msg001"]
    assert db_session.get(SyncState, user.id).history_id == "3000"


def test_api_error_on_listing_aborts_without_touching_cursor(db_session, user):
    set_history_id(db_session, user, "500")
    api = FakeGmailApi(fixtures_by_number(1))
    api.history_error = http_error(500)
    with pytest.raises(HttpError):
        run_sync(user, db_session, api, Collector())
    assert db_session.get(SyncState, user.id).history_id == "500"
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
    assert db_session.get(SyncState, user.id).history_id == "1000"  # run still finished


def test_failure_messages_never_contain_email_content(db_session, user):
    api = FakeGmailApi(fixtures_by_number(11))
    run_sync(user, db_session, api, Collector())
    error = failures(db_session, user)["msg011"].error
    assert len(error) <= 200 and "QUJDR" not in error


def test_failed_message_is_retried_next_sync_and_failure_cleared(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1, 2))
    api.get_errors["msg002"] = RuntimeError("temporary")
    sink = Collector()
    run_sync(user, db_session, api, sink)
    assert set(failures(db_session, user)) == {"msg002"}

    del api.get_errors["msg002"]
    result = run_sync(user, db_session, api, sink)

    assert result.mode == "incremental" and result.delivered == 1
    assert sink.ids == ["msg001", "msg002"]
    assert failures(db_session, user) == {}


def test_persistent_failure_counts_attempts_and_stops_after_the_limit(db_session, user):
    api = FakeGmailApi(fixtures_by_number(1, 2))
    api.get_errors["msg002"] = RuntimeError("always")
    sink = Collector()
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
    sink = Collector()

    result = run_sync(user, db_session, api, sink)

    assert sink.ids == ["msg001", "msg002"]
    assert (result.delivered, result.failed, result.deferred) == (2, 0, 3)
    assert result.rate_limited is True
    assert api.gets() == ["msg001", "msg002", "msg003"]  # stopped hammering at the first 429
    rows = failures(db_session, user)
    assert set(rows) == {"msg003", "msg004", "msg005"}
    assert all(r.attempts == 0 and "deferred" in r.error for r in rows.values())
    assert db_session.get(SyncState, user.id).history_id == "1000"  # cursor still advances

    del api.get_errors["msg003"]
    second = run_sync(user, db_session, api, sink)
    assert second.mode == "incremental" and second.deferred == 0
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
    assert db_session.get(SyncState, user.id).history_id is None
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
