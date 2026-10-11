from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "postgresql+psycopg://smart_todo:smart_todo@localhost:5432/smart_todo"
    app_env: str = "dev"
    display_timezone: str = "Asia/Kolkata"

    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    google_redirect_uri: str = "http://localhost:8000/auth/callback"
    fernet_key: SecretStr = SecretStr("")
    session_secret: SecretStr = SecretStr("")
    cookie_secure: bool = False
    session_max_age_seconds: int = 14 * 24 * 3600
    post_login_redirect: str = "/me"

    # Background jobs. Off by default so tests and ad-hoc imports never start a scheduler.
    scheduler_enabled: bool = False
    sms_enabled: bool = False  # SMS to Indian numbers generally needs DLT registration

    # Where links in emails point (the "Mark as done" page). Must be the public HTTPS URL in prod.
    public_base_url: str = "http://localhost:8000"

    # Email via SMTP; leave smtp_host empty to disable (the channel then only logs).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: str = "starttls"  # "starttls" | "ssl"
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    email_from: str = ""

    # Web Push (VAPID). Leave the keys empty to disable (the channel then only logs).
    vapid_public_key: str = ""
    vapid_private_key: SecretStr = SecretStr("")
    vapid_subject: str = ""

    # Where the dashboard lives; a test push opens it.
    frontend_url: str = "http://localhost:3000"

    calendar_enabled: bool = True  # events are only attempted for users who have a Google token
    # Serves /dev/push-test and /dev/sw.js so Web Push can be tried before the dashboard exists.
    dev_push_test_page: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
