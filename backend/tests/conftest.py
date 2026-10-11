import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from alembic import command
from app.auth import credentials
from app.config import get_settings
from tests.helpers import alembic_cfg


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    """Tests never depend on (or touch) the real Google credentials or keys in .env."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret-" + "x" * 32)
    monkeypatch.setenv("COOKIE_SECURE", "false")
    get_settings.cache_clear()
    credentials._access_cache.clear()
    yield
    get_settings.cache_clear()
    credentials._access_cache.clear()


@pytest.fixture
def empty_db():
    """A brand-new empty database, dropped afterwards. Yields its URL."""
    base = make_url(get_settings().database_url)
    name = f"smart_todo_test_{uuid.uuid4().hex[:12]}"
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = base.set(database=name).render_as_string(hide_password=False)
    try:
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
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
