"""Models for the Updates tab, the review re-sort and the per-sender bulk actions."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.tasks import Source


class UpdateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    source: Source
    link: str | None
    link_type: Literal["action", "gmail"] | None
    link_domain: str | None = None
    received_at: datetime | None
    created_at: datetime


class ReprocessOut(BaseModel):
    total: int  # review items looked at in this batch
    task: int  # moved to your tasks (open)
    update: int  # moved to Updates
    ignore: int  # removed: one-time codes, receipts, security notices, ...
    review: int  # still need a person
    gone: int  # no longer in Gmail; left where they were
    next_after_id: int | None  # call again with after_id=this until it is null


class BulkReview(BaseModel):
    action: Literal["confirm", "dismiss"]
    sender: str = Field(min_length=3, max_length=320)

    @field_validator("sender")
    @classmethod
    def _clean(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value or " " in value:
            raise ValueError("sender must be an email address")
        return value


class BulkOut(BaseModel):
    updated: int
