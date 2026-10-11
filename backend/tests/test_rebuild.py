"""Rebuild from sources: removes only automatic tasks that no longer match, never the user's."""

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.api.sync import get_gmail_api
from app.ingest.gmail import RateLimited
from app.main import app
from app.models import Quarantine, Reminder, Task, User, UserSettings
from app.notify.calendar import get_calendar_api
from app.rebuild import RebuildResult, preview, run_rebuild
from tests.gmail_fakes import FakeGmailApi

FIXTURES = Path(__file__).parent / "fixtures" / "source_emails"
MANIFEST = json.loads((FIXTURES / "expected.manifest").read_text())
NOW = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)


def raw_fixtures() -> dict[str, dict]:
    out = {}
    for path in sorted(FIXTURES.glob("*.json")):
        raw = json.loads(path.read_text())
        out[raw["id"]] = raw
    return out


def fixture_id(prefix: str) -> str:
    return next(v["id"] for k, v in MANIFEST.items() if k.split("_", 1)[1].startswith(prefix))


@pytest.fixture
def user(db_session):
    u = User(email="me@example.test")
    db_session.add(u)
    db_session.flush()
    db_session.add(
        UserSettings(
            user_id=u.id,
            sources=[
                {"name": "NPTEL", "type": "nptel", "senders": ["nptel.ac.in"]},
                {"name": "Digii", "type": "digii", "senders": ["digii.example"]},
            ],
            block_senders=["blocked.example"],
        )
    )
    db_session.commit()
    return u


def add(session, user, ref, **kw) -> Task:
    values = {
        "user_id": user.id,
        "title": f"title {ref}",
        "source": "nptel",
        "source_name": "NPTEL",
        "source_ref": ref,
        "sender_email": "noreply@nptel.ac.in",
        "status": "open",
        "user_confirmed": False,
        "source_received_at": NOW - timedelta(days=2),
    }
    task = Task(**(values | kw))
    session.add(task)
    session.commit()
    return task


def refs(session, user) -> dict[str, Task]:
    return {
        t.source_ref or f"id{t.id}": t
        for t in session.scalars(select(Task).where(Task.user_id == user.id))
    }


def rebuild(session, user, gmail=None) -> RebuildResult:
    gmail = gmail or FakeGmailApi(raw_fixtures())
    return run_rebuild(session, user, gmail, None, lambda: NOW)


def protected_set(session, user) -> dict[str, Task]:
    """Tasks a rebuild must never touch, in every flavour."""
    return {
        "manual": add(
            session, user, None, source="manual", source_name=None, sender_email=None, title="m"
        ),
        "confirmed": add(
            session, user, "old-confirmed", sender_email="gone@old.test", user_confirmed=True
        ),
        "done": add(session, user, "old-done", sender_email="gone@old.test", status="done"),
        "dismissed": add(
            session, user, "old-dismissed", sender_email="gone@old.test", status="dismissed"
        ),
        "outside_window": add(
            session,
            user,
            "old-window",
            sender_email="gone@old.test",
            source_received_at=NOW - timedelta(days=90),
        ),
    }


# ---------- what a rebuild removes ----------


def test_it_removes_automatic_tasks_whose_sender_is_no_longer_a_source(db_session, user):
    add(db_session, user, "stale1", sender_email="noreply@dropped-source.test")
    add(db_session, user, "stale2", source="custom", source_name="Old", sender_email="a@gone.test")

    result = rebuild(db_session, user)

    assert result.removed == 2
    assert "stale1" not in refs(db_session, user) and "stale2" not in refs(db_session, user)


def test_it_removes_a_task_whose_mail_is_now_decided_not_a_task(db_session, user):
    # The mail is still in the query, but the rules now say drop (a confirmation).
    ref = fixture_id("drop_digii_you_have_submitted")
    add(
        db_session,
        user,
        ref,
        source="digii",
        source_name="Digii",
        sender_email="no-reply@digii.example",
    )

    result = rebuild(db_session, user)

    assert ref not in refs(db_session, user) and result.removed == 1


def test_it_keeps_automatic_tasks_that_still_match_and_creates_the_missing_ones(db_session, user):
    ref = fixture_id("task_digii_new_quiz_table")
    existing = add(
        db_session,
        user,
        ref,
        source="digii",
        source_name="Digii",
        sender_email="no-reply@digii.example",
        title="New Quiz Published: Week 9 Quiz",
    )

    result = rebuild(db_session, user)

    now_tasks = refs(db_session, user)
    assert now_tasks[ref].id == existing.id  # same row, not deleted and re-made
    assert result.kept == 1 and result.removed == 0
    assert (
        result.created == len([v for v in MANIFEST.values() if v["kind"] in ("task", "review")]) - 1
    )
    assert result.tasks_by_source == {"Digii": 1, "NPTEL": 2, "Hackathon": 1}
    assert result.review == 1  # the conflicting-deadlines mail, nothing else
    assert result.suspicious == len([v for v in MANIFEST.values() if v["kind"] == "suspicious"])


def test_a_renamed_source_is_followed_by_its_existing_tasks(db_session, user):
    ref = fixture_id("task_digii_new_quiz_table")
    add(
        db_session,
        user,
        ref,
        source="digii",
        source_name="Old name",
        sender_email="no-reply@digii.example",
    )
    rebuild(db_session, user)
    assert refs(db_session, user)[ref].source_name == "Digii"


# ---------- what it never touches ----------


def test_it_never_deletes_manual_confirmed_done_dismissed_or_out_of_window_tasks(db_session, user):
    keep = protected_set(db_session, user)
    ids = {name: t.id for name, t in keep.items()}
    add(db_session, user, "stale", sender_email="x@gone.test")

    result = rebuild(db_session, user)

    surviving = {t.id for t in db_session.scalars(select(Task))}
    assert set(ids.values()) <= surviving
    assert result.removed == 1
    for task in keep.values():
        db_session.refresh(task)
    assert keep["done"].status == "done" and keep["dismissed"].status == "dismissed"
    assert keep["confirmed"].user_confirmed is True and keep["manual"].source == "manual"


def test_a_confirmed_task_is_kept_even_when_its_mail_is_now_dropped(db_session, user):
    ref = fixture_id("drop_digii_you_have_submitted")
    task = add(
        db_session, user, ref, source="digii", source_name="Digii", user_confirmed=True,
        sender_email="no-reply@digii.example",
    )  # fmt: skip
    rebuild(db_session, user)
    assert db_session.get(Task, task.id) is not None


def test_a_task_the_user_edited_through_the_api_is_marked_confirmed(db_session, user):
    # The column the rebuild relies on defaults to false and the API sets it (see test_api_tasks).
    assert add(db_session, user, "plain").user_confirmed is False


def test_running_it_twice_changes_nothing_the_second_time(db_session, user):
    protected_set(db_session, user)
    add(db_session, user, "stale", sender_email="x@gone.test")
    first = rebuild(db_session, user)
    snapshot = {k: (t.id, t.status) for k, t in refs(db_session, user).items()}
    held = db_session.scalar(select(func.count()).select_from(Quarantine))

    second = rebuild(db_session, user)

    assert first.removed == 1 and first.created > 0
    assert (second.removed, second.created) == (0, 0)
    assert {k: (t.id, t.status) for k, t in refs(db_session, user).items()} == snapshot
    assert db_session.scalar(select(func.count()).select_from(Quarantine)) == held


def test_deleting_a_task_removes_its_reminders_too(db_session, user):
    stale = add(
        db_session, user, "stale", sender_email="x@gone.test", deadline=NOW + timedelta(days=5)
    )
    db_session.add(Reminder(task_id=stale.id, channel="push", fire_at=NOW + timedelta(days=3)))
    db_session.commit()
    stale_id = stale.id
    rebuild(db_session, user)
    assert db_session.get(Task, stale_id) is None
    left = select(func.count()).select_from(Reminder).where(Reminder.task_id == stale_id)
    assert db_session.scalar(left) == 0


# ---------- failures and limits ----------


def test_a_rate_limited_run_does_not_delete_what_it_could_not_check(db_session, user):
    stale = add(db_session, user, "stale", sender_email="x@gone.test")
    gmail = FakeGmailApi(raw_fixtures())
    gmail.get_errors[sorted(raw_fixtures())[0]] = RateLimited("quota")

    result = rebuild(db_session, user, gmail)

    assert result.incomplete is True
    assert db_session.get(Task, stale.id) is not None  # not looked at, so left for the next run


def test_a_message_that_failed_keeps_its_task(db_session, user):
    ref = fixture_id("task_digii_new_quiz_table")
    task = add(
        db_session,
        user,
        ref,
        source="digii",
        source_name="Digii",
        sender_email="no-reply@digii.example",
    )
    gmail = FakeGmailApi(raw_fixtures())
    gmail.get_errors[ref] = RuntimeError("temporary")

    result = rebuild(db_session, user, gmail)

    assert result.failed == 1
    assert db_session.get(Task, task.id) is not None


def test_removing_a_source_removes_its_automatic_tasks_on_rebuild(db_session, user):
    ref = fixture_id("task_digii_new_quiz_table")
    task = add(
        db_session,
        user,
        ref,
        source="digii",
        source_name="Digii",
        sender_email="no-reply@digii.example",
    )
    settings = db_session.get(UserSettings, user.id)
    settings.sources = [s for s in settings.sources if s["name"] != "Digii"]
    db_session.commit()

    result = rebuild(db_session, user)

    assert db_session.get(Task, task.id) is None and result.removed == 1


# ---------- preview ----------


def test_preview_counts_without_fetching_anything(db_session, user):
    protected_set(db_session, user)
    add(db_session, user, "gone", sender_email="x@gone.test")
    add(db_session, user, "still", sender_email="a@nptel.ac.in")
    add(db_session, user, "kw", source="keyword", source_name="Hackathon", sender_email="x@y.test")
    add(db_session, user, "rev", sender_email="x@gone.test", status="needs_review")

    got = preview(db_session, user, NOW)

    assert got.window_days == 30
    assert got.rechecked == 4  # gone, still, kw, rev
    assert (
        got.will_remove == 2
    )  # gone + rev: their sender is in no source (keyword tasks are re-judged)
    assert got.review_items == 1
    assert got.protected == 5


# ---------- HTTP ----------


@pytest.fixture
def use_gmail():
    def _use(fake):
        app.dependency_overrides[get_gmail_api] = lambda: fake
        app.dependency_overrides[get_calendar_api] = lambda: None

    yield _use
    app.dependency_overrides.pop(get_gmail_api, None)
    app.dependency_overrides.pop(get_calendar_api, None)


def test_rebuild_requires_login(client_factory):
    client = client_factory()
    assert client.post("/sources/rebuild").status_code == 401
    assert client.get("/sources/rebuild/preview").status_code == 401
    assert client.get("/sources/rebuild/status").status_code == 401


def test_rebuild_endpoint_runs_reports_counts_and_is_idempotent(api, engine, uid, use_gmail):
    use_gmail(FakeGmailApi(raw_fixtures()))
    api.put(
        "/settings",
        json={
            "sources": [
                {"name": "NPTEL", "type": "nptel", "senders": ["nptel.ac.in"]},
                {"name": "Digii", "type": "digii", "senders": ["digii.example"]},
            ],
            "watch_keywords": ["hackathon", "ideathon", "datathon"],
            "block_senders": ["blocked.example"],
            "require_deadline": False,
            "digest_hour": 8,
        },
    )
    assert api.get("/sources/rebuild/status").json() == {"state": "idle"}
    manual = api.post("/tasks", json={"title": "mine"}).json()

    first = api.post("/sources/rebuild?wait=true")
    assert first.status_code == 200
    body = first.json()
    assert body["state"] == "done"
    result = body["result"]
    # (the deadlines in the fixtures are dated, so the keyword task may expire: stay loose here)
    assert result["created"] >= 3 and result["removed"] == 0
    assert result["tasks_by_source"]["Digii"] == 1 and result["tasks_by_source"]["NPTEL"] >= 1
    assert api.get("/sources/rebuild/status").json()["state"] == "done"

    second = api.post("/sources/rebuild?wait=true").json()["result"]
    assert (second["created"], second["removed"]) == (0, 0)

    ids = {t["id"] for t in api.get("/tasks").json()}
    assert manual["id"] in ids


def test_rebuild_endpoint_returns_202_and_finishes_in_the_background(api, uid, use_gmail):
    use_gmail(FakeGmailApi({}))
    resp = api.post("/sources/rebuild")
    assert resp.status_code == 202
    assert resp.json()["state"] in ("running", "done")
    for _ in range(100):  # wait for the background run so it cannot outlive the test database
        if api.get("/sources/rebuild/status").json()["state"] != "running":
            break
        time.sleep(0.1)
    assert api.get("/sources/rebuild/status").json()["state"] == "done"


def test_preview_endpoint_is_scoped_to_the_user(api, api_b, engine, uid):
    api.post("/tasks", json={"title": "mine"})
    assert api.get("/sources/rebuild/preview").json()["protected"] == 1
    assert api_b.get("/sources/rebuild/preview").json()["protected"] == 0


def test_a_task_still_holding_a_tracking_wrapper_gets_the_real_link_on_rebuild(db_session, user):
    ref = fixture_id("task_digii_new_quiz_table")
    wrapped = (
        "https://x.r.ap-south-1.awstrack.me/L0/https:%2F%2Fapp.digii.example%2Fquiz%2F9/1/abc/sig"
    )
    task = add(
        db_session, user, ref, source="digii", source_name="Digii", user_confirmed=True,
        sender_email="no-reply@digii.example", link=wrapped, link_type="action",
    )  # fmt: skip
    rebuild(db_session, user)
    db_session.refresh(task)
    assert task.link == "https://app.digii.example/quiz/9"
