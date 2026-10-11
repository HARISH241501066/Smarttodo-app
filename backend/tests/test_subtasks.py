"""Subtasks, tags, /deadlines, origin filter and per-task reminder switch."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text


def iso(**delta) -> str:
    return (datetime.now(UTC) + timedelta(**delta)).isoformat()


def make(api, title="T", **kw) -> dict:
    resp = api.post("/tasks", json={"title": title, **kw})
    assert resp.status_code == 201, resp.text
    return resp.json()


def add_email_task(engine, uid, title="Email task", deadline=None, source="nptel", status="open"):
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO tasks (user_id, title, source, source_ref, status, deadline, "
                "priority) "
                "VALUES (:u, :t, :s, :r, :st, :d, 'regular') RETURNING id"
            ),
            {"u": uid, "t": title, "s": source, "r": f"ref-{title}", "st": status, "d": deadline},
        ).scalar_one()


def count(engine, sql, **params) -> int:
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar_one()


# ---------- CRUD ----------


def test_create_a_task_with_subtasks_tag_and_notes(api):
    task = make(
        api, "Report", tag="  Uni  work ", notes="n", subtasks=["Draft", " Review  it ", "Send"]
    )
    assert task["tag"] == "Uni work" and task["origin"] == "manual"
    assert [s["title"] for s in task["subtasks"]] == ["Draft", "Review it", "Send"]
    assert [s["position"] for s in task["subtasks"]] == [0, 1, 2]
    assert (task["subtasks_done"], task["subtasks_total"]) == (0, 3)
    assert task["reminders_enabled"] is True


def test_subtask_crud_and_reorder(api):
    task = make(api, "Report")
    tid = task["id"]

    a = api.post(f"/tasks/{tid}/subtasks", json={"title": "  A "})
    assert a.status_code == 201
    a_id = a.json()["subtask"]["id"]
    b_id = api.post(f"/tasks/{tid}/subtasks", json={"title": "B"}).json()["subtask"]["id"]
    c_id = api.post(f"/tasks/{tid}/subtasks", json={"title": "C"}).json()["subtask"]["id"]
    assert [s["title"] for s in api.get(f"/tasks/{tid}").json()["subtasks"]] == ["A", "B", "C"]

    renamed = api.patch(f"/tasks/{tid}/subtasks/{b_id}", json={"title": "B2"}).json()
    assert renamed["subtask"]["title"] == "B2" and renamed["all_subtasks_done"] is False

    moved = api.patch(f"/tasks/{tid}/subtasks/reorder", json={"ids": [c_id, a_id, b_id]})
    assert moved.status_code == 200
    assert [s["title"] for s in moved.json()["task"]["subtasks"]] == ["C", "A", "B2"]
    assert [s["title"] for s in api.get(f"/tasks/{tid}").json()["subtasks"]] == ["C", "A", "B2"]

    deleted = api.delete(f"/tasks/{tid}/subtasks/{a_id}")
    assert deleted.status_code == 200
    assert [s["title"] for s in deleted.json()["task"]["subtasks"]] == ["C", "B2"]
    # a new subtask goes to the end
    d = api.post(f"/tasks/{tid}/subtasks", json={"title": "D"}).json()["task"]["subtasks"]
    assert [s["title"] for s in d] == ["C", "B2", "D"]


def test_deleting_a_task_deletes_its_subtasks(api, engine):
    task = make(api, "Gone", subtasks=["a", "b"])
    assert count(engine, "SELECT count(*) FROM subtasks") == 2
    assert api.delete(f"/tasks/{task['id']}").status_code == 204
    assert count(engine, "SELECT count(*) FROM subtasks") == 0


def test_subtask_validation(api):
    tid = make(api, "T", subtasks=["a", "b"])["id"]
    ids = [s["id"] for s in api.get(f"/tasks/{tid}").json()["subtasks"]]
    assert api.post(f"/tasks/{tid}/subtasks", json={"title": "   "}).status_code == 422
    assert api.post(f"/tasks/{tid}/subtasks", json={"title": "x" * 301}).status_code == 422
    assert api.post("/tasks", json={"title": "T", "subtasks": [" "]}).status_code == 422
    assert api.patch(f"/tasks/{tid}/subtasks/{ids[0]}", json={}).status_code == 422
    assert api.patch(f"/tasks/{tid}/subtasks/{ids[0]}", json={"title": None}).status_code == 422
    assert api.patch(f"/tasks/{tid}/subtasks/{ids[0]}", json={"done": None}).status_code == 422
    for bad in ([ids[0]], [ids[0], ids[0]], [ids[0], ids[1], 999999], []):
        assert api.patch(f"/tasks/{tid}/subtasks/reorder", json={"ids": bad}).status_code == 422
    assert api.patch(f"/tasks/{tid}/subtasks/999999", json={"done": True}).status_code == 404
    assert api.delete(f"/tasks/{tid}/subtasks/999999").status_code == 404
    assert api.post("/tasks/999999/subtasks", json={"title": "x"}).status_code == 404


def test_subtasks_work_on_email_tasks_too(api, engine, uid):
    tid = add_email_task(engine, uid, "Week 9 quiz")
    added = api.post(f"/tasks/{tid}/subtasks", json={"title": "Revise week 9"})
    assert added.status_code == 201
    task = api.get(f"/tasks/{tid}").json()
    assert task["origin"] == "email" and task["subtasks_total"] == 1
    sub = task["subtasks"][0]["id"]
    assert (
        api.patch(f"/tasks/{tid}/subtasks/{sub}", json={"done": True}).json()["task"][
            "subtasks_done"
        ]
        == 1
    )


def test_subtasks_are_private_to_their_owner(api, api_b, engine, uid):
    tid = make(api, "Mine", subtasks=["a"])["id"]
    sub = api.get(f"/tasks/{tid}").json()["subtasks"][0]["id"]
    assert api_b.post(f"/tasks/{tid}/subtasks", json={"title": "x"}).status_code == 404
    assert api_b.patch(f"/tasks/{tid}/subtasks/{sub}", json={"done": True}).status_code == 404
    assert api_b.delete(f"/tasks/{tid}/subtasks/{sub}").status_code == 404
    assert api_b.patch(f"/tasks/{tid}/subtasks/reorder", json={"ids": [sub]}).status_code == 404


# ---------- the "last subtask" signal ----------


def test_ticking_the_last_subtask_reports_all_done_but_never_completes_the_task(api):
    task = make(api, "Three steps", subtasks=["1", "2", "3"], deadline=iso(days=3))
    tid = task["id"]
    ids = [s["id"] for s in task["subtasks"]]

    flags = []
    for sub in ids:
        resp = api.patch(f"/tasks/{tid}/subtasks/{sub}", json={"done": True}).json()
        flags.append(resp["all_subtasks_done"])
    assert flags == [False, False, True]

    after = api.get(f"/tasks/{tid}").json()
    assert after["status"] == "open" and after["subtasks_done"] == 3
    # unticking one clears the flag again
    assert (
        api.patch(f"/tasks/{tid}/subtasks/{ids[0]}", json={"done": False}).json()[
            "all_subtasks_done"
        ]
        is False
    )
    # a task with no subtasks never reports it
    empty = make(api, "None")
    assert (
        api.post(f"/tasks/{empty['id']}/subtasks", json={"title": "x"}).json()["all_subtasks_done"]
        is False
    )


# ---------- /deadlines and the origin filter ----------


def test_deadlines_are_ordered_soonest_first_with_overdue_in_and_no_deadline_last(api, engine, uid):
    make(api, "manual later", deadline=iso(days=10))
    make(api, "manual no deadline")
    make(api, "manual soon", deadline=iso(hours=5))
    make(api, "manual overdue", deadline=iso(days=-2))
    add_email_task(engine, uid, "email tomorrow", deadline=datetime.now(UTC) + timedelta(days=1))
    add_email_task(engine, uid, "email no deadline", deadline=None)
    add_email_task(
        engine, uid, "email done", deadline=datetime.now(UTC) + timedelta(days=2), status="done"
    )
    add_email_task(
        engine,
        uid,
        "email review",
        deadline=datetime.now(UTC) + timedelta(days=3),
        status="needs_review",
    )

    titles = [t["title"] for t in api.get("/deadlines").json()]

    assert titles[:4] == ["manual overdue", "manual soon", "email tomorrow", "manual later"]
    assert set(titles[4:]) == {"manual no deadline", "email no deadline"}  # null deadlines last
    assert "email done" not in titles and "email review" not in titles  # open tasks only

    with_done = [t["title"] for t in api.get("/deadlines?include_done=true").json()]
    assert "email done" in with_done
    assert with_done.index("email done") < with_done.index(
        "manual later"
    )  # still in deadline order


def test_deadlines_only_show_the_users_own_tasks(api, api_b):
    make(api, "mine", deadline=iso(days=1))
    assert api_b.get("/deadlines").json() == []


def test_origin_filter_splits_manual_from_email(api, engine, uid):
    make(api, "manual one")
    add_email_task(engine, uid, "email one")
    assert [t["title"] for t in api.get("/tasks?origin=manual").json()] == ["manual one"]
    assert [t["title"] for t in api.get("/tasks?origin=email").json()] == ["email one"]
    assert len(api.get("/tasks").json()) == 2
    assert api.get("/tasks?origin=bogus").status_code == 422


# ---------- reminders_enabled ----------


def reminders(engine, task_id) -> int:
    return count(engine, "SELECT count(*) FROM reminders WHERE task_id = :t", t=task_id)


def test_reminders_off_generates_none_and_turning_it_on_creates_them(api, engine):
    off = make(api, "Quiet", deadline=iso(days=5), reminders_enabled=False)
    assert off["reminders_enabled"] is False and reminders(engine, off["id"]) == 0
    on = make(api, "Loud", deadline=iso(days=5))
    assert reminders(engine, on["id"]) > 0

    # changing the deadline of a quiet task still creates nothing
    api.patch(f"/tasks/{off['id']}", json={"deadline": iso(days=6)})
    assert reminders(engine, off["id"]) == 0

    assert api.patch(f"/tasks/{off['id']}", json={"reminders_enabled": True}).json()[
        "reminders_enabled"
    ]
    assert reminders(engine, off["id"]) > 0


def test_switching_reminders_off_deletes_the_unsent_ones(api, engine):
    task = make(api, "Was loud", deadline=iso(days=5))
    assert reminders(engine, task["id"]) > 0
    assert api.patch(f"/tasks/{task['id']}", json={"reminders_enabled": False}).status_code == 200
    assert reminders(engine, task["id"]) == 0
    assert api.patch(f"/tasks/{task['id']}", json={"reminders_enabled": None}).status_code == 422


def test_reminders_off_applies_to_email_tasks_too(api, engine, uid):
    tid = add_email_task(engine, uid, "email", deadline=datetime.now(UTC) + timedelta(days=5))
    api.patch(f"/tasks/{tid}", json={"priority": "important"})  # no reminder change
    assert api.patch(f"/tasks/{tid}", json={"reminders_enabled": True}).status_code == 200
    assert reminders(engine, tid) > 0
    api.patch(f"/tasks/{tid}", json={"reminders_enabled": False})
    assert reminders(engine, tid) == 0


# ---------- tags and links ----------


def test_tag_suggestions_come_from_tags_in_use(api, api_b):
    make(api, "a", tag="Uni")
    make(api, "b", tag="Uni")
    make(api, "c", tag="Home")
    make(api, "d")
    make(api_b, "e", tag="Secret")
    assert api.get("/tags").json() == ["Uni", "Home"]
    tid = make(api, "f", tag="Work")["id"]
    assert api.patch(f"/tasks/{tid}", json={"tag": None}).json()["tag"] is None
    assert api.patch(f"/tasks/{tid}", json={"tag": "x" * 51}).status_code == 422
    assert make(api, "g", tag="   ")["tag"] is None


@pytest.mark.parametrize(
    "link",
    ["javascript:alert(1)", "data:text/html,x", "ftp://x.test", "//evil.test", "example.com"],
)
def test_a_manual_task_with_a_non_http_link_is_rejected(api, link):
    assert api.post("/tasks", json={"title": "bad", "link": link}).status_code == 422
    assert api.get("/tasks").json() == []


def test_a_manual_task_with_an_https_link_is_accepted(api):
    task = make(api, "ok", link="https://example.com/x")
    assert task["link"] == "https://example.com/x" and task["link_type"] == "action"


# ---------- expired email tasks complete themselves; the full email; tracking links ----------


def test_an_email_task_past_its_deadline_moves_to_completed_but_a_manual_one_stays_overdue(
    api, engine, uid
):
    email = add_email_task(
        engine, uid, "email late", deadline=datetime.now(UTC) - timedelta(hours=3)
    )
    pending = add_email_task(
        engine, uid, "email ok", deadline=datetime.now(UTC) + timedelta(days=2)
    )
    manual = make(api, "manual late", deadline=iso(hours=-3))
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO reminders (task_id, fire_at, channel) "
                "VALUES (:t, now() + interval '1 day', 'push')"
            ),
            {"t": email},
        )

    open_titles = [t["title"] for t in api.get("/tasks?status=open").json()]
    assert (
        "email late" not in open_titles
        and "email ok" in open_titles
        and "manual late" in open_titles
    )
    assert api.get(f"/tasks/{email}").json()["status"] == "done"
    assert api.get(f"/tasks/{pending}").json()["status"] == "open"
    assert api.get(f"/tasks/{manual['id']}").json()["status"] == "open"
    assert reminders(engine, email) == 0
    assert "email late" not in [t["title"] for t in api.get("/deadlines").json()]
    assert "email late" in [t["title"] for t in api.get("/deadlines?include_done=true").json()]


def test_view_the_full_email_behind_an_email_task(api, engine, uid):
    from app.api.sync import get_gmail_api
    from app.main import app
    from tests.gmail_fakes import FakeGmailApi, fixtures_by_number

    fake = FakeGmailApi(fixtures_by_number(1))
    app.dependency_overrides[get_gmail_api] = lambda: fake
    try:
        tid = add_email_task(engine, uid, "From mail")
        with engine.begin() as conn:
            conn.execute(text("UPDATE tasks SET source_ref = 'msg001' WHERE id = :i"), {"i": tid})
        body = api.get(f"/tasks/{tid}/email")
        assert body.status_code == 200
        data = body.json()
        assert data["subject"] and data["sender"] and data["body_text"]
        assert data["gmail_url"].endswith("/msg001") and data["truncated"] is False
        with engine.begin() as conn:
            conn.execute(text("UPDATE tasks SET source_ref = 'gone-msg' WHERE id = :i"), {"i": tid})
        assert api.get(f"/tasks/{tid}/email").status_code == 410
        assert api.get("/tasks/999999/email").status_code == 404
        manual = make(api, "mine")
        assert api.get(f"/tasks/{manual['id']}/email").status_code == 404
    finally:
        app.dependency_overrides.pop(get_gmail_api, None)


def test_the_email_view_is_private_to_the_owner(api, api_b, engine, uid):
    from app.api.sync import get_gmail_api
    from app.main import app
    from tests.gmail_fakes import FakeGmailApi

    app.dependency_overrides[get_gmail_api] = lambda: FakeGmailApi({})
    try:
        tid = add_email_task(engine, uid, "Private")
        assert api_b.get(f"/tasks/{tid}/email").status_code == 404
    finally:
        app.dependency_overrides.pop(get_gmail_api, None)


def test_amazon_ses_tracking_links_are_unwrapped():
    from app.filters.urls import unwrap_tracking_url

    wrapped = "https://lc21skd8.r.ap-south-1.awstrack.me/L0/https:%2F%2Fapp.digii.example%2Fquiz%2F9%3Fx%3D1/1/0100abcd-000000/sig=123"
    assert unwrap_tracking_url(wrapped) == "https://app.digii.example/quiz/9?x=1"
    plain = "https://app.digii.example/quiz/9"
    assert unwrap_tracking_url(plain) == plain
    odd = "https://x.awstrack.me/L0/not-a-url/1/2"
    assert unwrap_tracking_url(odd) == odd
