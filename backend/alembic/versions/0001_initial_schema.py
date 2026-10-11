"""initial schema

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

NOW = sa.text("now()")


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=NOW, nullable=False)


def _user_fk(**kw) -> sa.Column:
    return sa.Column("user_id", sa.BigInteger, sa.ForeignKey("users.id", ondelete="CASCADE"), **kw)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text),
        sa.Column("telegram_chat_id", sa.String(64)),
        sa.Column("needs_reauth", sa.Boolean, server_default=sa.text("false"), nullable=False),
        _ts("created_at"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "tasks",
        sa.Column("id", sa.BigInteger, primary_key=True),
        _user_fk(nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("source", sa.String(16), server_default="other", nullable=False),
        sa.Column("source_ref", sa.String(255)),
        sa.Column("link", sa.Text),
        sa.Column("priority", sa.String(16), server_default="regular", nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), server_default="open", nullable=False),
        sa.Column("confidence", sa.Float),
        sa.Column("gcal_event_id", sa.String(255)),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("user_id", "source_ref", name="uq_tasks_user_id_source_ref"),
        sa.CheckConstraint(
            "source IN ('manual', 'nptel', 'internship', 'gform', 'college', 'other')",
            name="source",
        ),
        sa.CheckConstraint(
            "priority IN ('urgent', 'important', 'regular')", name="priority"
        ),
        sa.CheckConstraint(
            "status IN ('open', 'done', 'needs_review', 'dismissed')", name="status"
        ),
    )
    op.create_index("ix_tasks_user_id", "tasks", ["user_id"])
    op.create_table(
        "reminders",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "task_id", sa.BigInteger, sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("fire_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.UniqueConstraint("task_id", "fire_at", "channel", name="uq_reminders_task_fire_channel"),
    )
    op.create_index("ix_reminders_task_id", "reminders", ["task_id"])
    op.create_table(
        "quarantine",
        sa.Column("id", sa.BigInteger, primary_key=True),
        _user_fk(nullable=False),
        sa.Column("message_id", sa.String(255), nullable=False),
        sa.Column("subject", sa.Text),
        sa.Column("sender", sa.Text),
        sa.Column("score", sa.Float),
        sa.Column(
            "reasons", postgresql.JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column("status", sa.String(16), server_default="held", nullable=False),
        _ts("created_at"),
        sa.CheckConstraint(
            "status IN ('held', 'rescued', 'confirmed_scam')", name="status"
        ),
    )
    op.create_index("ix_quarantine_user_id", "quarantine", ["user_id"])
    op.create_table(
        "sync_state",
        _user_fk(primary_key=True),
        sa.Column("history_id", sa.String(64)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("watch_expires_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "user_settings",
        _user_fk(primary_key=True),
        sa.Column(
            "denylist", postgresql.JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column(
            "allowlist", postgresql.JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column("llm_enabled", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("digest_hour", sa.Integer, server_default=sa.text("8"), nullable=False),
    )
    op.create_table(
        "ingest_failures",
        sa.Column("id", sa.BigInteger, primary_key=True),
        _user_fk(nullable=False),
        sa.Column("message_id", sa.String(255), nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("attempts", sa.Integer, server_default=sa.text("1"), nullable=False),
        _ts("created_at"),
    )
    op.create_index("ix_ingest_failures_user_id", "ingest_failures", ["user_id"])


def downgrade() -> None:
    for table in (
        "ingest_failures",
        "user_settings",
        "sync_state",
        "quarantine",
        "reminders",
        "tasks",
        "users",
    ):
        op.drop_table(table)
