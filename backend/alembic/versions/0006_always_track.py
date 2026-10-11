"""always_track sender list; tasks.source gains 'tracked'

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

OLD = "('manual', 'nptel', 'internship', 'gform', 'college', 'other')"
NEW = "('manual', 'nptel', 'internship', 'gform', 'college', 'other', 'tracked')"


def upgrade() -> None:
    op.add_column(
        "user_settings",
        sa.Column(
            "always_track",
            postgresql.JSONB,
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.drop_constraint("source", "tasks", type_="check")
    op.create_check_constraint("source", "tasks", f"source IN {NEW}")


def downgrade() -> None:
    op.execute("UPDATE tasks SET source = 'other' WHERE source = 'tracked'")
    op.drop_constraint("source", "tasks", type_="check")
    op.create_check_constraint("source", "tasks", f"source IN {OLD}")
    op.drop_column("user_settings", "always_track")
