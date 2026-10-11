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


@lru_cache
def get_settings() -> Settings:
    return Settings()
