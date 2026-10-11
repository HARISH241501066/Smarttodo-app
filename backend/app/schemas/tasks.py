"""Request/response models for the task, review, quarantine and settings routes."""

import re
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ingest.query import normalize_denylist

IST = ZoneInfo("Asia/Kolkata")
MAX_LINK_CHARS = 2000
MAX_LIST_ENTRIES = 200

Priority = Literal["urgent", "important", "regular"]
Status = Literal["open", "done", "needs_review", "dismissed"]
Source = Literal["manual", "nptel", "internship", "gform", "college", "other"]
SettableStatus = Literal["open", "done", "dismissed"]


def validate_http_url(value: str | None) -> str | None:
    """Only absolute http(s) URLs with a host; javascript:, data: and the like are refused."""
    if value is None:
        return None
    url = value.strip()
    if not url:
        return None
    if len(url) > MAX_LINK_CHARS or re.search(r"[\s\x00-\x1f\x7f]", url):
        raise ValueError("link must be a single http or https URL")
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise ValueError("link must be an http or https URL")
    return url


def to_utc(value: datetime | None) -> datetime | None:
    """Naive datetimes are read as IST; everything is stored as UTC."""
    if value is None:
        return None
    return (value.replace(tzinfo=IST) if value.tzinfo is None else value).astimezone(UTC)


def link_domain(url: str | None) -> str | None:
    host = (urlparse(url).hostname or "").lower() if url else ""
    return host.removeprefix("www.") or None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    notes: str | None
    deadline: datetime | None  # UTC
    deadline_ist: datetime | None  # the same instant in Asia/Kolkata
    deadline_display: str | None  # e.g. "Thu, 22 Jan 2026, 11:59 PM IST"
    priority: Priority
    source: Source
    status: Status
    link: str | None
    link_type: Literal["action", "gmail"] | None
    link_domain: str | None
    link_confidence: float | None
    confidence: float | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_task(cls, task: Any) -> "TaskOut":
        deadline = task.deadline.astimezone(UTC) if task.deadline else None
        ist = deadline.astimezone(IST) if deadline else None
        return cls(
            id=task.id,
            title=task.title,
            notes=task.notes,
            deadline=deadline,
            deadline_ist=ist,
            deadline_display=ist.strftime("%a, %d %b %Y, %I:%M %p IST") if ist else None,
            priority=task.priority,
            source=task.source,
            status=task.status,
            link=task.link,
            link_type=task.link_type,
            link_domain=link_domain(task.link),
            link_confidence=task.link_confidence,
            confidence=task.confidence,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )


def _clean_title(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    if not value:
        raise ValueError("title must not be blank")
    return value


class TaskCreate(BaseModel):
    title: str = Field(max_length=500)
    notes: str | None = Field(default=None, max_length=5000)
    deadline: datetime | None = None
    priority: Priority | None = None  # default: urgent if due within 48h, otherwise regular
    link: str | None = None

    _title = field_validator("title")(_clean_title)
    _link = field_validator("link")(validate_http_url)


class TaskUpdate(BaseModel):
    """Only the fields that are sent change. null clears deadline, notes and link."""

    title: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=5000)
    deadline: datetime | None = None
    priority: Priority | None = None
    status: SettableStatus | None = None
    link: str | None = None

    _title = field_validator("title")(_clean_title)
    _link = field_validator("link")(validate_http_url)


class ReviewConfirm(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    deadline: datetime | None = None
    link: str | None = None

    _title = field_validator("title")(_clean_title)
    _link = field_validator("link")(validate_http_url)


class QuarantineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    message_id: str
    subject: str | None
    sender: str | None
    score: float | None
    reasons: list[str]
    status: Literal["held", "rescued", "confirmed_scam"]
    created_at: datetime


class RescueOut(BaseModel):
    quarantine: QuarantineOut
    task: TaskOut | None
    task_created: bool  # False when the message was folded into an existing task


def _domain_list(values: list[str]) -> list[str]:
    if len(values) > MAX_LIST_ENTRIES:
        raise ValueError(f"at most {MAX_LIST_ENTRIES} entries")
    cleaned = normalize_denylist(values)
    stripped = {v.strip().lower().removeprefix("@") for v in values if isinstance(v, str)}
    if not set(cleaned) >= stripped:
        raise ValueError("entries must be plain domains or addresses, e.g. example.com")
    return cleaned


class SettingsBody(BaseModel):
    denylist: list[str]
    allowlist: list[str]
    digest_hour: int = Field(ge=0, le=23)

    _denylist = field_validator("denylist")(_domain_list)
    _allowlist = field_validator("allowlist")(_domain_list)
