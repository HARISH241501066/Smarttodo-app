# ruff: noqa: E501  (long literals in fixtures and expected strings)
"""Module 13: scopes, secrets and rotation, CSRF and CORS, headers, access control, limits,
output safety, risky actions, rate limits, privacy and production switches."""

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from app.auth import google
from app.config import get_settings
from app.hardening import RATE_LIMITS, reset_rate_limits
from app.main import app, create_app
from app.notify.channels import Outgoing
from app.notify.email import build_email
from app.security import decrypt_token, encrypt_token, sign, unsign


@pytest.fixture
def client(client_factory):
    return client_factory()  # no session cookie


@pytest.fixture
def web(client_factory):
    return client_factory()


FRONTEND = "http://localhost:3000"
EVIL = "https://evil.example"


# ---------- 1. scopes and secrets ----------


def test_the_requested_google_scopes_are_exactly_the_allowed_four():
    assert set(google.SCOPES) == {
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar.events",
    }
    assert len(google.SCOPES) == 4
    assert not [s for s in google.SCOPES if "gmail.send" in s or s.endswith("/calendar")]


def test_the_login_url_asks_for_nothing_more(client):
    url = client.get("/auth/login").headers["location"]
    from urllib.parse import parse_qs, urlparse

    asked = set(parse_qs(urlparse(url).query)["scope"][0].split())
    assert asked == set(google.SCOPES)


def test_refresh_tokens_encrypted_with_the_old_key_still_decrypt_after_rotation(monkeypatch):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setenv("FERNET_KEY", old)
    get_settings.cache_clear()
    sealed_with_old = encrypt_token("refresh-token-1")

    monkeypatch.setenv("FERNET_KEY", f"{new},{old}")  # newest first
    get_settings.cache_clear()
    assert decrypt_token(sealed_with_old) == "refresh-token-1"
    sealed_with_new = encrypt_token("refresh-token-2")

    monkeypatch.setenv("FERNET_KEY", new)  # the old key is retired
    get_settings.cache_clear()
    assert decrypt_token(sealed_with_new) == "refresh-token-2"
    with pytest.raises(Exception):  # noqa: B017  (InvalidToken)
        decrypt_token(sealed_with_old)


def test_signed_values_survive_a_secret_rotation_and_new_ones_use_the_new_secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "old-secret-" + "a" * 32)
    monkeypatch.setenv("MARK_DONE_SECRET", "old-mark-" + "b" * 32)
    get_settings.cache_clear()
    session_old = sign({"uid": 1}, "session")
    done_old = sign({"t": 1}, "mark-done")

    monkeypatch.setenv("SESSION_SECRET", "new-secret-" + "c" * 32 + ",old-secret-" + "a" * 32)
    monkeypatch.setenv("MARK_DONE_SECRET", "new-mark-" + "d" * 32 + ",old-mark-" + "b" * 32)
    get_settings.cache_clear()
    assert unsign(session_old, "session", 100) == {"uid": 1}
    assert unsign(done_old, "mark-done", 100) == {"t": 1}
    fresh = sign({"uid": 2}, "session")
    fresh_done = sign({"t": 2}, "mark-done")

    monkeypatch.setenv("SESSION_SECRET", "new-secret-" + "c" * 32)
    monkeypatch.setenv("MARK_DONE_SECRET", "new-mark-" + "d" * 32)
    get_settings.cache_clear()
    assert unsign(fresh, "session", 100) == {"uid": 2}
    assert unsign(fresh_done, "mark-done", 100) == {"t": 2}
    assert unsign(session_old, "session", 100) is None  # retired
    assert unsign(done_old, "mark-done", 100) is None
    # the two purposes never accept each other's tokens
    assert unsign(fresh, "mark-done", 100) is None


def test_mark_done_links_fall_back_to_the_session_secret(monkeypatch):
    monkeypatch.setenv("MARK_DONE_SECRET", "")
    get_settings.cache_clear()
    token = sign({"t": 5}, "mark-done")
    assert unsign(token, "mark-done", 100) == {"t": 5}


def test_env_example_holds_placeholders_only():
    from pathlib import Path

    text_ = (Path(__file__).resolve().parents[2] / ".env.example").read_text()
    for line in text_.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if any(w in key for w in ("SECRET", "KEY", "PASSWORD")):
            assert (
                len(value.strip()) < 20 or "change" in value.lower() or "your" in value.lower()
            ), key


# ---------- 2. cookies, CSRF, CORS, headers ----------


def test_the_session_cookie_is_httponly_lax_and_secure_in_production(client_factory, monkeypatch):
    from app.api import auth as auth_api

    assert auth_api._cookie_kwargs() == {"httponly": True, "samesite": "lax", "secure": False}
    monkeypatch.setenv("ENV", "production")
    get_settings.cache_clear()
    assert auth_api._cookie_kwargs() == {"httponly": True, "samesite": "lax", "secure": True}


@pytest.mark.parametrize("method", ["post", "patch", "delete", "put"])
def test_state_changing_requests_from_a_foreign_origin_are_refused(api, method):
    task = api.post("/tasks", json={"title": "mine"}).json()
    calls = {
        "post": ("/tasks", {"title": "evil"}),
        "patch": (f"/tasks/{task['id']}", {"title": "evil"}),
        "delete": (f"/tasks/{task['id']}", None),
        "put": ("/settings", {}),
    }
    path, body = calls[method]
    resp = getattr(api, method)(
        path, headers={"Origin": EVIL}, **({"json": body} if body is not None else {})
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Cross-site request refused"
    assert [t["title"] for t in api.get("/tasks").json()] == ["mine"]  # nothing happened


def test_a_foreign_referer_is_refused_and_the_dashboard_origin_is_allowed(api):
    assert (
        api.post("/tasks", json={"title": "x"}, headers={"Referer": EVIL + "/page"}).status_code
        == 403
    )
    assert api.post("/tasks", json={"title": "ok"}, headers={"Origin": FRONTEND}).status_code == 201
    assert (
        api.post(
            "/tasks", json={"title": "ok2"}, headers={"Referer": FRONTEND + "/email"}
        ).status_code
        == 201
    )
    assert api.post("/tasks", json={"title": "n"}, headers={"Origin": "null"}).status_code == 403
    # reading is never blocked by the origin check
    assert api.get("/tasks", headers={"Origin": EVIL}).status_code == 200


def test_the_mark_done_page_may_post_to_itself(client):
    resp = client.post("/action/done?t=garbage", headers={"Origin": "http://localhost:8000"})
    assert resp.status_code != 403


def test_a_disallowed_cors_origin_is_rejected(api):
    preflight = api.options(
        "/tasks",
        headers={"Origin": EVIL, "Access-Control-Request-Method": "POST"},
    )
    assert preflight.status_code == 400
    assert "access-control-allow-origin" not in preflight.headers
    simple = api.get("/tasks", headers={"Origin": EVIL})
    assert "access-control-allow-origin" not in simple.headers

    ok = api.options(
        "/tasks", headers={"Origin": FRONTEND, "Access-Control-Request-Method": "POST"}
    )
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == FRONTEND
    assert ok.headers["access-control-allow-credentials"] == "true"


def test_security_headers_are_on_every_backend_response(api, client):
    for resp in (
        api.get("/tasks"),
        client.get("/health"),
        api.post("/tasks", json={}),
        client.get("/nope"),
    ):
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["referrer-policy"] == "no-referrer"
        csp = resp.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in csp
        assert resp.headers["x-frame-options"] == "DENY"
    refused = api.post("/tasks", json={"title": "x"}, headers={"Origin": EVIL})
    assert refused.headers["x-content-type-options"] == "nosniff"  # even on the 403


# ---------- 3. access control: every route, user B's ids, logged in as user A ----------


@pytest.fixture
def theirs(api_b, engine, uid_b):
    """Everything user B owns, so the sweep can point user A at it."""
    task = api_b.post("/tasks", json={"title": "B task", "subtasks": ["b sub"]}).json()
    folder = api_b.post("/folders", json={"name": "B folder"}).json()
    api_b.post(f"/folders/{folder['id']}/pins/{task['id']}")
    with engine.begin() as conn:
        quarantine = conn.execute(
            text(
                "INSERT INTO quarantine (user_id, message_id, subject, sender, score, reasons, status) "
                "VALUES (:u, 'qb', 'B subject', 'x@y.test', 6, '[]'::jsonb, 'held') RETURNING id"
            ),
            {"u": uid_b},
        ).scalar_one()
        note = conn.execute(
            text(
                "INSERT INTO notifications (user_id, kind, title) VALUES (:u, 'reminder', 'B note') RETURNING id"
            ),
            {"u": uid_b},
        ).scalar_one()
        review = conn.execute(
            text(
                "INSERT INTO tasks (user_id, title, source, source_ref, status, sender_email) "
                "VALUES (:u, 'B review', 'other', 'rb', 'needs_review', 'b@sender.test') RETURNING id"
            ),
            {"u": uid_b},
        ).scalar_one()
    endpoint = "https://push.example.test/b-endpoint"
    api_b.post("/push/subscribe", json={"endpoint": endpoint, "keys": {"p256dh": "k", "auth": "a"}})
    return {
        "task": task["id"],
        "sub": task["subtasks"][0]["id"],
        "folder": folder["id"],
        "quarantine": quarantine,
        "note": note,
        "review": review,
        "endpoint": endpoint,
    }


def sweep_table(t):
    sub_body = {"title": "hacked"}
    return [
        ("GET", f"/tasks/{t['task']}", None),
        ("GET", f"/tasks/{t['task']}/email", None),
        ("PATCH", f"/tasks/{t['task']}", {"title": "hacked"}),
        ("DELETE", f"/tasks/{t['task']}", None),
        ("POST", f"/tasks/{t['task']}/done", None),
        ("POST", f"/tasks/{t['task']}/dismiss", None),
        ("POST", f"/review/{t['review']}/confirm", {}),
        ("POST", f"/tasks/{t['task']}/subtasks", sub_body),
        ("PATCH", f"/tasks/{t['task']}/subtasks/reorder", {"ids": [t["sub"]]}),
        ("PATCH", f"/tasks/{t['task']}/subtasks/{t['sub']}", {"done": True}),
        ("DELETE", f"/tasks/{t['task']}/subtasks/{t['sub']}", None),
        ("PATCH", f"/folders/{t['folder']}", {"name": "hacked"}),
        ("DELETE", f"/folders/{t['folder']}", None),
        ("GET", f"/folders/{t['folder']}/tasks", None),
        ("POST", f"/folders/{t['folder']}/pins/{t['task']}", None),
        ("DELETE", f"/folders/{t['folder']}/pins/{t['task']}", None),
        ("POST", f"/quarantine/{t['quarantine']}/confirm-scam", None),
        ("POST", f"/quarantine/{t['quarantine']}/rescue", None),
        ("POST", f"/notifications/{t['note']}/read", None),
    ]


def test_user_a_gets_403_or_404_on_every_route_with_user_bs_ids(api, theirs):
    from app.api.sync import get_gmail_api
    from tests.gmail_fakes import FakeGmailApi

    app.dependency_overrides[get_gmail_api] = lambda: FakeGmailApi({})
    try:
        for method, path, body in sweep_table(theirs):
            resp = api.request(method, path, **({"json": body} if body is not None else {}))
            assert resp.status_code in (403, 404), (method, path, resp.status_code, resp.text[:120])
    finally:
        app.dependency_overrides.pop(get_gmail_api, None)


def test_the_sweep_covers_every_route_with_an_id_in_its_path(theirs):
    templates = {m + " " + _template(p, theirs) for m, p, _ in sweep_table(theirs)}
    id_routes = {
        f"{m} {r.path}"
        for r in app.routes
        if isinstance(r, APIRoute) and "{" in r.path
        for m in r.methods - {"HEAD", "OPTIONS"}
    }
    assert id_routes <= templates, sorted(id_routes - templates)


def _template(path: str, t: dict) -> str:
    # turn the concrete ids back into the route's parameter names
    out = path
    out = out.replace(f"/review/{t['review']}/", "/review/{task_id}/")
    out = out.replace(f"/quarantine/{t['quarantine']}/", "/quarantine/{quarantine_id}/")
    out = out.replace(f"/notifications/{t['note']}/", "/notifications/{notification_id}/")
    out = out.replace(f"/folders/{t['folder']}", "/folders/{folder_id}")
    out = out.replace(f"/pins/{t['task']}", "/pins/{task_id}")
    out = out.replace(f"/subtasks/{t['sub']}", "/subtasks/{sub_id}")
    out = out.replace(f"/tasks/{t['task']}", "/tasks/{task_id}")
    return out


def test_collections_never_contain_user_bs_data(api, theirs):
    for path in (
        "/tasks",
        "/deadlines",
        "/review",
        "/quarantine",
        "/notifications",
        "/folders",
        "/tags",
    ):
        assert "B " not in json.dumps(api.get(path).json()), path
    assert api.get("/folders/preview?folder_id=" + str(theirs["folder"])).status_code == 404
    assert api.get("/tasks?origin=manual").json() == []


def test_user_a_cannot_unsubscribe_user_bs_browser_or_touch_their_review_items(
    api, api_b, engine, theirs
):
    resp = api.request("DELETE", "/push/subscribe", json={"endpoint": theirs["endpoint"]})
    assert resp.status_code in (204, 404)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM push_subscriptions")).scalar_one() == 1
    bulk = api.post("/review/bulk", json={"action": "dismiss", "sender": "b@sender.test"})
    assert bulk.status_code == 200 and bulk.json()["updated"] == 0
    assert api_b.get("/review").json()[0]["title"] == "B review"
    api.post("/notifications/read-all")
    assert api_b.get("/notifications").json()["unread_count"] == 1


def test_a_users_settings_are_their_own(api, api_b):
    api.put("/settings", json={**api.get("/settings").json(), "block_senders": ["only-a.test"]})
    assert api_b.get("/settings").json()["block_senders"] == []


# ---------- 4. input validation ----------

BAD_LINKS = [
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    "data:text/html,<script>1</script>",
    "file:///etc/passwd",
    "vbscript:msgbox(1)",
    "ftp://x.test/a",
    "//evil.test",
    " javascript:alert(1)",
]


@pytest.mark.parametrize("link", BAD_LINKS)
def test_dangerous_links_are_rejected_on_every_way_to_set_one(api, engine, uid, link):
    assert api.post("/tasks", json={"title": "t", "link": link}).status_code == 422
    task = api.post("/tasks", json={"title": "ok", "link": "https://good.example/a"}).json()
    assert api.patch(f"/tasks/{task['id']}", json={"link": link}).status_code == 422
    assert api.get(f"/tasks/{task['id']}").json()["link"] == "https://good.example/a"
    with engine.begin() as conn:
        rid = conn.execute(
            text(
                "INSERT INTO tasks (user_id, title, source, source_ref, status) "
                "VALUES (:u, 'r', 'other', 'r1', 'needs_review') RETURNING id"
            ),
            {"u": uid},
        ).scalar_one()
    assert api.post(f"/review/{rid}/confirm", json={"link": link}).status_code == 422


def test_links_found_in_emails_are_only_ever_http_or_https():
    from app.filters.links import is_droppable, pick_action_link
    from app.ingest.mime import Link, ParsedMessage

    links = tuple(Link(u, "Take quiz") for u in (*BAD_LINKS, "mailto:a@b.test"))
    for link in links:
        assert is_droppable(link), link.url
    message = ParsedMessage(
        message_id="m1", thread_id="t", labels=(), received_at=None, sender_email="a@b.test",
        sender="A <a@b.test>", subject="s", body_text="", has_attachments=False, links=links,
    )  # fmt: skip
    choice = pick_action_link(message)
    assert choice.link_type == "gmail" and choice.url.startswith("https://mail.google.com/")


def test_size_limits(api):
    assert api.post("/tasks", json={"title": "x" * 300}).status_code == 201
    assert api.post("/tasks", json={"title": "x" * 301}).status_code == 422
    assert api.post("/tasks", json={"title": "t", "notes": "n" * 5000}).status_code == 201
    assert api.post("/tasks", json={"title": "t", "notes": "n" * 5001}).status_code == 422
    assert api.post("/tasks", json={"title": "t", "tag": "g" * 50}).status_code == 201
    assert api.post("/tasks", json={"title": "t", "tag": "g" * 51}).status_code == 422
    base = api.get("/settings").json()
    sources = lambda n: [{"name": f"S{i}", "type": "custom", "senders": []} for i in range(n)]  # noqa: E731
    assert api.put("/settings", json={**base, "sources": sources(50)}).status_code == 200
    assert api.put("/settings", json={**base, "sources": sources(51)}).status_code == 422
    senders = lambda n: [f"d{i}.example.test" for i in range(n)]  # noqa: E731
    one = lambda n: [{"name": "A", "type": "custom", "senders": senders(n)}]  # noqa: E731
    assert api.put("/settings", json={**base, "sources": one(100)}).status_code == 200
    assert api.put("/settings", json={**base, "sources": one(101)}).status_code == 422
    keywords = lambda n: [f"word{i}" for i in range(n)]  # noqa: E731
    assert api.put("/settings", json={**base, "watch_keywords": keywords(20)}).status_code == 200
    assert api.put("/settings", json={**base, "watch_keywords": keywords(21)}).status_code == 422
    for i in range(50):
        assert api.post("/folders", json={"name": f"F{i}"}).status_code == 201
    assert api.post("/folders", json={"name": "51st"}).status_code == 422


def test_sender_entries_must_be_domains_or_addresses(api):
    base = api.get("/settings").json()
    for bad in (
        "not a domain",
        "javascript:alert(1)",
        "a@",
        "x OR from:y.test",
        "-in:anywhere",
        "<script>",
    ):
        body = {**base, "sources": [{"name": "A", "type": "custom", "senders": [bad]}]}
        assert api.put("/settings", json=body).status_code == 422, bad


# ---------- 5. output safety ----------

HOSTILE = '<script>alert(1)</script><img src=x onerror=alert(2)> & "quotes"'


def test_escalation_emails_escape_the_title_and_the_link():
    message = Outgoing(
        user_id=1, task_id=1, reminder_id=1, channel="email", kind="t24", title=HOSTILE, body="b",
        link='https://x.test/?a="><script>1</script>', user_email="me@example.test",
        deadline=datetime(2030, 1, 1, tzinfo=UTC),
    )  # fmt: skip
    mail = build_email(message, sender="from@example.test", base_url="https://app.test")
    html = mail.get_body(("html",)).get_content()
    assert "<script>" not in html and "<img" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(2)&gt;" in html  # shown as text, never an element
    assert '"><script>' not in html


def test_javascript_links_never_reach_an_email_button():
    message = Outgoing(
        user_id=1, task_id=1, reminder_id=1, channel="email", kind="t24", title="t", body="b",
        link="javascript:alert(1)", user_email="me@example.test",
    )  # fmt: skip
    html = (
        build_email(message, sender="from@example.test", base_url="https://app.test")
        .get_body(("html",))
        .get_content()
    )
    assert "javascript:" not in html


def test_push_text_is_plain_and_truncated():
    from app.notify.push import payload_for

    message = Outgoing(
        user_id=1, task_id=1, reminder_id=1, channel="push", kind="t24", title="T" * 500,
        body="B" * 900, link="javascript:alert(1)",
    )  # fmt: skip
    data = json.loads(payload_for(message, "https://app.test/"))
    assert len(data["title"]) <= 200 and len(data["body"]) <= 300
    assert data["url"] == "https://app.test/"  # a javascript: link is never offered


def test_the_mark_done_page_escapes_whatever_it_shows(web, engine, uid):
    from app.notify.tokens import make_done_token

    with engine.begin() as conn:
        tid = conn.execute(
            text("INSERT INTO tasks (user_id, title, status) VALUES (:u, :t, 'open') RETURNING id"),
            {"u": uid, "t": HOSTILE},
        ).scalar_one()
    token = make_done_token(tid, datetime.now(UTC) + timedelta(days=1))
    page = web.get(f"/action/done?t={token}")
    assert "<script>" not in page.text and "<img" not in page.text


# ---------- 6. risky actions need typing the word ----------


def test_rebuild_fails_without_the_typed_confirmation(api, engine, uid):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tasks (user_id, title, source, source_ref, status, deadline) "
                "VALUES (:u, 'auto', 'nptel', 'm1', 'open', now() + interval '2 days')"
            ),
            {"u": uid},
        )
    from app.api.sync import get_gmail_api
    from tests.gmail_fakes import FakeGmailApi

    app.dependency_overrides[get_gmail_api] = lambda: FakeGmailApi({})
    try:
        for body in (
            None,
            {},
            {"confirm": ""},
            {"confirm": "rebuild"},
            {"confirm": "DELETE"},
            {"confirm": True},
        ):
            reset_rate_limits()  # refused attempts still count; not what is tested here
            resp = api.post(
                "/sources/rebuild?wait=true", **({"json": body} if body is not None else {})
            )
            assert resp.status_code == 422, body
    finally:
        app.dependency_overrides.pop(get_gmail_api, None)
    assert api.get("/sources/rebuild/status").json() == {"state": "idle"}  # it never started
    assert [t["title"] for t in api.get("/tasks").json()] == ["auto"]


def test_deleting_the_account_fails_without_the_typed_confirmation(api, engine, uid):
    api.post("/tasks", json={"title": "keep me"})
    assert api.delete("/me").status_code == 405  # the old unconfirmed route is gone
    for body in (None, {}, {"confirm": ""}, {"confirm": "delete"}, {"confirm": "REBUILD"}):
        resp = api.post("/me/delete", **({"json": body} if body is not None else {}))
        assert resp.status_code == 422, body
    with engine.connect() as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM users WHERE id = :u"), {"u": uid}).scalar_one()
            == 1
        )
    assert [t["title"] for t in api.get("/tasks").json()] == ["keep me"]


# ---------- rate limits ----------


def _hammer(send, name):
    allowed, _ = RATE_LIMITS[name]
    codes = [send().status_code for _ in range(allowed + 1)]
    assert 429 not in codes[:allowed], codes
    assert codes[-1] == 429
    return codes


def test_auth_routes_are_rate_limited(client):
    resp = None

    def send():
        nonlocal resp
        resp = client.get("/auth/login")
        return resp

    _hammer(send, "auth")
    assert "retry-after" in resp.headers


def test_the_mark_done_page_and_post_are_rate_limited(client):
    _hammer(lambda: client.get("/action/done?t=x"), "mark-done")


def test_the_mark_done_post_shares_its_limit(client):
    allowed, _ = RATE_LIMITS["mark-done"]
    for _ in range(allowed):
        client.post("/action/done?t=x")
    assert client.post("/action/done?t=x").status_code == 429
    assert client.get("/action/done?t=x").status_code == 429


def test_push_subscribe_is_rate_limited(api):
    body = {"endpoint": "https://push.example.test/e", "keys": {"p256dh": "k", "auth": "a"}}
    _hammer(lambda: api.post("/push/subscribe", json=body), "push-subscribe")


def test_sync_and_rebuild_triggers_are_rate_limited(api):
    from app.api.sync import get_gmail_api
    from tests.gmail_fakes import FakeGmailApi

    app.dependency_overrides[get_gmail_api] = lambda: FakeGmailApi({})
    try:
        _hammer(lambda: api.post("/sync/run"), "sync")
        reset_rate_limits()
        allowed, _ = RATE_LIMITS["rebuild"]
        codes = [
            api.post("/sources/rebuild?wait=true", json={"confirm": "REBUILD"}).status_code
            for _ in range(allowed + 1)
        ]
        assert codes[-1] == 429 and 429 not in codes[:allowed]
    finally:
        app.dependency_overrides.pop(get_gmail_api, None)


def test_limits_are_per_client_and_reset_after_the_window(client, monkeypatch):
    import app.hardening as hardening

    allowed, window = RATE_LIMITS["auth"]
    now = [1000.0]
    monkeypatch.setattr(hardening.time, "monotonic", lambda: now[0])
    for _ in range(allowed):
        assert client.get("/auth/login").status_code == 302
    assert client.get("/auth/login").status_code == 429
    now[0] += window + 1
    assert client.get("/auth/login").status_code == 302


# ---------- 7. privacy ----------

TEXT_OK = {"encrypted_refresh_token", "endpoint", "p256dh", "auth"}  # credentials, not content
BODYISH = {"body", "body_text", "html", "raw", "payload", "snippet", "content", "message_body"}


def test_no_table_has_a_column_that_could_hold_an_email_body(engine):
    insp = inspect(engine)
    for table in insp.get_table_names():
        if table == "apscheduler_jobs":
            continue
        for column in insp.get_columns(table):
            name = column["name"].lower()
            if (table, name) == ("notifications", "body"):
                continue  # our own reminder text, never email content
            assert name not in BODYISH, (table, name)
    # the only free-text columns that may carry email-derived words are these (subject-length)
    allowed_text = {
        ("tasks", "title"), ("tasks", "notes"), ("tasks", "tag"), ("tasks", "link"),
        ("subtasks", "title"), ("notifications", "title"), ("notifications", "body"),
        ("notifications", "link"), ("quarantine", "subject"), ("quarantine", "sender"),
        ("ingest_failures", "error"), ("processed_messages", "reason"),
    }  # fmt: skip
    # Every text column of every table is on the list or is plainly not email text.
    for table in insp.get_table_names():
        if table in ("apscheduler_jobs", "alembic_version"):
            continue
        for column in insp.get_columns(table):
            if column["type"].__class__.__name__ == "TEXT":
                assert (table, column["name"]) in allowed_text or column["name"] in TEXT_OK, (
                    table, column["name"],
                )  # fmt: skip


def test_a_whole_sync_stores_and_logs_no_body_text(db_session, caplog, engine):
    """Run the real pipeline over fixture mails whose bodies carry a unique marker, then search
    every table and the logs for it."""
    import base64

    from app.filters.pipeline import make_filter_sink
    from app.ingest.sync import run_sync
    from app.models import User, UserSettings
    from tests.gmail_fakes import FakeGmailApi

    marker = "ZXQ-BODY-MARKER-77431"
    user = User(email="priv@example.test")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        UserSettings(
            user_id=user.id,
            sources=[{"name": "NPTEL", "type": "nptel", "senders": ["nptel.ac.in"]}],
        )
    )
    db_session.commit()

    def mail(mid, subject, sender, body, auth="mx; dmarc=pass"):
        data = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
        return {
            "id": mid, "threadId": mid, "labelIds": ["INBOX"], "internalDate": "1767225600000",
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": sender},
                    {"name": "Subject", "value": subject},
                    {"name": "Authentication-Results", "value": auth},
                ],
                "body": {"size": len(body), "data": data},
            },
        }  # fmt: skip

    messages = {
        "p1": mail("p1", "Assignment is live", "NPTEL <a@nptel.ac.in>", f"Assignment is live. Last date 25 Oct 2030. {marker}"),
        "p2": mail("p2", "Hackathon pay now", "X <a@scam.example>", f"Pay the registration fee via UPI. {marker}", auth=""),
        "p3": mail("p3", "Your OTP", "NPTEL <a@nptel.ac.in>", f"Your OTP is 123456 {marker}"),
        "p4": mail("p4", "Hello", "Ravi <r@gmail.com>", f"hi {marker}"),
    }  # fmt: skip
    with caplog.at_level(logging.DEBUG):
        result = run_sync(user, db_session, FakeGmailApi(messages), make_filter_sink(db_session))
    assert result.failed == 0 and sum(result.decisions.values()) == 4

    dump = []
    with engine.connect() as conn:
        for table in inspect(engine).get_table_names():
            for row in conn.execute(text(f'SELECT * FROM "{table}"')).mappings():
                dump.append(json.dumps(dict(row), default=str))
    assert marker not in "\n".join(dump)
    assert marker not in caplog.text
    assert "UPI" not in caplog.text and "OTP is" not in caplog.text


def test_finished_tasks_lose_their_notes_and_notification_bodies_after_30_days(db_session, engine):
    from app.models import Notification, Task, User
    from app.retention import purge_completed

    user = User(email="ret@example.test")
    db_session.add(user)
    db_session.flush()
    now = datetime.now(UTC)

    def task(title, status, age_days):
        t = Task(user_id=user.id, title=title, notes=f"private {title}", status=status)
        db_session.add(t)
        db_session.flush()
        db_session.add(
            Notification(
                user_id=user.id, task_id=t.id, kind="reminder", title=title, body=f"body {title}"
            )
        )
        db_session.execute(
            text("UPDATE tasks SET updated_at = :u WHERE id = :i"),
            {"u": now - timedelta(days=age_days), "i": t.id},
        )
        return t.id

    old_done = task("old done", "done", 31)
    new_done = task("new done", "done", 5)
    old_open = task("old open", "open", 90)
    db_session.commit()

    assert purge_completed(db_session, now) == (1, 1)
    db_session.commit()
    rows = {r.title: r for r in db_session.query(Task).all()}
    assert rows["old done"].notes is None and rows["old done"].title == "old done"
    assert rows["new done"].notes and rows["old open"].notes
    bodies = {n.title: n.body for n in db_session.query(Notification).all()}
    assert bodies == {"old done": None, "new done": "body new done", "old open": "body old open"}
    assert purge_completed(db_session, now) == (0, 0)  # idempotent
    assert old_done and new_done and old_open


def test_delete_me_leaves_zero_rows_for_the_user_in_every_table(
    api, engine, uid, uid_b, monkeypatch
):
    """Fill every table the user can have rows in, then erase the account."""
    deleted = []

    class FakeCalendar:
        def delete_event(self, event_id):
            deleted.append(event_id)

    monkeypatch.setattr("app.api.me.calendar_for", lambda user, session: FakeCalendar())
    task = api.post(
        "/tasks",
        json={
            "title": "t",
            "subtasks": ["s"],
            "deadline": (datetime.now(UTC) + timedelta(days=3)).isoformat(),
        },
    ).json()
    folder = api.post("/folders", json={"name": "f"}).json()
    api.post(f"/folders/{folder['id']}/pins/{task['id']}")
    api.post(
        "/push/subscribe",
        json={"endpoint": "https://push.example.test/me", "keys": {"p256dh": "k", "auth": "a"}},
    )
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE tasks SET gcal_event_id = 'evt-1' WHERE id = :i"), {"i": task["id"]}
        )
        conn.execute(
            text("INSERT INTO calendar_orphans (user_id, event_id) VALUES (:u, 'evt-2')"),
            {"u": uid},
        )
        conn.execute(
            text(
                "INSERT INTO notifications (user_id, task_id, kind, title) VALUES (:u, :t, 'reminder', 'n')"
            ),
            {"u": uid, "t": task["id"]},
        )
        conn.execute(
            text("INSERT INTO quarantine (user_id, message_id, status) VALUES (:u, 'q', 'held')"),
            {"u": uid},
        )
        conn.execute(
            text(
                "INSERT INTO processed_messages (user_id, message_id, decision) VALUES (:u, 'pm', 'dropped')"
            ),
            {"u": uid},
        )
        conn.execute(
            text("INSERT INTO ingest_failures (user_id, message_id, error) VALUES (:u, 'f', 'e')"),
            {"u": uid},
        )
        conn.execute(
            text("INSERT INTO sync_state (user_id) VALUES (:u) ON CONFLICT DO NOTHING"), {"u": uid}
        )
    api.get("/settings")  # creates the settings row
    with engine.connect() as conn:
        user_tables = [
            t for t in inspect(engine).get_table_names()
            if any(c["name"] == "user_id" for c in inspect(engine).get_columns(t))
        ]  # fmt: skip
        filled = [
            t
            for t in user_tables
            if conn.execute(
                text(f'SELECT count(*) FROM "{t}" WHERE user_id = :u'), {"u": uid}
            ).scalar_one()
        ]
    assert {
        "tasks",
        "folders",
        "notifications",
        "push_subscriptions",
        "quarantine",
        "processed_messages",
        "user_settings",
    } <= set(filled), filled

    resp = api.post("/me/delete", json={"confirm": "DELETE"})
    assert resp.status_code == 200 and resp.json()["deleted"] is True
    assert sorted(deleted) == ["evt-1", "evt-2"] and resp.json()["calendar_events_deleted"] == 2

    with engine.connect() as conn:
        for t in inspect(engine).get_table_names():
            columns = {c["name"] for c in inspect(engine).get_columns(t)}
            if "user_id" in columns:
                assert (
                    conn.execute(
                        text(f'SELECT count(*) FROM "{t}" WHERE user_id = :u'), {"u": uid}
                    ).scalar_one()
                    == 0
                ), t
        for child in ("subtasks", "reminders", "folder_pins"):
            assert conn.execute(text(f'SELECT count(*) FROM "{child}"')).scalar_one() == 0, child
        assert (
            conn.execute(text("SELECT count(*) FROM users WHERE id = :u"), {"u": uid}).scalar_one()
            == 0
        )
        assert (
            conn.execute(
                text("SELECT count(*) FROM users WHERE id = :u"), {"u": uid_b}
            ).scalar_one()
            == 1
        )


# ---------- 8. production switches ----------


def test_in_production_docs_and_the_dev_push_page_are_off(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("DEV_PUSH_TEST_PAGE", "true")  # forced off anyway
    get_settings.cache_clear()
    prod = TestClient(create_app())
    for path in ("/docs", "/redoc", "/openapi.json", "/dev/push-test", "/dev/sw.js"):
        assert prod.get(path).status_code == 404, path
    assert prod.get("/nothing-here").status_code == 404
    assert "strict-transport-security" in prod.get("/nothing-here").headers


def test_outside_production_docs_exist_and_the_dev_page_obeys_its_flag(monkeypatch):
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("DEV_PUSH_TEST_PAGE", "true")
    get_settings.cache_clear()
    dev = TestClient(create_app())
    assert dev.get("/docs").status_code == 200
    assert dev.get("/dev/push-test").status_code == 200
    monkeypatch.setenv("DEV_PUSH_TEST_PAGE", "false")
    get_settings.cache_clear()
    assert TestClient(create_app()).get("/dev/push-test").status_code == 404


def test_both_spellings_of_the_environment_switch_work(monkeypatch):
    for name in ("ENV", "APP_ENV"):
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.delenv("APP_ENV", raising=False)
        monkeypatch.setenv(name, "Production")
        get_settings.cache_clear()
        assert get_settings().is_production and get_settings().secure_cookies


# ---------- expired Google login never crashes the scheduler ----------


def test_an_expired_google_login_stops_that_users_sync_and_the_scheduler_keeps_going(db_session):
    from app.auth.credentials import ReauthRequired
    from app.models import User
    from app.scheduler.jobs import poll_sync

    good = User(email="good@example.test", encrypted_refresh_token="x")
    bad = User(email="bad@example.test", encrypted_refresh_token="x")
    db_session.add_all([bad, good])
    db_session.commit()

    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(db_session.get_bind(), expire_on_commit=False)
    seen = []

    def gmail_factory(user, session):
        seen.append(user.email)
        if user.email == "bad@example.test":
            raise ReauthRequired("invalid_grant")
        from tests.gmail_fakes import FakeGmailApi

        return FakeGmailApi({})

    outcomes = poll_sync(session_factory=factory, gmail_factory=gmail_factory)
    assert outcomes[bad.id] == "needs_reauth"
    assert outcomes[good.id].startswith("ok")  # the next user was still synced
    assert sorted(seen) == ["bad@example.test", "good@example.test"]
