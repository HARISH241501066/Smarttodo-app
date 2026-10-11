"""task action links: link_type, link_confidence, source_received_at

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("link_type", sa.String(16)))
    op.add_column("tasks", sa.Column("link_confidence", sa.Float))
    # When the email that created/last updated the task was received; stops an older email
    # (processed later in a backfill) from overwriting a newer extension notice.
    op.add_column("tasks", sa.Column("source_received_at", sa.DateTime(timezone=True)))
    op.create_check_constraint("link_type", "tasks", "link_type IN ('action', 'gmail')")


def downgrade() -> None:
    op.drop_constraint("link_type", "tasks", type_="check")
    op.drop_column("tasks", "source_received_at")
    op.drop_column("tasks", "link_confidence")
    op.drop_column("tasks", "link_type")
