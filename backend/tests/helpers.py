from pathlib import Path

from alembic.config import Config

BACKEND_DIR = Path(__file__).resolve().parents[1]


def alembic_cfg(url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg
