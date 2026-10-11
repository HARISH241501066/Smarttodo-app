"""Request/response models for smart folders."""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.tasks import Priority, TaskOut

MAX_RULE_ITEMS = 30
MAX_FOLDERS = 50
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _clean_list(values: list[str], limit: int = MAX_RULE_ITEMS) -> list[str]:
    out: list[str] = []
    for raw in values:
        value = " ".join(raw.split())
        if not value:
            raise ValueError("entries must not be blank")
        if len(value) > 60:
            raise ValueError("entries must be at most 60 characters")
        if value.lower() not in [v.lower() for v in out]:
            out.append(value)
    if len(out) > limit:
        raise ValueError(f"at most {limit} entries")
    return out


class FolderRules(BaseModel):
    """OR inside one list, AND across the lists; a list left empty is ignored. With no rules at
    all a folder shows only the tasks pinned to it."""

    sources: list[str] = Field(default_factory=list)  # your source names, "hackathon", "manual"
    tags: list[str] = Field(default_factory=list)
    priorities: list[Priority] = Field(default_factory=list)
    title_contains: str | None = Field(default=None, max_length=100)

    @field_validator("sources", "tags")
    @classmethod
    def _lists(cls, values: list[str]) -> list[str]:
        return _clean_list(values)

    @field_validator("priorities")
    @classmethod
    def _priorities(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))

    @field_validator("title_contains")
    @classmethod
    def _title(cls, value: str | None) -> str | None:
        value = " ".join((value or "").split())
        return value or None

    @property
    def empty(self) -> bool:
        return not (self.sources or self.tags or self.priorities or self.title_contains)


class FolderBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    emoji: str = Field(default="", max_length=16)
    color: str = "#2563eb"
    rules: FolderRules = Field(default_factory=FolderRules)
    show_completed: bool = False

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("a folder needs a name")
        return value

    @field_validator("emoji")
    @classmethod
    def _emoji(cls, value: str) -> str:
        return value.strip()

    @field_validator("color")
    @classmethod
    def _color(cls, value: str) -> str:
        if not _COLOR_RE.fullmatch(value):
            raise ValueError("color must look like #2563eb")
        return value.lower()


class FolderPatch(BaseModel):
    """Only the fields sent change."""

    name: str | None = Field(default=None, min_length=1, max_length=60)
    emoji: str | None = Field(default=None, max_length=16)
    color: str | None = None
    rules: FolderRules | None = None
    show_completed: bool | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        if not value:
            raise ValueError("a folder needs a name")
        return value

    @field_validator("color")
    @classmethod
    def _color(cls, value: str | None) -> str | None:
        if value is not None and not _COLOR_RE.fullmatch(value):
            raise ValueError("color must look like #2563eb")
        return value.lower() if value else value


class FolderOut(BaseModel):
    id: int
    name: str
    emoji: str
    color: str
    position: int
    rules: FolderRules
    show_completed: bool
    open_count: int
    has_overdue: bool
    pinned: bool | None = None  # only when asked about one task (?task_id=)


class FolderReorder(BaseModel):
    ids: list[int] = Field(max_length=MAX_FOLDERS)


GroupKey = Literal["overdue", "today", "tomorrow", "week", "later", "none", "completed"]


class FolderGroup(BaseModel):
    key: GroupKey
    label: str
    tasks: list[TaskOut]


class FolderTasksOut(BaseModel):
    groups: list[FolderGroup]
    pinned_ids: list[int]  # tasks in the folder because you pinned them
    rule_ids: list[int]  # tasks in the folder because they match its rules


class FolderPreview(BaseModel):
    count: int
