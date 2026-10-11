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


def _signer(salt: str) -> URLSafeTimedSerializer:
    secret = get_settings().session_secret.get_secret_value()
    if not secret:
        raise RuntimeError("SESSION_SECRET is not configured")
    return URLSafeTimedSerializer(secret, salt=salt)


def sign(payload: dict[str, Any], salt: str) -> str:
    return _signer(salt).dumps(payload)


def unsign(token: str, salt: str, max_age: int) -> dict[str, Any] | None:
    """Returns the payload, or None if the token is invalid, tampered with or expired."""
    try:
        data = _signer(salt).loads(token, max_age=max_age)
    except BadSignature:
        return None
    return data if isinstance(data, dict) else None
