"""processed_messages idempotency table; one quarantine row per message

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "processed_messages",
        sa.Column(
            "user_id",
            sa.BigInteger,
            sa.ForeignKey(
                "users.id", ondelete="CASCADE", name="fk_processed_messages_user_id_users"
            ),
            nullable=False,
        ),
        sa.Column("message_id", sa.String(255), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(255)),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id", "message_id", name="pk_processed_messages"),
        sa.CheckConstraint(
            "decision IN ('passed', 'quarantined', 'skipped', 'failed')",
            name="decision",
        ),
    )
    op.create_unique_constraint(
        "uq_quarantine_user_id_message_id", "quarantine", ["user_id", "message_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_quarantine_user_id_message_id", "quarantine", type_="unique")
    op.drop_table("processed_messages")
