"""smart folders: folders, folder_pins, tasks.snoozed_until

Revision ID: 0010
Revises: 0009
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("snoozed_until", sa.DateTime(timezone=True)))
    op.create_table(
        "folders",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_folders_user_id_users"),
            nullable=False,
        ),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("emoji", sa.String(16), nullable=False, server_default=""),
        sa.Column("color", sa.String(16), nullable=False, server_default="#2563eb"),
        sa.Column("position", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("rules", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("show_completed", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_folders_user_id", "folders", ["user_id"])
    op.create_table(
        "folder_pins",
        sa.Column(
            "folder_id",
            sa.BigInteger,
            sa.ForeignKey("folders.id", ondelete="CASCADE", name="fk_folder_pins_folder_id_folders"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            sa.BigInteger,
            sa.ForeignKey("tasks.id", ondelete="CASCADE", name="fk_folder_pins_task_id_tasks"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("folder_id", "task_id", name="pk_folder_pins"),
    )
    op.create_index("ix_folder_pins_task_id", "folder_pins", ["task_id"])


def downgrade() -> None:
    op.drop_table("folder_pins")
    op.drop_table("folders")
    op.drop_column("tasks", "snoozed_until")
