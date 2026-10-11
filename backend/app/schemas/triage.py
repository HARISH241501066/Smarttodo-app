"""Models for the per-sender bulk actions on the review queue."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


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
