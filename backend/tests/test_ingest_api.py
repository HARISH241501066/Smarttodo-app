import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.api.sync import get_gmail_api
from app.auth.credentials import ReauthRequired
from app.db import get_session
from app.ingest.gmail import RateLimited
from app.ingest.sync import LOCK_NAMESPACE
from app.main import app
from app.security import sign
from tests.gmail_fakes import FakeGmailApi, fixtures_by_number
from tests.test_ingest_gmail import http_error


@pytest.fixture
def client(engine):
    def override_session():
        with sessionmaker(engine, expire_on_commit=False)() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    with TestClient(app, follow_redirects=False) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def user_id(engine):
    with engine.begin() as conn:
        return conn.execute(
            text("INSERT INTO users (email) VALUES ('me@example.test') RETURNING id")
        ).scalar_one()


@pytest.fixture
def logged_in(client, user_id):
    client.cookies.set("session", sign({"uid": user_id}, "session"))
    return client


def use_gmail(fake):
    app.dependency_overrides[get_gmail_api] = lambda: fake


def test_sync_requires_login(client):
    assert client.post("/sync/run").status_code == 401


def test_sync_run_returns_counts_and_persists_cursor(logged_in, engine, user_id):
    use_gmail(FakeGmailApi(fixtures_by_number(1, 2, 3, 15, 17)))
    resp = logged_in.post("/sync/run")
    assert resp.status_code == 200
    assert resp.json() == {
        "mode": "backfill",
        "history_reset": False,
        "discovered": 5,
        "fetched": 5,
        "delivered": 3,
        "passed": 2,
        "quarantined": 1,  # fixture 1: display name "NPTEL" on a non-NPTEL domain
        "ignored": 0,
        "already_processed": 0,
        "skipped": 2,
        "failed": 0,
        "deferred": 0,
        "rate_limited": False,
    }
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT history_id, last_success_at FROM sync_state WHERE user_id = :u"),
            {"u": user_id},
        ).one()
    assert row.history_id == "1000" and row.last_success_at is not None


def test_running_twice_is_incremental_the_second_time(logged_in):
    use_gmail(FakeGmailApi(fixtures_by_number(1, 2, 3)))
    assert logged_in.post("/sync/run").json()["mode"] == "backfill"
    second = logged_in.post("/sync/run").json()
    assert second["mode"] == "incremental" and second["delivered"] == 0


def test_response_never_includes_email_content(logged_in):
    use_gmail(FakeGmailApi(fixtures_by_number(1, 2, 3)))
    body = logged_in.post("/sync/run").text
    for secret in ("NPTEL", "Assignment", "internship", "example.test"):
        assert secret not in body


def test_expired_google_access_returns_401(logged_in):
    def needs_reauth():
        raise ReauthRequired("revoked")

    app.dependency_overrides[get_gmail_api] = needs_reauth
    resp = logged_in.post("/sync/run")
    assert resp.status_code == 401
    assert "sign in again" in resp.json()["detail"]


def test_gmail_api_failure_returns_502(logged_in):
    fake = FakeGmailApi(fixtures_by_number(1))
    fake.list_error = http_error(500)
    use_gmail(fake)
    assert logged_in.post("/sync/run").status_code == 502


def test_concurrent_sync_returns_409(logged_in, engine, user_id):
    use_gmail(FakeGmailApi(fixtures_by_number(1)))
    key = LOCK_NAMESPACE * 2**32 + user_id % 2**32
    with engine.connect() as other:
        other.execute(text("SELECT pg_advisory_lock(:k)"), {"k": key})
        assert logged_in.post("/sync/run").status_code == 409
        other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
    assert logged_in.post("/sync/run").status_code == 200


def test_sync_only_touches_the_logged_in_users_state(logged_in, engine, user_id):
    with engine.begin() as conn:
        other = conn.execute(
            text("INSERT INTO users (email) VALUES ('other@example.test') RETURNING id")
        ).scalar_one()
    use_gmail(FakeGmailApi(fixtures_by_number(1)))
    logged_in.post("/sync/run")
    with engine.connect() as conn:
        users_with_state = set(conn.execute(text("SELECT user_id FROM sync_state")).scalars())
    assert users_with_state == {user_id} and other not in users_with_state


def test_rate_limited_sync_reports_deferred_messages(logged_in):
    fake = FakeGmailApi(fixtures_by_number(1, 2, 3))
    fake.get_errors["msg002"] = RateLimited("quota")
    use_gmail(fake)
    body = logged_in.post("/sync/run").json()
    assert (body["delivered"], body["deferred"], body["rate_limited"]) == (1, 2, True)


def test_rate_limit_while_listing_returns_429(logged_in):
    fake = FakeGmailApi(fixtures_by_number(1))
    fake.list_error = RateLimited("quota")
    use_gmail(fake)
    assert logged_in.post("/sync/run").status_code == 429
