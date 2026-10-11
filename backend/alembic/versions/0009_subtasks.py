"""subtasks, tasks.tag, tasks.reminders_enabled

tasks.notes already exists (since migration 0001), so only tag and reminders_enabled are added.

Revision ID: 0009
Revises: 0008
"""
import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("tag", sa.Text))
    op.add_column(
        "tasks",
        sa.Column("reminders_enabled", sa.Boolean, server_default=sa.text("true"), nullable=False),
    )
    op.create_table(
        "subtasks",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "task_id",
            sa.BigInteger,
            sa.ForeignKey("tasks.id", ondelete="CASCADE", name="fk_subtasks_task_id_tasks"),
            nullable=False,
        ),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("done", sa.Boolean, server_default=sa.text("false"), nullable=False),
        sa.Column("position", sa.Integer, server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_subtasks_task_id", "subtasks", ["task_id"])


def downgrade() -> None:
    op.drop_table("subtasks")
    op.drop_column("tasks", "reminders_enabled")
    op.drop_column("tasks", "tag")
