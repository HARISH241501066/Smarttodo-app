# ruff: noqa: E501  (long literals in fixtures and expected strings)
"""Module 11b: the Updates routes, the per-sender bulk actions and the review re-sort endpoint."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.api.sync import get_gmail_api
from app.ingest.gmail import MessageGone, RateLimited
from app.ingest.sync import LOCK_NAMESPACE
from app.main import app
from app.models import EmailUpdate, ProcessedMessage, Reminder, Task
from tests.gmail_fakes import FakeGmailApi
from tests.test_ingest_gmail import http_error

FIXTURES = Path(__file__).parent / "fixtures" / "triage_emails"
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def use_gmail(fake):
    app.dependency_overrides[get_gmail_api] = lambda: fake


def triage_raws() -> dict[str, dict]:
    manifest = json.loads((FIXTURES / "expected.manifest").read_text())
    return {m["id"]: json.loads((FIXTURES / f"{n}.json").read_text()) for n, m in manifest.items()}


def add_update(session, uid, title="Quiz summary", message_id="m1", minutes=0, **kw):
    values = {
        "source": "nptel",
        "link": "https://onlinecourses.nptel.ac.in/x",
        "link_type": "action",
    }
    row = EmailUpdate(
        user_id=uid, title=title, message_id=message_id,
        received_at=T0 + timedelta(minutes=minutes), **(values | kw),
    )  # fmt: skip
    session.add(row)
    session.commit()
    return row.id


def add_review(session, uid, title, sender, message_id=None, **kw):
    task = Task(
        user_id=uid, title=title, status="needs_review", source="other", sender_email=sender,
        source_ref=message_id or f"r-{title}", **kw,
    )  # fmt: skip
    session.add(task)
    session.commit()
    return task.id


# ====================================================================== auth


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/updates"),
        ("post", "/updates/1/task"),
        ("post", "/updates/1/dismiss"),
        ("post", "/review/reprocess"),
        ("post", "/review/bulk"),
    ],
)
def test_the_new_routes_require_login(client_factory, method, path):
    assert getattr(client_factory(), method)(path).status_code == 401


def test_the_new_routes_are_in_the_api_docs(client_factory):
    spec = client_factory().get("/openapi.json").json()
    listed = {(m.upper(), p) for p, item in spec["paths"].items() for m in item}
    for route in (
        ("GET", "/updates"),
        ("POST", "/updates/{update_id}/task"),
        ("POST", "/updates/{update_id}/dismiss"),
        ("POST", "/review/reprocess"),
        ("POST", "/review/bulk"),
    ):
        assert route in listed


# ====================================================================== updates


def test_updates_list_newest_first_with_the_link_domain(api, db_session, uid):
    add_update(db_session, uid, "Older", "m1", minutes=0)
    add_update(
        db_session,
        uid,
        "Newer",
        "m2",
        minutes=30,
        link="https://www.example.com/p",
        link_type="action",
    )
    add_update(db_session, uid, "No link", "m3", minutes=10, link=None, link_type=None)
    rows = api.get("/updates").json()
    assert [r["title"] for r in rows] == ["Newer", "No link", "Older"]
    assert rows[0]["link_domain"] == "example.com" and rows[1]["link_domain"] is None
    assert set(rows[0]) == {
        "id",
        "title",
        "source",
        "link",
        "link_type",
        "link_domain",
        "received_at",
        "created_at",
    }


def test_updates_are_per_user(api, api_b, db_session, uid):
    update_id = add_update(db_session, uid)
    assert api_b.get("/updates").json() == []
    assert api_b.post(f"/updates/{update_id}/task").status_code == 404
    assert api_b.post(f"/updates/{update_id}/dismiss").status_code == 404
    assert len(api.get("/updates").json()) == 1  # untouched


def test_turn_an_update_into_a_task(api, db_session, uid):
    update_id = add_update(db_session, uid, "Week 9 lecture videos uploaded", "mx")
    resp = api.post(f"/updates/{update_id}/task")
    assert resp.status_code == 200
    task = resp.json()
    assert (task["title"], task["status"], task["source"], task["deadline"]) == (
        "Week 9 lecture videos uploaded", "open", "nptel", None
    )  # fmt: skip
    assert (task["priority"], task["link"], task["link_type"]) == (
        "important",
        "https://onlinecourses.nptel.ac.in/x",
        "action",
    )
    assert api.get("/updates").json() == []  # it left the Updates tab
    assert [t["id"] for t in api.get("/tasks?status=open").json()] == [task["id"]]
    assert (
        db_session.scalar(select(func.count()).select_from(Reminder)) == 0
    )  # no deadline, nothing to remind
    assert api.post(f"/updates/{update_id}/task").status_code == 404  # already converted


def test_an_update_from_an_unknown_sender_becomes_a_regular_task(api, db_session, uid):
    update_id = add_update(db_session, uid, "FYI", "my", source="other")
    assert api.post(f"/updates/{update_id}/task").json()["priority"] == "regular"


def test_dismissing_an_update_removes_it_but_the_message_stays_handled(api, db_session, uid):
    db_session.add(
        ProcessedMessage(user_id=uid, message_id="m1", decision="passed", reason="update")
    )
    update_id = add_update(db_session, uid, message_id="m1")
    assert api.post(f"/updates/{update_id}/dismiss").status_code == 204
    assert api.get("/updates").json() == []
    assert api.post(f"/updates/{update_id}/dismiss").status_code == 404
    assert (
        db_session.scalar(select(func.count()).select_from(ProcessedMessage)) == 1
    )  # so it never comes back


# ====================================================================== bulk by sender


def test_bulk_confirm_moves_only_that_senders_review_items(api, db_session, uid):
    a1 = add_review(db_session, uid, "A1", "news@example.com")
    a2 = add_review(db_session, uid, "A2", "news@example.com")
    b1 = add_review(db_session, uid, "B1", "other@example.com")
    no_sender = add_review(db_session, uid, "Old item", None)

    resp = api.post("/review/bulk", json={"action": "confirm", "sender": "News@Example.com"})

    assert resp.status_code == 200 and resp.json() == {"updated": 2}
    status = lambda i: api.get(f"/tasks/{i}").json()["status"]  # noqa: E731
    assert (status(a1), status(a2), status(b1), status(no_sender)) == (
        "open",
        "open",
        "needs_review",
        "needs_review",
    )
    assert (
        db_session.scalar(
            select(func.count()).select_from(Reminder).where(Reminder.kind == "created")
        )
        == 0
    )
    assert api.post(
        "/review/bulk", json={"action": "confirm", "sender": "news@example.com"}
    ).json() == {"updated": 0}


def test_bulk_dismiss(api, db_session, uid):
    ids = [add_review(db_session, uid, f"N{i}", "spam@example.com") for i in range(3)]
    keep = add_review(db_session, uid, "Keep", "good@example.com")
    assert api.post(
        "/review/bulk", json={"action": "dismiss", "sender": "spam@example.com"}
    ).json() == {"updated": 3}
    assert {api.get(f"/tasks/{i}").json()["status"] for i in ids} == {"dismissed"}
    assert api.get(f"/tasks/{keep}").json()["status"] == "needs_review"
    assert [t["id"] for t in api.get("/review").json()] == [keep]


def test_bulk_does_not_touch_open_tasks_or_other_users(api, api_b, db_session, uid, uid_b):
    open_task = Task(
        user_id=uid,
        title="Already open",
        status="open",
        source="other",
        sender_email="news@example.com",
    )
    db_session.add(open_task)
    theirs = add_review(db_session, uid_b, "Theirs", "news@example.com")
    db_session.commit()
    assert api.post(
        "/review/bulk", json={"action": "dismiss", "sender": "news@example.com"}
    ).json() == {"updated": 0}
    assert api.get(f"/tasks/{open_task.id}").json()["status"] == "open"
    assert api_b.get(f"/tasks/{theirs}").json()["status"] == "needs_review"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"action": "confirm"},
        {"sender": "a@b.com"},
        {"action": "delete", "sender": "a@b.com"},
        {"action": "confirm", "sender": ""},
        {"action": "confirm", "sender": "not-an-address"},
        {"action": "confirm", "sender": "a b@c.com"},
    ],
)
def test_bulk_validation(api, body):
    assert api.post("/review/bulk", json=body).status_code == 422


def test_tasks_expose_the_sender_address(api, db_session, uid):
    task_id = add_review(db_session, uid, "X", "news@example.com")
    assert api.get(f"/tasks/{task_id}").json()["sender_email"] == "news@example.com"
    assert api.get("/review").json()[0]["sender_email"] == "news@example.com"


# ====================================================================== re-sort the review queue


def seed_queue(session, uid):
    # tri18: strong "do this" and "FYI" cues in one email (no dates, so it never depends on the clock)
    for mid in ("tri01", "tri02", "tri04", "tri18", "tri08"):
        add_review(session, uid, f"item {mid}", None, message_id=mid)
    add_review(session, uid, "item gone", None, message_id="not-in-gmail")


def test_reprocess_sorts_the_queue_and_reports_counts(api, db_session, uid):
    seed_queue(db_session, uid)
    use_gmail(FakeGmailApi(triage_raws()))

    resp = api.post("/review/reprocess")

    assert resp.status_code == 200
    assert resp.json() == {
        "total": 6,
        "task": 2,
        "update": 1,
        "ignore": 1,
        "review": 1,
        "gone": 1,
        "next_after_id": None,
    }
    assert {t["title"] for t in api.get("/review").json()} == {"item tri18", "item gone"}
    assert [u["title"] for u in api.get("/updates").json()] == ["item tri02"]
    assert {
        t.source_ref for t in db_session.scalars(select(Task).where(Task.status == "open"))
    } == {"tri01", "tri08"}


def test_reprocess_twice_changes_nothing_more(api, db_session, uid):
    seed_queue(db_session, uid)
    use_gmail(FakeGmailApi(triage_raws()))
    api.post("/review/reprocess")
    before = (api.get("/tasks?limit=500").json(), api.get("/updates").json())
    again = api.post("/review/reprocess").json()
    assert (again["task"], again["update"], again["ignore"]) == (0, 0, 0)
    assert (api.get("/tasks?limit=500").json(), api.get("/updates").json()) == before


def test_reprocess_in_batches_follows_the_cursor(api, db_session, uid):
    seed_queue(db_session, uid)
    use_gmail(FakeGmailApi(triage_raws()))
    after, seen = 0, {"task": 0, "update": 0, "ignore": 0, "review": 0, "gone": 0}
    for _ in range(5):
        body = api.post(f"/review/reprocess?limit=2&after_id={after}").json()
        for key in seen:
            seen[key] += body[key]
        if body["next_after_id"] is None:
            break
        after = body["next_after_id"]
    assert seen == {"task": 2, "update": 1, "ignore": 1, "review": 1, "gone": 1}


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=x", "after_id=-1"])
def test_reprocess_validation(api, query):
    use_gmail(FakeGmailApi({}))
    assert api.post(f"/review/reprocess?{query}").status_code == 422


def test_reprocess_only_touches_the_callers_queue(api, api_b, db_session, uid, uid_b):
    mine = add_review(db_session, uid, "mine", None, message_id="tri02")
    theirs = add_review(db_session, uid_b, "theirs", None, message_id="tri02")
    use_gmail(FakeGmailApi(triage_raws()))
    assert api.post("/review/reprocess").json()["update"] == 1
    assert api_b.get(f"/tasks/{theirs}").json()["status"] == "needs_review"
    assert api.get(f"/tasks/{mine}").status_code == 404  # moved to Updates


@pytest.mark.parametrize(
    "error,status",
    [(RateLimited("slow"), 429), (http_error(500), 502)],
)
def test_reprocess_gmail_failures(api, db_session, uid, error, status):
    add_review(db_session, uid, "x", None, message_id="tri01")
    fake = FakeGmailApi(triage_raws())
    fake.get_errors["tri01"] = error
    use_gmail(fake)
    assert api.post("/review/reprocess").status_code == status
    assert [t["title"] for t in api.get("/review").json()] == ["x"]  # nothing lost


def test_reprocess_leaves_an_email_deleted_from_gmail_where_it_is(api, db_session, uid):
    add_review(db_session, uid, "vanished", None, message_id="tri01")
    fake = FakeGmailApi({})
    fake.get_errors["tri01"] = MessageGone("tri01")
    use_gmail(fake)
    assert api.post("/review/reprocess").json()["gone"] == 1
    assert [t["title"] for t in api.get("/review").json()] == ["vanished"]


def test_reprocess_refuses_to_run_during_a_sync(api, engine, db_session, uid):
    add_review(db_session, uid, "x", None, message_id="tri02")
    use_gmail(FakeGmailApi(triage_raws()))
    key = LOCK_NAMESPACE * 2**32 + uid % 2**32
    with engine.connect() as other:
        other.execute(text("SELECT pg_advisory_lock(:k)"), {"k": key})
        assert api.post("/review/reprocess").status_code == 409
        other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
    assert api.post("/review/reprocess").status_code == 200


# ====================================================================== the sync endpoint


def test_the_sync_endpoint_reports_ignored_mail(api, db_session, uid):
    use_gmail(FakeGmailApi({"tri04": triage_raws()["tri04"], "tri01": triage_raws()["tri01"]}))
    body = api.post("/sync/run").json()
    assert body["ignored"] == 1 and body["passed"] == 1  # the OTP is dropped, the assignment kept
    assert (
        db_session.scalar(
            select(ProcessedMessage.decision).where(ProcessedMessage.message_id == "tri04")
        )
        == "ignored"
    )
