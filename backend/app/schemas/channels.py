"""Models for the notification and push routes."""

from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_ENDPOINT_CHARS = 2000
MAX_KEY_CHARS = 512


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int | None
    kind: str
    title: str
    body: str | None
    link: str | None
    read: bool
    created_at: datetime


class NotificationList(BaseModel):
    unread_count: int
    items: list[NotificationOut]


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=MAX_KEY_CHARS)
    auth: str = Field(min_length=1, max_length=MAX_KEY_CHARS)


def _https_endpoint(value: str) -> str:
    value = value.strip()
    parsed = urlparse(value)
    if len(value) > MAX_ENDPOINT_CHARS or parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("endpoint must be an https URL")
    return value


class PushSubscribe(BaseModel):
    """What the browser's PushSubscription.toJSON() produces (extra fields are ignored)."""

    endpoint: str
    keys: PushKeys

    _endpoint = field_validator("endpoint")(_https_endpoint)


class PushUnsubscribe(BaseModel):
    endpoint: str

    _endpoint = field_validator("endpoint")(_https_endpoint)
