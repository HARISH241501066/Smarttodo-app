"""triage: updates table, processed_messages 'ignored', tasks.sender_email

Revision ID: 0007
Revises: 0006
"""
import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

NOW = sa.text("now()")
SOURCES = "('manual', 'nptel', 'internship', 'gform', 'college', 'other', 'tracked')"
DECISIONS_OLD = "('passed', 'quarantined', 'skipped', 'failed')"
DECISIONS_NEW = "('passed', 'quarantined', 'skipped', 'failed', 'ignored')"


def upgrade() -> None:
    op.drop_constraint("decision", "processed_messages", type_="check")
    op.create_check_constraint("decision", "processed_messages", f"decision IN {DECISIONS_NEW}")

    # Who the mail came from (address only), so one sender's review items can be handled together.
    op.add_column("tasks", sa.Column("sender_email", sa.String(320)))
    op.create_index("ix_tasks_user_sender", "tasks", ["user_id", "sender_email"])

    op.create_table(
        "updates",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_updates_user_id_users"),
            nullable=False,
        ),
        sa.Column("message_id", sa.String(255), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("source", sa.String(16), server_default="other", nullable=False),
        sa.Column("link", sa.Text),
        sa.Column("link_type", sa.String(16)),
        sa.Column("received_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("user_id", "message_id", name="uq_updates_user_id_message_id"),
        sa.CheckConstraint(f"source IN {SOURCES}", name="source"),
        sa.CheckConstraint("link_type IN ('action', 'gmail')", name="link_type"),
    )
    op.create_index("ix_updates_user_id", "updates", ["user_id"])


def downgrade() -> None:
    op.drop_table("updates")
    op.drop_index("ix_tasks_user_sender", table_name="tasks")
    op.drop_column("tasks", "sender_email")
    op.execute("UPDATE processed_messages SET decision = 'passed' WHERE decision = 'ignored'")
    op.drop_constraint("decision", "processed_messages", type_="check")
    op.create_check_constraint("decision", "processed_messages", f"decision IN {DECISIONS_OLD}")
