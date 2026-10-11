"""notifications table; reminders.kind

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reminders", sa.Column("kind", sa.String(16)))
    op.create_check_constraint(
        "kind", "reminders", "kind IN ('created', 't48', 't24', 't10', 't1', 'immediate')"
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_notifications_user_id_users"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.BigInteger,
            sa.ForeignKey("tasks.id", ondelete="CASCADE", name="fk_notifications_task_id_tasks"),
        ),
        sa.Column(
            "reminder_id",
            sa.BigInteger,
            sa.ForeignKey(
                "reminders.id", ondelete="SET NULL", name="fk_notifications_reminder_id_reminders"
            ),
        ),
        sa.Column("kind", sa.String(16), server_default="reminder", nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("body", sa.Text),
        sa.Column("link", sa.Text),
        sa.Column("read", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("reminder_id", name="uq_notifications_reminder_id"),
        sa.CheckConstraint("kind IN ('reminder', 'digest')", name="kind"),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_task_id", "notifications", ["task_id"])
    # the dispatcher's hot query
    op.create_index(
        "ix_reminders_due", "reminders", ["fire_at"], postgresql_where=sa.text("sent_at IS NULL")
    )


def downgrade() -> None:
    op.drop_index("ix_reminders_due", table_name="reminders")
    op.drop_table("notifications")
    op.drop_constraint("kind", "reminders", type_="check")
    op.drop_column("reminders", "kind")
