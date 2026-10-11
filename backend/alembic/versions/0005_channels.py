"""notification channels: push subscriptions, used mark-done tokens, calendar sync state

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey(
                "users.id", ondelete="CASCADE", name="fk_push_subscriptions_user_id_users"
            ),
            nullable=False,
        ),
        sa.Column("endpoint", sa.Text, nullable=False),
        sa.Column("p256dh", sa.Text, nullable=False),
        sa.Column("auth", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.UniqueConstraint("endpoint", name="uq_push_subscriptions_endpoint"),
    )
    op.create_index("ix_push_subscriptions_user_id", "push_subscriptions", ["user_id"])

    op.create_table(
        "used_action_tokens",
        sa.Column("nonce", sa.String(64), primary_key=True),
        sa.Column(
            "task_id",
            sa.BigInteger,
            sa.ForeignKey(
                "tasks.id", ondelete="CASCADE", name="fk_used_action_tokens_task_id_tasks"
            ),
        ),
        sa.Column("used_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )

    op.create_table(
        "calendar_orphans",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_calendar_orphans_user_id_users"),
            nullable=False,
        ),
        sa.Column("event_id", sa.String(255), nullable=False),
        sa.Column("attempts", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    )
    op.create_index("ix_calendar_orphans_user_id", "calendar_orphans", ["user_id"])

    op.add_column("tasks", sa.Column("gcal_synced_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("tasks", "gcal_synced_at")
    op.drop_table("calendar_orphans")
    op.drop_table("used_action_tokens")
    op.drop_table("push_subscriptions")
