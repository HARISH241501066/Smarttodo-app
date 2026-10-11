# ruff: noqa: E501  (long literals in fixtures and expected strings)
"""Deployment: the dashboard served by the backend, /health, the single scheduler, production."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import db
from app.config import Settings, get_settings
from app.db import get_session
from app.health import STALE_AFTER, beat, health
from app.main import create_app
from app.scheduler.core import acquire_scheduler_lock
from app.security import sign
from app.static_site import script_hashes

INLINE = "self.__next_f.push([1,'hello'])"


@pytest.fixture
def dist(tmp_path) -> Path:
    """A tiny stand-in for the exported dashboard."""
    root = tmp_path / "dist"
    (root / "_next" / "static").mkdir(parents=True)
    page = (
        '<!doctype html><html><head><script src="/_next/static/app.js"></script>'
        f'<script>{INLINE}</script><script type="application/json">{{"a":1}}</script>'
        "</head><body>%s</body></html>"
    )
    (root / "index.html").write_text(page % "HOME")
    (root / "settings.html").write_text(page % "SETTINGS")
    (root / "folder.html").write_text(page % "FOLDER")
    (root / "_next" / "static" / "app.js").write_text("console.log(1)")
    (root / "sw.js").write_text("self.addEventListener('push', () => {})")
    (tmp_path / "secret.txt").write_text("TOP SECRET")  # next to the dist folder, not inside
    return root


@pytest.fixture
def site(dist, engine):
    """The backend with the dashboard bundled in, on the throwaway database."""
    application = create_app(dist)

    def override():
        with sessionmaker(engine, expire_on_commit=False)() as session:
            yield session

    application.dependency_overrides[get_session] = override
    with TestClient(application, follow_redirects=False) as c:
        yield c


@pytest.fixture
def real_db(empty_db, engine, monkeypatch):
    """Point the app's own engine (used by /health and the scheduler) at the throwaway database."""
    monkeypatch.setenv("DATABASE_URL", empty_db)
    get_settings.cache_clear()
    db.get_engine.cache_clear()
    yield empty_db
    get_settings.cache_clear()
    db.get_engine.cache_clear()


# ---------- the dashboard is served by the backend ----------


def test_pages_come_from_the_backend_and_every_app_route_survives_a_refresh(site):
    assert "HOME" in site.get("/").text
    assert "SETTINGS" in site.get("/settings").text
    assert "FOLDER" in site.get("/folder?id=3").text
    # an address no file matches is an app route: the dashboard decides what to show
    for path in ("/something/deep/inside", "/deadlines", "/folders/3"):
        resp = site.get(path)
        assert resp.status_code == 200 and "HOME" in resp.text, path
    assert site.head("/settings").status_code == 200


def test_assets_have_the_right_caching_and_a_missing_asset_is_a_404_not_the_app(site):
    asset = site.get("/_next/static/app.js")
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
    assert site.get("/_next/static/nope.js").status_code == 404
    assert site.get("/favicon-missing.ico").json() == {"detail": "Not Found"}
    sw = site.get("/sw.js")
    assert sw.headers["cache-control"] == "no-cache" and sw.headers["service-worker-allowed"] == "/"
    assert site.get("/settings").headers["cache-control"] == "no-cache"


def test_the_page_policy_allows_only_its_own_inline_scripts_by_hash(site, dist):
    csp = site.get("/settings").headers["content-security-policy"]
    (digest,) = script_hashes(
        (dist / "settings.html").read_text()
    )  # the JSON block is not a script
    assert f"script-src 'self' {digest};" in csp
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert "default-src 'self'" in csp
    headers = site.get("/").headers
    assert (
        headers["x-content-type-options"] == "nosniff"
        and headers["referrer-policy"] == "no-referrer"
    )


def test_files_outside_the_dashboard_folder_are_never_served(site):
    for path in (
        "/../secret.txt",
        "/%2e%2e/secret.txt",
        "/..%2fsecret.txt",
        "/_next/../../secret.txt",
    ):
        resp = site.get(path)
        assert "TOP SECRET" not in resp.text, path


def test_the_api_lives_under_api_and_is_not_hidden_by_the_dashboard(site, engine):
    with engine.begin() as conn:
        uid = conn.execute(
            text("INSERT INTO users (email) VALUES ('a@example.test') RETURNING id")
        ).scalar_one()
    site.cookies.set("session", sign({"uid": uid}, "session"))
    created = site.post(
        "/api/tasks",
        json={"title": "from the bundled app"},
        headers={"Origin": "http://localhost:3000"},
    )
    assert created.status_code == 201
    assert [t["title"] for t in site.get("/api/tasks").json()] == ["from the bundled app"]
    assert site.get("/api/me").json()["email"] == "a@example.test"
    # a 404 from the API is JSON; so is an unknown /api path; never the dashboard page
    missing = site.get("/api/tasks/99999")
    assert missing.status_code == 404 and missing.json() == {"detail": "Task not found"}
    assert site.get("/api/no-such-thing").json() == {"detail": "Not Found"}
    # without /api the same words are dashboard pages, so /settings never answers with JSON
    assert "SETTINGS" in site.get("/settings").text
    assert site.get("/tasks").text.startswith("<!doctype html>")
    # unauthenticated API calls are refused, not answered with a page
    site.cookies.clear()
    assert site.get("/api/tasks").status_code == 401


def test_auth_action_and_health_stay_at_the_root(site):
    assert site.get("/health").json()["db"] == "ok"
    login = site.get("/auth/login")
    assert login.status_code == 302 and "accounts.google.com" in login.headers["location"]
    assert site.get("/api/auth/login").status_code == 302  # what the dashboard's button calls
    page = site.get("/action/done?t=garbage")
    assert page.status_code == 400 or page.status_code == 410 or page.status_code == 200
    assert "<!doctype html>" in page.text and "HOME" not in page.text
    assert site.get("/auth/nothing-here").json() == {"detail": "Not Found"}


def test_without_a_dashboard_everything_stays_at_the_root(engine):
    plain = TestClient(create_app(None))
    assert plain.get("/tasks").status_code == 401  # the API, not a page
    assert plain.get("/api/tasks").status_code == 404
    empty = TestClient(create_app("/definitely/not/here"))
    assert empty.get("/tasks").status_code == 401


# ---------- production ----------


def test_in_production_the_bundled_app_has_no_docs_and_no_dev_page(dist, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("DEV_PUSH_TEST_PAGE", "true")
    monkeypatch.setenv("PUBSUB_MODE", "push")
    get_settings.cache_clear()
    prod = TestClient(create_app(dist))
    for path in ("/docs", "/redoc", "/openapi.json", "/dev/push-test", "/dev/sw.js"):
        resp = prod.get(path)
        assert resp.status_code == 404 and "HOME" not in resp.text, path
    assert "HOME" in prod.get("/").text
    assert prod.get("/").headers["strict-transport-security"].startswith("max-age=")
    assert get_settings().effective_pubsub_mode == "off"
    assert get_settings().secure_cookies and not get_settings().dev_page_enabled


def test_database_urls_from_hosts_are_made_to_use_psycopg_3():
    for given in ("postgres://u:p@h/db?sslmode=require", "postgresql://u:p@h/db?sslmode=require"):
        assert (
            Settings(database_url=given).database_url
            == "postgresql+psycopg://u:p@h/db?sslmode=require"
        )
    assert (
        Settings(database_url="postgresql+psycopg://u:p@h/db").database_url
        == "postgresql+psycopg://u:p@h/db"
    )


# ---------- /health ----------


def test_health_is_503_when_the_scheduler_heartbeat_is_missing_or_old_and_200_when_fresh(
    real_db, monkeypatch
):
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    get_settings.cache_clear()
    now = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
    status, body = health(now)
    assert (status, body) == (
        503,
        {"status": "fail", "db": "ok", "scheduler": "stale"},
    )  # never beat

    eng = create_engine(real_db)
    with sessionmaker(eng, expire_on_commit=False)() as s:
        beat(s, now=now - timedelta(minutes=4))
        assert health(now) == (200, {"status": "ok", "db": "ok", "scheduler": "ok"})
        beat(s, now=now - STALE_AFTER - timedelta(seconds=1))  # one beat, now too old
        assert health(now)[0] == 503 and health(now)[1]["scheduler"] == "stale"
        beat(s, now=now)
        assert health(now)[0] == 200
        assert (
            s.execute(text("SELECT count(*) FROM heartbeats")).scalar_one() == 1
        )  # one row, updated
    eng.dispose()


def test_the_health_endpoint_returns_the_status_code_and_nothing_secret(real_db, monkeypatch):
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    get_settings.cache_clear()
    web = TestClient(create_app(None))
    stale = web.get("/health")
    assert stale.status_code == 503 and stale.json()["scheduler"] == "stale"
    assert stale.headers["cache-control"] == "no-store"
    eng = create_engine(real_db)
    with sessionmaker(eng, expire_on_commit=False)() as s:
        beat(s)
    eng.dispose()
    fresh = web.get("/health")
    assert fresh.status_code == 200 and set(fresh.json()) == {"status", "db", "scheduler"}
    assert real_db not in fresh.text and "postgres" not in fresh.text


def test_health_is_503_naming_the_database_when_it_cannot_be_reached(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@127.0.0.1:1/x")
    get_settings.cache_clear()
    db.get_engine.cache_clear()
    try:
        status, body = health()
        assert status == 503 and body["db"] == "error" and "127.0.0.1" not in str(body)
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        db.get_engine.cache_clear()


def test_the_dispatcher_job_writes_a_heartbeat(engine, monkeypatch, real_db):
    from app.scheduler import jobs

    monkeypatch.setattr(jobs, "_session", lambda: sessionmaker(engine, expire_on_commit=False)())
    jobs.dispatch_job()
    with engine.connect() as conn:
        last = conn.execute(
            text("SELECT last_beat_at FROM heartbeats WHERE name = 'dispatcher'")
        ).scalar_one()
    assert datetime.now(UTC) - last < timedelta(seconds=30)


# ---------- only one scheduler ----------


def test_only_one_process_can_hold_the_scheduler_lock(empty_db):
    first = acquire_scheduler_lock(empty_db)
    assert first is not None
    assert acquire_scheduler_lock(empty_db) is None  # a second instance is refused
    assert acquire_scheduler_lock(empty_db) is None
    first.release()
    again = acquire_scheduler_lock(empty_db)
    assert again is not None  # released: the next one may take over
    again.release()


def test_two_app_processes_start_but_only_one_runs_the_scheduler(real_db, monkeypatch):
    started = []

    class FakeScheduler:
        def shutdown(self, wait=True):
            started.append("stopped")

    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "app.main.start_scheduler", lambda: started.append("started") or FakeScheduler()
    )
    first, second = create_app(None), create_app(None)
    with TestClient(first):
        with TestClient(second):
            assert started == ["started"]  # the second app came up without a scheduler
            assert hasattr(first.state, "scheduler") and not hasattr(second.state, "scheduler")
            assert second.state.scheduler_lock is None
        assert started == ["started"]
    assert started == ["started", "stopped"]
    # the lock went back when the first one stopped, so a restart works
    with TestClient(create_app(None)):
        assert started.count("started") == 2


def test_the_service_worker_is_allowed_to_fetch_from_its_own_origin(site):
    """A worker runs under the policy of the response that delivered it."""
    for path in ("/sw.js", "/_next/static/app.js"):
        csp = site.get(path).headers["content-security-policy"]
        assert "default-src 'self'" in csp and "connect-src 'self'" in csp, path
        assert "default-src 'none'" not in csp
