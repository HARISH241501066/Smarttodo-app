import time
import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import sessionmaker

from alembic import command
from app.auth import credentials
from app.config import get_settings
from app.db import get_session
from app.main import app
from app.security import sign
from tests.helpers import alembic_cfg


def _fields(dependant):
    for group in ("path_params", "query_params", "header_params", "cookie_params", "body_params"):
        yield from getattr(dependant, group, [])
    for sub in getattr(dependant, "dependencies", []):
        yield from _fields(sub)


@pytest.fixture(scope="session", autouse=True)
def _warm_fastapi_schemas():
    """FastAPI builds each field's pydantic TypeAdapter lazily, on the first request. If that
    first request happens while freezegun has time frozen, `datetime` is a fake class and schema
    generation fails. Build them all up front."""
    import typing

    from fastapi._compat import get_cached_model_fields
    from pydantic import BaseModel

    from app.main import app

    for route in app.routes:
        if not hasattr(route, "dependant"):
            continue
        fields = [*_fields(route.dependant), getattr(route, "response_field", None)]
        for field in filter(None, fields):
            getattr(field, "_type_adapter", None)
        # request bodies: FastAPI builds a field per model attribute on the first request
        for body in route.dependant.body_params:
            annotation = body.field_info.annotation
            for candidate in (annotation, *typing.get_args(annotation)):
                if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                    get_cached_model_fields(candidate)
    yield


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    """Tests never depend on (or touch) the real Google credentials or keys in .env."""
    from app.hardening import reset_rate_limits

    reset_rate_limits()
    from app import db as app_db
    from app import rebuild
    from app.health import reset_health_cache

    reset_health_cache()
    app_db.get_engine.cache_clear()  # no engine may leak from one test's database into the next
    rebuild._status.clear()  # a previous test's finished rebuild must not look like this one's
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret-" + "x" * 32)
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("SMS_ENABLED", "false")
    monkeypatch.setenv("CALENDAR_ENABLED", "false")
    monkeypatch.setenv("DEV_PUSH_TEST_PAGE", "false")
    for name in ("SMTP_HOST", "VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY"):
        monkeypatch.setenv(name, "")  # tests never send real email or push
    get_settings.cache_clear()
    credentials._access_cache.clear()
    yield
    get_settings.cache_clear()
    credentials._access_cache.clear()


def _retry_admin(statement: str, admin, attempts: int = 20) -> None:
    """Run a CREATE/DROP DATABASE, retrying while a background Postgres process (autovacuum)
    briefly holds the template or the new database. That is a machine-level race, not a bug."""
    for attempt in range(attempts):
        try:
            with admin.connect() as conn:
                conn.execute(text(statement))
            return
        except (ProgrammingError, OperationalError) as exc:
            transient = any(
                marker in str(exc)
                for marker in ("permission denied to terminate", "being accessed by other users")
            )
            if not transient or attempt == attempts - 1:
                raise
            time.sleep(0.5)


@pytest.fixture
def empty_db():
    """A brand-new empty database, dropped afterwards. Yields its URL."""
    base = make_url(get_settings().database_url)
    name = f"smart_todo_test_{uuid.uuid4().hex[:12]}"
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    _retry_admin(f'CREATE DATABASE "{name}"', admin)
    url = base.set(database=name).render_as_string(hide_password=False)
    try:
        yield url
    finally:
        _retry_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)', admin)
        admin.dispose()


@pytest.fixture
def engine(empty_db):
    """Engine for a temp database migrated to head."""
    command.upgrade(alembic_cfg(empty_db), "head")
    eng = create_engine(empty_db)
    yield eng
    eng.dispose()


@pytest.fixture
def db_session(engine):
    with sessionmaker(engine, expire_on_commit=False)() as session:
        yield session


# ---- API clients shared by the API and scheduler tests ----


@pytest.fixture
def client_factory(engine):
    def override_session():
        with sessionmaker(engine, expire_on_commit=False)() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    clients = []

    def make(user_id: int | None = None) -> TestClient:
        c = TestClient(app, follow_redirects=False)
        if user_id is not None:
            c.cookies.set("session", sign({"uid": user_id}, "session"))
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.close()
    app.dependency_overrides.clear()


def new_user(engine, email) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text("INSERT INTO users (email) VALUES (:e) RETURNING id"), {"e": email}
        ).scalar_one()


@pytest.fixture
def uid(engine):
    return new_user(engine, "a@example.test")


@pytest.fixture
def uid_b(engine):
    return new_user(engine, "b@example.test")


@pytest.fixture
def api(client_factory, uid):
    return client_factory(uid)


@pytest.fixture
def api_b(client_factory, uid_b):
    return client_factory(uid_b)
