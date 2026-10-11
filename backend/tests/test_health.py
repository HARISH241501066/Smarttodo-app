from fastapi.testclient import TestClient

from app import db
from app.config import get_settings
from app.main import app

client = TestClient(app)


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "ok"}


def test_health_reports_db_down(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@127.0.0.1:1/x")
    get_settings.cache_clear()
    db.get_engine.cache_clear()
    try:
        assert client.get("/health").json()["db"] == "error"
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        db.get_engine.cache_clear()


def test_config_reads_environment(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test-env")
    get_settings.cache_clear()
    try:
        assert get_settings().app_env == "test-env"
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
