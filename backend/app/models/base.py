from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# "nptel", "digii", "internship" and "custom" are the source types the user configures;
# "keyword" is a watch-keyword match from an unlisted sender. The rest are kept for older rows.
TASK_SOURCES = (
    "manual", "nptel", "digii", "internship", "custom", "keyword",
    "gform", "college", "other", "tracked",
)  # fmt: skip
TASK_PRIORITIES = ("urgent", "important", "regular")
TASK_STATUSES = ("open", "done", "needs_review", "dismissed")
QUARANTINE_STATUSES = ("held", "rescued", "confirmed_scam")
LINK_TYPES = ("action", "gmail")
REMINDER_KINDS = ("created", "t48", "t24", "t10", "t1", "immediate")
NOTIFICATION_KINDS = ("reminder", "digest")
PROCESSED_DECISIONS = ("passed", "quarantined", "skipped", "failed", "ignored", "dropped")


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
        CheckConstraint(_in("link_type", LINK_TYPES), name="link_type"),
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
    link_type: Mapped[str | None] = mapped_column(String(16))
    link_confidence: Mapped[float | None] = mapped_column(Float)
    source_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sender_email: Mapped[str | None] = mapped_column(String(320))  # address only, no body
    source_name: Mapped[str | None] = mapped_column(
        String(64)
    )  # "NPTEL", "Digii", "Keyword: hackathon"
    # Written by hand, confirmed, edited or finished by the user: a rebuild never deletes it.
    user_confirmed: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tag: Mapped[str | None] = mapped_column(Text)  # free text; suggestions come from tags in use
    reminders_enabled: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true"), nullable=False
    )
    priority: Mapped[str] = mapped_column(String(16), server_default="regular", nullable=False)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), server_default="open", nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    gcal_event_id: Mapped[str | None] = mapped_column(String(255))
    gcal_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()
    subtasks: Mapped[list["Subtask"]] = relationship(
        order_by="Subtask.position, Subtask.id",
        lazy="selectin",
        passive_deletes=True,
        cascade="all, delete-orphan",
    )


class Folder(Base):
    """A saved view: tasks matching `rules` (computed when read, never copied) plus pinned ones."""

    __tablename__ = "folders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    emoji: Mapped[str] = mapped_column(String(16), server_default="", nullable=False)
    color: Mapped[str] = mapped_column(String(16), server_default="#2563eb", nullable=False)
    position: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    rules: Mapped[Any] = mapped_column(JSONB, server_default=text("'{}'::jsonb"), nullable=False)
    show_completed: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )
    created_at: Mapped[datetime] = _ts()


class FolderPin(Base):
    """A task put into a folder by hand. Deleted with either the folder or the task."""

    __tablename__ = "folder_pins"

    folder_id: Mapped[int] = mapped_column(
        ForeignKey("folders.id", ondelete="CASCADE"), primary_key=True
    )
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class Subtask(Base):
    """A checklist item on a task (manual or email). Deleted with its task."""

    __tablename__ = "subtasks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    done: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    created_at: Mapped[datetime] = _ts()


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (
        UniqueConstraint("task_id", "fire_at", "channel", name="uq_reminders_task_fire_channel"),
        CheckConstraint(_in("kind", REMINDER_KINDS), name="kind"),
        Index("ix_reminders_due", "fire_at", postgresql_where=text("sent_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    kind: Mapped[str | None] = mapped_column(String(16))  # ladder step; drives the wording


class Notification(Base):
    """In-app notification (the bell). One per sent reminder, plus daily digests."""

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("reminder_id", name="uq_notifications_reminder_id"),
        CheckConstraint(_in("kind", NOTIFICATION_KINDS), name="kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    reminder_id: Mapped[int | None] = mapped_column(ForeignKey("reminders.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(16), server_default="reminder", nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(Text)
    read: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    created_at: Mapped[datetime] = _ts()


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


class PushSubscription(Base):
    """A browser's Web Push subscription. The endpoint is the identity of one browser profile."""

    __tablename__ = "push_subscriptions"
    __table_args__ = (UniqueConstraint("endpoint", name="uq_push_subscriptions_endpoint"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    p256dh: Mapped[str] = mapped_column(Text, nullable=False)
    auth: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _ts()


class UsedActionToken(Base):
    """Nonces of "Mark as done" email links that have been used (each link works once)."""

    __tablename__ = "used_action_tokens"

    nonce: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    used_at: Mapped[datetime] = _ts()


class CalendarOrphan(Base):
    """A Google Calendar event whose task is gone but whose deletion failed; retried by a job."""

    __tablename__ = "calendar_orphans"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    created_at: Mapped[datetime] = _ts()


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
    # The only places mail is fetched from: [{"name", "type", "senders": [domain or address]}].
    sources: Mapped[Any] = mapped_column(JSONB, server_default=text("'[]'::jsonb"), nullable=False)
    # Words that make mail from an unlisted sender worth a look (hackathon, ...).
    watch_keywords: Mapped[Any] = mapped_column(
        JSONB,
        server_default=text('\'["hackathon", "ideathon", "datathon"]\'::jsonb'),
        nullable=False,
    )
    # Domains/addresses that never become tasks, even when a keyword matches.
    block_senders: Mapped[Any] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb"), nullable=False
    )
    # If true, mail without a deadline is dropped instead of becoming a task with none.
    require_deadline: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
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
