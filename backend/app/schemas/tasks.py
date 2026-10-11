"""Request/response models for the task, review, quarantine and settings routes."""

import re
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.sources import MAX_NAME_CHARS

IST = ZoneInfo("Asia/Kolkata")
MAX_LINK_CHARS = 2000
MAX_LIST_ENTRIES = 200

Priority = Literal["urgent", "important", "regular"]
Status = Literal["open", "done", "needs_review", "dismissed"]
Source = Literal[
    "manual",
    "nptel",
    "digii",
    "internship",
    "custom",
    "keyword",
    "gform",
    "college",
    "other",
    "tracked",
]
SourceType = Literal["nptel", "digii", "internship", "custom"]
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


class SubtaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    done: bool
    position: int


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
    source_name: str | None = None
    origin: Literal["manual", "email"] = "email"
    tag: str | None = None
    reminders_enabled: bool = True
    snoozed_until: datetime | None = None
    subtasks: list[SubtaskOut] = []
    subtasks_done: int = 0
    subtasks_total: int = 0
    status: Status
    link: str | None
    link_type: Literal["action", "gmail"] | None
    link_domain: str | None
    link_confidence: float | None
    confidence: float | None
    sender_email: str | None
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
            source_name=task.source_name,
            origin="manual" if task.source == "manual" else "email",
            tag=task.tag,
            reminders_enabled=task.reminders_enabled,
            snoozed_until=task.snoozed_until,
            subtasks=[SubtaskOut.model_validate(s) for s in task.subtasks],
            subtasks_done=sum(1 for s in task.subtasks if s.done),
            subtasks_total=len(task.subtasks),
            status=task.status,
            link=task.link,
            link_type=task.link_type,
            link_domain=link_domain(task.link),
            link_confidence=task.link_confidence,
            confidence=task.confidence,
            sender_email=task.sender_email,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )


def _clean_subtask(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise ValueError("a subtask needs a title")
    if len(value) > 300:
        raise ValueError("a subtask title must be at most 300 characters")
    return value


def _clean_title(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    if not value:
        raise ValueError("title must not be blank")
    return value


MAX_TAG_CHARS = 40
MAX_SUBTASKS = 100


def _clean_tag(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    if len(value) > MAX_TAG_CHARS:
        raise ValueError(f"tag must be at most {MAX_TAG_CHARS} characters")
    return value or None


class TaskCreate(BaseModel):
    title: str = Field(max_length=500)
    tag: str | None = None
    reminders_enabled: bool = True
    subtasks: list[str] = Field(default_factory=list, max_length=MAX_SUBTASKS)
    notes: str | None = Field(default=None, max_length=5000)
    deadline: datetime | None = None
    priority: Priority | None = None  # default: urgent if due within 48h, otherwise regular
    link: str | None = None

    _title = field_validator("title")(_clean_title)
    _link = field_validator("link")(validate_http_url)
    _tag = field_validator("tag")(_clean_tag)

    @field_validator("subtasks")
    @classmethod
    def _subtasks(cls, values: list[str]) -> list[str]:
        return [_clean_subtask(v) for v in values]


class TaskUpdate(BaseModel):
    """Only the fields that are sent change. null clears deadline, notes and link."""

    title: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=5000)
    deadline: datetime | None = None
    priority: Priority | None = None
    status: SettableStatus | None = None
    link: str | None = None
    tag: str | None = None
    reminders_enabled: bool | None = None
    snoozed_until: datetime | None = None  # hides the task until then; null shows it again

    _title = field_validator("title")(_clean_title)
    _link = field_validator("link")(validate_http_url)
    _tag = field_validator("tag")(_clean_tag)


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


_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DOMAIN = rf"(?:{_LABEL}\.)+[a-z]{{2,63}}"
_DOMAIN_RE = re.compile(rf"^{_DOMAIN}$")
_EMAIL_RE = re.compile(rf"^[a-z0-9][a-z0-9._%+-]{{0,63}}@{_DOMAIN}$")


def _sender_list(values: list[str] | None) -> list[str] | None:
    """Always-track entries must be a real domain (nptel.ac.in) or a full address (a@b.com)."""
    if values is None:
        return None
    if len(values) > MAX_LIST_ENTRIES:
        raise ValueError(f"at most {MAX_LIST_ENTRIES} entries")
    out: list[str] = []
    for raw in values:
        entry = raw.strip().lower().removeprefix("@") if isinstance(raw, str) else ""
        if not (_DOMAIN_RE.fullmatch(entry) or _EMAIL_RE.fullmatch(entry)):
            raise ValueError(f"'{str(raw)[:60]}' is not a valid domain or email address")
        if entry not in out:
            out.append(entry)
    return out


MAX_SOURCES = 20
MAX_KEYWORDS = 50


class SourceBody(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME_CHARS)
    type: SourceType
    senders: list[str] = Field(default_factory=list)

    _senders = field_validator("senders")(_sender_list)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("a source needs a name")
        return value


_KEYWORD_RE = re.compile(r"^[a-z0-9](?:[a-z0-9 -]{0,58}[a-z0-9])?$")


def _keyword_list(values: list[str]) -> list[str]:
    if len(values) > MAX_KEYWORDS:
        raise ValueError(f"at most {MAX_KEYWORDS} keywords")
    out: list[str] = []
    for raw in values:
        entry = " ".join(raw.lower().split()) if isinstance(raw, str) else ""
        if not _KEYWORD_RE.fullmatch(entry):
            raise ValueError(f"'{str(raw)[:60]}' is not a valid keyword (letters, numbers, spaces)")
        if entry not in out:
            out.append(entry)
    return out


class SettingsBody(BaseModel):
    sources: list[SourceBody]
    watch_keywords: list[str]
    block_senders: list[str]
    require_deadline: bool = False
    digest_hour: int = Field(ge=0, le=23)
    notices: list[str] = []  # filled in answers to PUT: what the change did elsewhere

    _keywords = field_validator("watch_keywords")(_keyword_list)
    _block = field_validator("block_senders")(_sender_list)

    @field_validator("sources")
    @classmethod
    def _sources(cls, values: list[SourceBody]) -> list[SourceBody]:
        if len(values) > MAX_SOURCES:
            raise ValueError(f"at most {MAX_SOURCES} sources")
        names = [v.name.lower() for v in values]
        if len(set(names)) != len(names):
            raise ValueError("two sources have the same name")
        return values


class SubtaskCreate(BaseModel):
    title: str

    _title = field_validator("title")(_clean_subtask)


class SubtaskUpdate(BaseModel):
    title: str | None = None
    done: bool | None = None

    @field_validator("title")
    @classmethod
    def _title(cls, value: str | None) -> str | None:
        return None if value is None else _clean_subtask(value)


class SubtaskReorder(BaseModel):
    ids: list[int] = Field(max_length=MAX_SUBTASKS)


class SubtaskChange(BaseModel):
    """Result of a subtask change. `all_subtasks_done` is true when every subtask is now ticked:
    the UI then asks "Mark the whole task done?". The task itself is never completed here."""

    subtask: SubtaskOut | None
    all_subtasks_done: bool
    task: TaskOut


class EmailOut(BaseModel):
    """An email read live from Gmail for the "View full email" window (never stored)."""

    subject: str
    sender: str
    received_at: datetime | None
    body_text: str
    truncated: bool
    gmail_url: str
