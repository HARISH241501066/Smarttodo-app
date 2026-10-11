from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

TASK_SOURCES = ("manual", "nptel", "internship", "gform", "college", "other")
TASK_PRIORITIES = ("urgent", "important", "regular")
TASK_STATUSES = ("open", "done", "needs_review", "dismissed")
QUARANTINE_STATUSES = ("held", "rescued", "confirmed_scam")
PROCESSED_DECISIONS = ("passed", "quarantined", "skipped", "failed")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64))
    needs_reauth: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )
    created_at: Mapped[datetime] = _ts()


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("user_id", "source_ref", name="uq_tasks_user_id_source_ref"),
        CheckConstraint(_in("source", TASK_SOURCES), name="source"),
        CheckConstraint(_in("priority", TASK_PRIORITIES), name="priority"),
        CheckConstraint(_in("status", TASK_STATUSES), name="status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), server_default="other", nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(255))
    link: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(16), server_default="regular", nullable=False)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), server_default="open", nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    gcal_event_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (
        UniqueConstraint("task_id", "fire_at", "channel", name="uq_reminders_task_fire_channel"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)


class Quarantine(Base):
    __tablename__ = "quarantine"
    __table_args__ = (
        UniqueConstraint("user_id", "message_id", name="uq_quarantine_user_id_message_id"),
        CheckConstraint(_in("status", QUARANTINE_STATUSES), name="status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str | None] = mapped_column(Text)
    sender: Mapped[str | None] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Float)
    reasons: Mapped[Any] = mapped_column(JSONB, server_default=text("'[]'::jsonb"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), server_default="held", nullable=False)
    created_at: Mapped[datetime] = _ts()


class ProcessedMessage(Base):
    """Idempotency record: one row per handled message. Never holds bodies, subjects or senders."""

    __tablename__ = "processed_messages"
    __table_args__ = (CheckConstraint(_in("decision", PROCESSED_DECISIONS), name="decision"),)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    message_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))
    processed_at: Mapped[datetime] = _ts()


class SyncState(Base):
    __tablename__ = "sync_state"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    history_id: Mapped[str | None] = mapped_column(String(64))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    watch_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    denylist: Mapped[Any] = mapped_column(JSONB, server_default=text("'[]'::jsonb"), nullable=False)
    allowlist: Mapped[Any] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"), nullable=False
    )
    llm_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    digest_hour: Mapped[int] = mapped_column(Integer, server_default=text("8"), nullable=False)


class IngestFailure(Base):
    __tablename__ = "ingest_failures"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("1"), nullable=False)
    created_at: Mapped[datetime] = _ts()
