from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from tests.helpers import alembic_cfg as _alembic_cfg

FIRE_AT = datetime(2030, 1, 1, 9, 0, tzinfo=UTC)


def _tables(eng) -> set[str]:
    with eng.connect() as conn:
        rows = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
        return {r[0] for r in rows}


EXPECTED_TABLES = {
    "users",
    "tasks",
    "reminders",
    "quarantine",
    "sync_state",
    "user_settings",
    "ingest_failures",
}


def test_upgrade_head_on_empty_db(empty_db):
    command.upgrade(_alembic_cfg(empty_db), "head")
    eng = create_engine(empty_db)
    try:
        assert EXPECTED_TABLES <= _tables(eng)
    finally:
        eng.dispose()


def test_downgrade_base_then_upgrade_head(empty_db):
    cfg = _alembic_cfg(empty_db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    eng = create_engine(empty_db)
    try:
        assert not (EXPECTED_TABLES & _tables(eng))
        command.upgrade(cfg, "head")
        assert EXPECTED_TABLES <= _tables(eng)
    finally:
        eng.dispose()


def _user(conn, email="a@example.test") -> int:
    return conn.execute(
        text("INSERT INTO users (email) VALUES (:e) RETURNING id"), {"e": email}
    ).scalar_one()


def _task(conn, user_id, source_ref=None, **cols) -> int:
    cols = {"title": "t", **cols}
    names = ["user_id", "source_ref", *cols]
    sql = (
        f"INSERT INTO tasks ({', '.join(names)}) "
        f"VALUES ({', '.join(':' + n for n in names)}) RETURNING id"
    )
    params = {"user_id": user_id, "source_ref": source_ref, **cols}
    return conn.execute(text(sql), params).scalar_one()


def test_duplicate_user_source_ref_rejected(engine):
    with engine.connect() as conn:
        uid = _user(conn)
        _task(conn, uid, "msg-1")
        with pytest.raises(IntegrityError, match="uq_tasks_user_id_source_ref"):
            _task(conn, uid, "msg-1")


def test_same_source_ref_allowed_for_different_users_and_null_refs(engine):
    with engine.begin() as conn:
        u1, u2 = _user(conn, "a@example.test"), _user(conn, "b@example.test")
        _task(conn, u1, "msg-1")
        _task(conn, u2, "msg-1")
        _task(conn, u1, None)
        _task(conn, u1, None)


def test_duplicate_email_rejected(engine):
    with engine.connect() as conn:
        _user(conn)
        with pytest.raises(IntegrityError, match="uq_users_email"):
            _user(conn)


def _reminder(conn, task_id, channel="telegram"):
    conn.execute(
        text("INSERT INTO reminders (task_id, fire_at, channel) VALUES (:t, :f, :c)"),
        {"t": task_id, "f": FIRE_AT, "c": channel},
    )


def test_duplicate_reminder_rejected(engine):
    with engine.connect() as conn:
        tid = _task(conn, _user(conn), "m")
        _reminder(conn, tid)
        with pytest.raises(IntegrityError, match="uq_reminders_task_fire_channel"):
            _reminder(conn, tid)


def test_same_reminder_time_allowed_on_other_channel(engine):
    with engine.begin() as conn:
        tid = _task(conn, _user(conn), "m")
        _reminder(conn, tid, "telegram")
        _reminder(conn, tid, "gcal")


def test_deleting_task_cascades_to_reminders(engine):
    with engine.begin() as conn:
        tid = _task(conn, _user(conn), "m")
        other = _task(conn, _user(conn, "b@example.test"), "m")
        _reminder(conn, tid)
        _reminder(conn, tid, "gcal")
        _reminder(conn, other)
        conn.execute(text("DELETE FROM tasks WHERE id = :i"), {"i": tid})
        remaining = conn.execute(text("SELECT task_id FROM reminders")).scalars().all()
        assert remaining == [other]


def test_defaults(engine):
    with engine.begin() as conn:
        uid = _user(conn)
        _task(conn, uid, "m")
        conn.execute(text("INSERT INTO user_settings (user_id) VALUES (:u)"), {"u": uid})
        task = conn.execute(text("SELECT source, priority, status FROM tasks")).one()
        assert tuple(task) == ("other", "regular", "open")
        s = conn.execute(
            text("SELECT llm_enabled, digest_hour, block_senders FROM user_settings")
        ).one()
        assert tuple(s) == (False, 8, [])
        assert conn.execute(text("SELECT needs_reauth FROM users")).scalar_one() is False


@pytest.mark.parametrize(
    ("column", "constraint"),
    [
        ("source", "ck_tasks_source"),
        ("priority", "ck_tasks_priority"),
        ("status", "ck_tasks_status"),
    ],
)
def test_task_enum_check_rejects_invalid_value(engine, column, constraint):
    with engine.connect() as conn:
        uid = _user(conn)
        with pytest.raises(IntegrityError, match=constraint):
            _task(conn, uid, "m", **{column: "bogus"})


def test_task_enum_checks_accept_every_valid_value(engine):
    valid = {
        "source": ["manual", "nptel", "internship", "gform", "college", "other"],
        "priority": ["urgent", "important", "regular"],
        "status": ["open", "done", "needs_review", "dismissed"],
    }
    with engine.begin() as conn:
        uid = _user(conn)
        n = 0
        for column, values in valid.items():
            for v in values:
                n += 1
                _task(conn, uid, f"ref-{n}", **{column: v})


def test_quarantine_status_check_rejects_invalid_value(engine):
    with engine.connect() as conn:
        uid = _user(conn)
        with pytest.raises(IntegrityError, match="ck_quarantine_status"):
            conn.execute(
                text("INSERT INTO quarantine (user_id, message_id, status) VALUES (:u, 'm', :s)"),
                {"u": uid, "s": "bogus"},
            )


def test_quarantine_status_accepts_valid_values(engine):
    with engine.begin() as conn:
        uid = _user(conn)
        for status in ("held", "rescued", "confirmed_scam"):
            conn.execute(
                text("INSERT INTO quarantine (user_id, message_id, status) VALUES (:u, :m, :s)"),
                {"u": uid, "m": f"m-{status}", "s": status},
            )


def test_every_check_constraint_in_db_is_covered(engine):
    """Guards against adding an enum CHECK without a rejection test above."""
    with engine.connect() as conn:
        names = set(
            conn.execute(
                text(
                    "SELECT conname FROM pg_constraint c "
                    "JOIN pg_class t ON t.oid = c.conrelid "
                    "JOIN pg_namespace n ON n.oid = t.relnamespace "
                    "WHERE c.contype = 'c' AND n.nspname = 'public'"
                )
            ).scalars()
        )
    assert names == {
        "ck_tasks_source",
        "ck_tasks_priority",
        "ck_tasks_status",
        "ck_quarantine_status",
        "ck_processed_messages_decision",
        "ck_tasks_link_type",
        "ck_reminders_kind",
        "ck_notifications_kind",
    }
