"""source-based scanning: sources, watch keywords, block senders, require_deadline

Replaces the broad "everything except promotions" scan (denylist / allowlist / always_track and
the Updates table) with user-chosen sources. Existing values are migrated, not lost:
    denylist      -> block_senders
    allowlist     -> a custom source named "College"
    always_track  -> sources too (those not already covered by NPTEL), as "Always track"

Revision ID: 0008
Revises: 0007
"""
import json

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

NOW = sa.text("now()")
DEFAULT_KEYWORDS = ["hackathon", "ideathon", "datathon"]
NEW_SOURCES = (
    "('manual', 'nptel', 'digii', 'internship', 'custom', 'keyword', "
    "'gform', 'college', 'other', 'tracked')"
)
OLD_SOURCES = "('manual', 'nptel', 'internship', 'gform', 'college', 'other', 'tracked')"
DECISIONS_NEW = "('passed', 'quarantined', 'skipped', 'failed', 'ignored', 'dropped')"
DECISIONS_OLD = "('passed', 'quarantined', 'skipped', 'failed', 'ignored')"


def _covers(entry: str, senders: list[str]) -> bool:
    domain = entry.rsplit("@", 1)[-1]
    return any(entry == s or domain == s or domain.endswith("." + s) for s in senders if "@" not in s)


def _sources_for(deny: list, allow: list, track: list) -> tuple[list[dict], list[str]]:
    nptel = ["nptel.ac.in"]
    sources = [
        {"name": "NPTEL", "type": "nptel", "senders": nptel},
        {"name": "Digii", "type": "digii", "senders": []},
        {"name": "Internship", "type": "internship", "senders": []},
    ]
    college = [a for a in allow if isinstance(a, str) and a]
    if college:
        sources.append({"name": "College", "type": "custom", "senders": college})
    extra = [t for t in track if isinstance(t, str) and t and not _covers(t, nptel) and t not in college]
    if extra:
        sources.append({"name": "Always track", "type": "custom", "senders": extra})
    taken = {s for src in sources for s in src["senders"]}
    block = [d for d in deny if isinstance(d, str) and d and d not in taken]
    return sources, block


def upgrade() -> None:
    bind = op.get_bind()
    jsonb = postgresql.JSONB
    op.add_column(
        "user_settings",
        sa.Column("sources", jsonb, server_default=sa.text("'[]'::jsonb"), nullable=False),
    )
    op.add_column(
        "user_settings",
        sa.Column(
            "watch_keywords",
            jsonb,
            server_default=sa.text(f"'{json.dumps(DEFAULT_KEYWORDS)}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "user_settings",
        sa.Column("block_senders", jsonb, server_default=sa.text("'[]'::jsonb"), nullable=False),
    )
    op.add_column(
        "user_settings",
        sa.Column("require_deadline", sa.Boolean, server_default=sa.text("false"), nullable=False),
    )

    rows = bind.execute(sa.text("SELECT user_id, denylist, allowlist, always_track FROM user_settings")).all()
    for user_id, deny, allow, track in rows:
        sources, block = _sources_for(deny or [], allow or [], track or [])
        bind.execute(
            sa.text(
                "UPDATE user_settings SET sources = CAST(:s AS jsonb), "
                "block_senders = CAST(:b AS jsonb) WHERE user_id = :u"
            ),
            {"s": json.dumps(sources), "b": json.dumps(block), "u": user_id},
        )
    op.drop_column("user_settings", "denylist")
    op.drop_column("user_settings", "allowlist")
    op.drop_column("user_settings", "always_track")

    # Tasks: which source (by name) and whether the user has taken ownership of it.
    op.add_column("tasks", sa.Column("source_name", sa.String(64)))
    op.add_column(
        "tasks",
        sa.Column("user_confirmed", sa.Boolean, server_default=sa.text("false"), nullable=False),
    )
    # A rebuild must never touch these: written by hand, confirmed or finished by the user.
    op.execute(
        "UPDATE tasks SET user_confirmed = TRUE WHERE source = 'manual' "
        "OR status IN ('done', 'dismissed') OR (confidence = 1.0 AND source <> 'tracked')"
    )
    op.drop_constraint("source", "tasks", type_="check")
    op.create_check_constraint("source", "tasks", f"source IN {NEW_SOURCES}")

    op.drop_constraint("decision", "processed_messages", type_="check")
    op.create_check_constraint("decision", "processed_messages", f"decision IN {DECISIONS_NEW}")

    op.drop_table("updates")  # informational mail is no longer kept: it is simply dropped


def downgrade() -> None:
    bind = op.get_bind()
    jsonb = postgresql.JSONB
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
        sa.CheckConstraint(
            "source IN ('manual', 'nptel', 'internship', 'gform', 'college', 'other', 'tracked')",
            name="source",
        ),
        sa.CheckConstraint("link_type IN ('action', 'gmail')", name="link_type"),
    )
    op.create_index("ix_updates_user_id", "updates", ["user_id"])

    op.execute("UPDATE processed_messages SET decision = 'ignored' WHERE decision = 'dropped'")
    op.drop_constraint("decision", "processed_messages", type_="check")
    op.create_check_constraint("decision", "processed_messages", f"decision IN {DECISIONS_OLD}")

    op.execute(
        "UPDATE tasks SET source = 'other' WHERE source IN ('digii', 'custom', 'keyword')"
    )
    op.drop_constraint("source", "tasks", type_="check")
    op.create_check_constraint("source", "tasks", f"source IN {OLD_SOURCES}")
    op.drop_column("tasks", "user_confirmed")
    op.drop_column("tasks", "source_name")

    for name in ("denylist", "allowlist", "always_track"):
        op.add_column(
            "user_settings",
            sa.Column(name, jsonb, server_default=sa.text("'[]'::jsonb"), nullable=False),
        )
    rows = bind.execute(sa.text("SELECT user_id, sources, block_senders FROM user_settings")).all()
    for user_id, sources, block in rows:
        college = next((s["senders"] for s in sources or [] if s.get("name") == "College"), [])
        bind.execute(
            sa.text(
                "UPDATE user_settings SET denylist = CAST(:d AS jsonb), "
                "allowlist = CAST(:a AS jsonb) WHERE user_id = :u"
            ),
            {"d": json.dumps(block or []), "a": json.dumps(college), "u": user_id},
        )
    for name in ("require_deadline", "block_senders", "watch_keywords", "sources"):
        op.drop_column("user_settings", name)
