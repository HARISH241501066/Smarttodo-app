import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from cryptography.fernet import Fernet, InvalidToken
from fastapi.testclient import TestClient
from freezegun import freeze_time
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.auth import credentials, google
from app.auth.credentials import ReauthRequired, load_credentials
from app.auth.google import CALENDAR_EVENTS, GMAIL_READONLY, GoogleLogin
from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models import User
from app.security import decrypt_token, encrypt_token, sign, unsign

PLAIN_REFRESH = "1//0g-plaintext-refresh-token"
ALL_SCOPES = frozenset(
    {"openid", "https://www.googleapis.com/auth/userinfo.email", GMAIL_READONLY, CALENDAR_EVENTS}
)
REVOKE_URL = "https://oauth2.googleapis.com/revoke"


@pytest.fixture
def client(engine):
    def override():
        with sessionmaker(engine, expire_on_commit=False)() as session:
            yield session

    app.dependency_overrides[get_session] = override
    with TestClient(app, follow_redirects=False) as c:
        yield c
    app.dependency_overrides.clear()


def _start_login(client) -> tuple[str, dict]:
    resp = client.get("/auth/login")
    assert resp.status_code == 302
    query = parse_qs(urlparse(resp.headers["location"]).query)
    return query["state"][0], query


def _sign_in(
    client, monkeypatch, email="me@example.test", refresh=PLAIN_REFRESH, scopes=ALL_SCOPES
):
    state, _ = _start_login(client)
    monkeypatch.setattr(
        google,
        "exchange_code",
        lambda code, verifier: GoogleLogin(
            email=email, refresh_token=refresh, granted_scopes=frozenset(scopes)
        ),
    )
    return client.get("/auth/callback", params={"code": "abc", "state": state})


def _stored_token(engine, email="me@example.test"):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT encrypted_refresh_token FROM users WHERE email = :e"), {"e": email}
        ).scalar_one_or_none()


def _count(engine, table, where="true", **params):
    with engine.connect() as conn:
        return conn.execute(
            text(f"SELECT count(*) FROM {table} WHERE {where}"), params
        ).scalar_one()


# ---------- /auth/login ----------


def test_login_redirects_to_google_with_required_parameters(client):
    resp = client.get("/auth/login")
    assert resp.status_code == 302
    url = urlparse(resp.headers["location"])
    assert url.netloc == "accounts.google.com"
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert q["client_id"] == get_settings().google_client_id
    assert q["redirect_uri"] == "http://localhost:8000/auth/callback"
    assert q["response_type"] == "code"
    assert q["access_type"] == "offline"
    assert q["prompt"] == "consent"
    assert set(q["scope"].split()) == ALL_SCOPES
    assert len(q["state"]) >= 32
    assert q["code_challenge_method"] == "S256"
    set_cookie = resp.headers["set-cookie"].lower()
    assert (
        "oauth_state=" in set_cookie and "httponly" in set_cookie and "samesite=lax" in set_cookie
    )


def test_login_state_is_unique_per_request(client):
    assert _start_login(client)[0] != _start_login(client)[0]


def test_pkce_verifier_matches_challenge(client, monkeypatch):
    state, query = _start_login(client)
    seen = {}

    def fake_exchange(code, verifier):
        seen["verifier"] = verifier
        return GoogleLogin("me@example.test", PLAIN_REFRESH, ALL_SCOPES)

    monkeypatch.setattr(google, "exchange_code", fake_exchange)
    client.get("/auth/callback", params={"code": "abc", "state": state})
    digest = hashlib.sha256(seen["verifier"].encode()).digest()
    assert base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == query["code_challenge"][0]


# ---------- callback state / CSRF ----------


def _exchange_must_not_run(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("code exchange must not run when state is invalid")

    monkeypatch.setattr(google, "exchange_code", boom)


def test_callback_without_state_returns_400(client, monkeypatch):
    _start_login(client)
    _exchange_must_not_run(monkeypatch)
    assert client.get("/auth/callback", params={"code": "abc"}).status_code == 400


def test_callback_without_any_params_returns_400(client, monkeypatch):
    _exchange_must_not_run(monkeypatch)
    assert client.get("/auth/callback").status_code == 400


def test_callback_without_state_cookie_returns_400(client, monkeypatch):
    _exchange_must_not_run(monkeypatch)
    resp = client.get("/auth/callback", params={"code": "abc", "state": "whatever"})
    assert resp.status_code == 400


def test_callback_with_wrong_state_returns_400(client, monkeypatch):
    _start_login(client)
    _exchange_must_not_run(monkeypatch)
    resp = client.get("/auth/callback", params={"code": "abc", "state": "attacker-state"})
    assert resp.status_code == 400


def test_callback_with_tampered_state_cookie_returns_400(client, monkeypatch):
    state, _ = _start_login(client)
    client.cookies.set("oauth_state", "not-a-valid-signed-cookie")
    _exchange_must_not_run(monkeypatch)
    assert client.get("/auth/callback", params={"code": "a", "state": state}).status_code == 400


def test_callback_with_state_signed_for_other_purpose_returns_400(client, monkeypatch):
    forged = sign({"state": "s", "verifier": "v"}, "session")  # wrong salt
    client.cookies.set("oauth_state", forged)
    _exchange_must_not_run(monkeypatch)
    assert client.get("/auth/callback", params={"code": "a", "state": "s"}).status_code == 400


def test_callback_user_denied_consent_returns_400(client):
    assert client.get("/auth/callback", params={"error": "access_denied"}).status_code == 400


def test_callback_google_rejecting_code_returns_400_and_creates_no_user(
    client, monkeypatch, engine
):
    state, _ = _start_login(client)

    def reject(code, verifier):
        raise ValueError("invalid_grant")

    monkeypatch.setattr(google, "exchange_code", reject)
    assert client.get("/auth/callback", params={"code": "bad", "state": state}).status_code == 400
    assert _count(engine, "users") == 0


# ---------- callback success ----------


def test_callback_stores_ciphertext_and_sets_session(client, monkeypatch, engine):
    resp = _sign_in(client, monkeypatch)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/me"
    set_cookies = [c.lower() for c in resp.headers.get_list("set-cookie")]
    session_cookie = next(c for c in set_cookies if c.startswith("session="))
    assert "httponly" in session_cookie and "samesite=lax" in session_cookie

    stored = _stored_token(engine)
    assert stored is not None
    assert not stored.startswith("1//")
    assert stored != PLAIN_REFRESH and PLAIN_REFRESH not in stored
    assert decrypt_token(stored) == PLAIN_REFRESH


def test_me_returns_email_after_login(client, monkeypatch):
    _sign_in(client, monkeypatch)
    resp = client.get("/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == "me@example.test"


def test_state_cookie_cleared_after_successful_callback(client, monkeypatch):
    _sign_in(client, monkeypatch)
    assert "oauth_state" not in client.cookies


def test_second_login_upserts_same_user_and_rotates_token(client, monkeypatch, engine):
    _sign_in(client, monkeypatch, refresh="1//first")
    _sign_in(client, monkeypatch, refresh="1//second")
    assert _count(engine, "users") == 1
    assert decrypt_token(_stored_token(engine)) == "1//second"


def test_relogin_without_new_refresh_token_keeps_existing_one(client, monkeypatch, engine):
    _sign_in(client, monkeypatch, refresh="1//keep-me")
    assert _sign_in(client, monkeypatch, refresh=None).status_code == 303
    assert decrypt_token(_stored_token(engine)) == "1//keep-me"


def test_relogin_clears_needs_reauth(client, monkeypatch, engine):
    _sign_in(client, monkeypatch)
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET needs_reauth = true"))
    _sign_in(client, monkeypatch)
    assert client.get("/me").json()["needs_reauth"] is False


def test_new_user_without_refresh_token_is_rejected(client, monkeypatch, engine):
    assert _sign_in(client, monkeypatch, refresh=None).status_code == 400
    assert _count(engine, "users") == 0


@pytest.mark.parametrize("missing", [GMAIL_READONLY, CALENDAR_EVENTS])
def test_missing_required_scope_is_rejected(client, monkeypatch, engine, missing):
    resp = _sign_in(client, monkeypatch, scopes=ALL_SCOPES - {missing})
    assert resp.status_code == 400
    assert _count(engine, "users") == 0


# ---------- /me, logout ----------


def test_me_requires_login(client):
    assert client.get("/me").status_code == 401


def test_me_rejects_tampered_session_cookie(client, monkeypatch):
    _sign_in(client, monkeypatch)
    client.cookies.set("session", "tampered")
    assert client.get("/me").status_code == 401


def test_me_rejects_session_for_deleted_user(client, monkeypatch, engine):
    _sign_in(client, monkeypatch)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM users"))
    assert client.get("/me").status_code == 401


def test_logout_clears_session(client, monkeypatch):
    _sign_in(client, monkeypatch)
    assert client.get("/me").status_code == 200
    assert client.post("/auth/logout").status_code == 200
    assert client.get("/me").status_code == 401


def test_logout_when_not_logged_in_is_ok(client):
    assert client.post("/auth/logout").status_code == 200


# ---------- DELETE /me ----------


def _seed_rows(engine, user_id: int, tag: str):
    with engine.begin() as conn:
        task_id = conn.execute(
            text(
                "INSERT INTO tasks (user_id, title, source_ref) VALUES (:u, 't', :r) RETURNING id"
            ),
            {"u": user_id, "r": tag},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO reminders (task_id, fire_at, channel) VALUES (:t, now(), 'telegram')"
            ),
            {"t": task_id},
        )
        conn.execute(
            text("INSERT INTO quarantine (user_id, message_id, status) VALUES (:u, :m, 'held')"),
            {"u": user_id, "m": tag},
        )
        conn.execute(
            text("INSERT INTO sync_state (user_id, history_id) VALUES (:u, '1')"), {"u": user_id}
        )
        conn.execute(text("INSERT INTO user_settings (user_id) VALUES (:u)"), {"u": user_id})
        conn.execute(
            text("INSERT INTO ingest_failures (user_id, message_id, error) VALUES (:u, :m, 'x')"),
            {"u": user_id, "m": tag},
        )


RELATED = ["tasks", "quarantine", "sync_state", "user_settings", "ingest_failures"]


def test_delete_me_removes_user_and_all_related_rows(client, monkeypatch, engine):
    _sign_in(client, monkeypatch)
    with engine.begin() as conn:
        other = conn.execute(
            text("INSERT INTO users (email) VALUES ('other@example.test') RETURNING id")
        ).scalar_one()
        mine = conn.execute(
            text("SELECT id FROM users WHERE email = 'me@example.test'")
        ).scalar_one()
    _seed_rows(engine, mine, "mine")
    _seed_rows(engine, other, "other")
    assert _count(engine, "reminders") == 2

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(REVOKE_URL).respond(200)
        resp = client.delete("/me")

    assert resp.status_code == 200
    assert resp.json() == {"deleted": True, "google_revoked": True}
    # Google was asked to revoke the real (decrypted) token
    assert parse_qs(route.calls.last.request.content.decode())["token"] == [PLAIN_REFRESH]

    assert _count(engine, "users", "id = :i", i=mine) == 0
    for table in RELATED:
        assert _count(engine, table, "user_id = :i", i=mine) == 0, table
    assert _count(engine, "reminders") == 1  # only the other user's reminder remains
    # the other user's data is untouched
    assert _count(engine, "users", "id = :i", i=other) == 1
    for table in RELATED:
        assert _count(engine, table, "user_id = :i", i=other) == 1, table
    # session is gone
    assert client.get("/me").status_code == 401


@pytest.mark.parametrize("failure", ["error_status", "network"])
def test_delete_me_still_deletes_when_google_revoke_fails(client, monkeypatch, engine, failure):
    _sign_in(client, monkeypatch)
    with respx.mock() as mock:
        route = mock.post(REVOKE_URL)
        if failure == "network":
            route.mock(side_effect=httpx.ConnectError("down"))
        else:
            route.respond(400)
        resp = client.delete("/me")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True, "google_revoked": False}
    assert _count(engine, "users") == 0


def test_delete_me_requires_login(client):
    assert client.delete("/me").status_code == 401


# ---------- credential loader ----------


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self.status = status
        self.data = json.dumps(payload).encode()
        self.headers = {}


class FakeTransport:
    """Stands in for google-auth's HTTP transport; records token endpoint calls."""

    def __init__(self, payload: dict, status: int = 200):
        self.payload, self.status, self.calls = payload, status, []

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        self.calls.append({"url": url, "method": method, "body": body})
        return FakeResponse(self.payload, self.status)


def _naive_utc(delta: timedelta) -> datetime:
    return (datetime.now(UTC) + delta).replace(tzinfo=None)


@pytest.fixture
def user(db_session):
    u = User(email="me@example.test", encrypted_refresh_token=encrypt_token(PLAIN_REFRESH))
    db_session.add(u)
    db_session.commit()
    return u


def test_loader_refreshes_expired_access_token(user, db_session):
    credentials._access_cache[user.id] = ("expired-token", _naive_utc(-timedelta(hours=1)))
    transport = FakeTransport({"access_token": "new-access-token", "expires_in": 3600})

    creds = load_credentials(user, db_session, request=transport)

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["url"] == "https://oauth2.googleapis.com/token"
    body = call["body"].decode() if isinstance(call["body"], bytes) else call["body"]
    form = parse_qs(body)
    assert form["grant_type"] == ["refresh_token"]
    assert form["refresh_token"] == [PLAIN_REFRESH]
    assert creds.token == "new-access-token"
    assert creds.valid
    assert credentials._access_cache[user.id][0] == "new-access-token"


def test_loader_refreshes_when_no_access_token_cached(user, db_session):
    transport = FakeTransport({"access_token": "first-token", "expires_in": 3600})
    assert load_credentials(user, db_session, request=transport).token == "first-token"
    assert len(transport.calls) == 1


def test_loader_reuses_valid_cached_token_without_network(user, db_session):
    credentials._access_cache[user.id] = ("cached-token", _naive_utc(timedelta(hours=1)))
    transport = FakeTransport({"access_token": "should-not-be-used", "expires_in": 3600})
    assert load_credentials(user, db_session, request=transport).token == "cached-token"
    assert transport.calls == []


def test_loader_marks_needs_reauth_when_google_revokes_refresh_token(user, db_session):
    transport = FakeTransport(
        {"error": "invalid_grant", "error_description": "Token has been expired or revoked."},
        status=400,
    )
    with pytest.raises(ReauthRequired):
        load_credentials(user, db_session, request=transport)
    db_session.refresh(user)
    assert user.needs_reauth is True
    assert user.id not in credentials._access_cache


def test_loader_clears_needs_reauth_after_successful_refresh(user, db_session):
    user.needs_reauth = True
    db_session.commit()
    transport = FakeTransport({"access_token": "tok", "expires_in": 3600})
    load_credentials(user, db_session, request=transport)
    db_session.refresh(user)
    assert user.needs_reauth is False


def test_loader_requires_reauth_when_no_token_stored(user, db_session):
    user.encrypted_refresh_token = None
    db_session.commit()
    transport = FakeTransport({"access_token": "x", "expires_in": 3600})
    with pytest.raises(ReauthRequired):
        load_credentials(user, db_session, request=transport)
    assert transport.calls == []
    db_session.refresh(user)
    assert user.needs_reauth is True


def test_loader_requires_reauth_when_token_cannot_be_decrypted(user, db_session, monkeypatch):
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())  # key was lost/replaced
    get_settings.cache_clear()
    transport = FakeTransport({"access_token": "x", "expires_in": 3600})
    with pytest.raises(ReauthRequired):
        load_credentials(user, db_session, request=transport)
    assert transport.calls == []


# ---------- security helpers ----------


def test_encrypt_decrypt_roundtrip_and_ciphertext_differs():
    c1, c2 = encrypt_token(PLAIN_REFRESH), encrypt_token(PLAIN_REFRESH)
    assert c1 != c2  # Fernet is randomized
    assert PLAIN_REFRESH not in c1 and not c1.startswith("1//")
    assert decrypt_token(c1) == decrypt_token(c2) == PLAIN_REFRESH


def test_decrypt_with_wrong_key_fails(monkeypatch):
    ciphertext = encrypt_token(PLAIN_REFRESH)
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    with pytest.raises(InvalidToken):
        decrypt_token(ciphertext)


def test_key_rotation_old_ciphertext_still_decrypts(monkeypatch):
    old_key = get_settings().fernet_key.get_secret_value()
    old_ciphertext = encrypt_token(PLAIN_REFRESH)
    new_key = Fernet.generate_key().decode()
    monkeypatch.setenv("FERNET_KEY", f"{new_key},{old_key}")
    get_settings.cache_clear()
    assert decrypt_token(old_ciphertext) == PLAIN_REFRESH
    fresh = encrypt_token(PLAIN_REFRESH)
    assert Fernet(new_key.encode()).decrypt(fresh.encode()).decode() == PLAIN_REFRESH


def test_missing_fernet_key_raises(monkeypatch):
    monkeypatch.setenv("FERNET_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="FERNET_KEY"):
        encrypt_token("x")


def test_signed_payload_expires_and_rejects_tampering():
    with freeze_time("2030-01-01 12:00:00"):
        token = sign({"a": 1}, "salt")
        assert unsign(token, "salt", max_age=60) == {"a": 1}
    with freeze_time("2030-01-01 12:02:00"):
        assert unsign(token, "salt", max_age=60) is None
    assert unsign(token + "x", "salt", max_age=10**9) is None
    assert unsign(token, "other-salt", max_age=10**9) is None
