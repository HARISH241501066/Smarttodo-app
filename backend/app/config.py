from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "postgresql+psycopg://smart_todo:smart_todo@localhost:5432/smart_todo"
    # ENV=production switches on the production behaviour (see is_production).
    app_env: str = Field("dev", validation_alias=AliasChoices("ENV", "APP_ENV"))
    display_timezone: str = "Asia/Kolkata"

    # The built dashboard (static files) that FastAPI serves. Empty = API only (development).
    frontend_dist: str = ""
    # Gmail push (Pub/Sub). Polling every 15 minutes is the only mode in production.
    pubsub_mode: str = "off"

    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    google_redirect_uri: str = "http://localhost:8000/auth/callback"
    fernet_key: SecretStr = SecretStr("")
    # May hold several comma-separated secrets, newest first: the first signs, all verify.
    session_secret: SecretStr = SecretStr("")
    # Signs the "Mark as done" email links. Empty means SESSION_SECRET is used. Same rotation rule.
    mark_done_secret: SecretStr = SecretStr("")
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

    @field_validator("database_url")
    @classmethod
    def _driver(cls, value: str) -> str:
        """Hosts (Neon, Render) hand out postgres:// or postgresql:// URLs; we use psycopg 3."""
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix) :]
        return value

    @property
    def effective_pubsub_mode(self) -> str:
        return "off" if self.is_production else self.pubsub_mode

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in ("production", "prod")

    @property
    def secure_cookies(self) -> bool:
        return self.cookie_secure or self.is_production

    @property
    def dev_page_enabled(self) -> bool:
        """The dev push page is forced off in production, whatever the flag says."""
        return self.dev_push_test_page and not self.is_production

    @property
    def allowed_origins(self) -> frozenset[str]:
        """Origins a browser may send state-changing requests from: the dashboard, and the public
        base URL (the "Mark as done" page posts to itself)."""
        out = set()
        for url in (self.frontend_url, self.public_base_url):
            parts = urlparse(url)
            if parts.scheme and parts.netloc:
                out.add(f"{parts.scheme}://{parts.netloc}")
        return frozenset(out)


@lru_cache
def get_settings() -> Settings:
    return Settings()
