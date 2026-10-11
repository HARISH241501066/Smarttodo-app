"""Token encryption (Fernet / MultiFernet) and signed cookie payloads."""

from typing import Any

from cryptography.fernet import Fernet, MultiFernet
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import get_settings


def _fernet() -> MultiFernet:
    """FERNET_KEY may hold several comma-separated keys, newest first, for rotation."""
    raw = get_settings().fernet_key.get_secret_value()
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise RuntimeError("FERNET_KEY is not configured")
    return MultiFernet([Fernet(k) for k in keys])


def encrypt_token(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Raises cryptography.fernet.InvalidToken if the key is wrong or data is corrupt."""
    return _fernet().decrypt(ciphertext.encode()).decode()


MARK_DONE_SALT = "mark-done"


def _secrets(salt: str) -> list[str]:
    """Signing secrets for a purpose, newest first. Mark-done links may have their own key."""
    s = get_settings()
    raw = ""
    if salt == MARK_DONE_SALT:
        raw = s.mark_done_secret.get_secret_value()
    raw = raw or s.session_secret.get_secret_value()
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        raise RuntimeError("SESSION_SECRET is not configured")
    return keys


def sign(payload: dict[str, Any], salt: str) -> str:
    """Always signs with the newest secret."""
    return URLSafeTimedSerializer(_secrets(salt)[0], salt=salt).dumps(payload)


def unsign(token: str, salt: str, max_age: int) -> dict[str, Any] | None:
    """Returns the payload, or None if the token is invalid, tampered with or expired. Tokens
    signed with any configured (old or new) secret are accepted."""
    for secret in _secrets(salt):
        try:
            data = URLSafeTimedSerializer(secret, salt=salt).loads(token, max_age=max_age)
        except BadSignature:
            continue
        return data if isinstance(data, dict) else None
    return None
