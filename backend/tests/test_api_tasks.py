"""Module 7: task, review, quarantine and settings routes."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from freezegun import freeze_time
from sqlalchemy import select, text

from app.api.sync import get_gmail_api
from app.ingest.gmail import MessageGone, RateLimited
from app.ingest.query import build_query
from app.main import app
from app.models import Reminder, Task
from app.priority import recompute_priorities
from app.sources import load_sources
from tests.gmail_fakes import FakeGmailApi
from tests.test_ingest_gmail import http_error

FIXTURES = Path(__file__).parent / "fixtures"
UTC_NOW = datetime(2026, 1, 1, 6, 0, tzinfo=UTC)


# ---------- fixtures ----------


def add_task(engine, uid, **kw) -> int:
    values = {"user_id": uid, "title": "Task", "source": "other", "status": "open"} | kw
    cols = ", ".join(values)
    params = ", ".join(f":{k}" for k in values)
    with engine.begin() as conn:
        return conn.execute(
            text(f"INSERT INTO tasks ({cols}) VALUES ({params}) RETURNING id"), values
        ).scalar_one()


def raw_fixture(*parts) -> dict:
    return json.loads(Path(FIXTURES, *parts).read_text())


def use_gmail(fake):
    app.dependency_overrides[get_gmail_api] = lambda: fake


# ---------- auth ----------

ALL_ROUTES = [
    ("get", "/tasks"),
    ("post", "/tasks"),
    ("get", "/tasks/1"),
    ("patch", "/tasks/1"),
    ("delete", "/tasks/1"),
    ("post", "/tasks/1/done"),
    ("post", "/tasks/1/dismiss"),
    ("get", "/review"),
    ("post", "/review/1/confirm"),
    ("get", "/quarantine"),
    ("post", "/quarantine/1/rescue"),
    ("get", "/settings"),
    ("put", "/settings"),
]


@pytest.mark.parametrize("method,path", ALL_ROUTES)
def test_every_route_requires_login(client_factory, method, path):
    assert getattr(client_factory(), method)(path).status_code == 401


# ---------- create ----------


def test_create_minimal_manual_task(api):
    resp = api.post("/tasks", json={"title": "  Buy   a  notebook "})
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "Buy a notebook"
    assert (body["source"], body["status"], body["priority"]) == ("manual", "open", "regular")
    assert body["deadline"] is None and body["deadline_ist"] is None
    assert body["link"] is None and body["link_type"] is None and body["link_domain"] is None
    assert body["confidence"] == 1.0


def test_create_with_link_deadline_and_ist_display(api):
    resp = api.post(
        "/tasks",
        json={
            "title": "Submit report",
            "deadline": "2030-01-22T23:59:00+05:30",
            "link": "https://www.Example.com/submit?x=1",
            "notes": "bring ID",
        },
    )
    body = resp.json()
    assert resp.status_code == 201
    assert body["deadline"].startswith("2030-01-22T18:29:00")
    assert body["deadline_ist"].startswith("2030-01-22T23:59:00")
    assert body["deadline_display"] == "Tue, 22 Jan 2030, 11:59 PM IST"
    assert (body["link_type"], body["link_domain"]) == ("action", "example.com")
    assert body["notes"] == "bring ID"


def test_naive_deadline_is_read_as_ist(api):
    body = api.post("/tasks", json={"title": "t", "deadline": "2030-01-22T10:00:00"}).json()
    assert body["deadline"].startswith("2030-01-22T04:30:00")


def test_explicit_priority_wins_over_the_computed_one(api):
    assert api.post("/tasks", json={"title": "t", "priority": "important"}).json()["priority"] == (
        "important"
    )


def test_deadline_within_48h_makes_a_new_task_urgent(client_factory, uid):
    with freeze_time("2030-01-01 06:00:00"):  # the cookie is signed inside the frozen clock too
        api = client_factory(uid)
        soon = api.post(
            "/tasks", json={"title": "t", "deadline": "2030-01-02T10:00:00+05:30"}
        ).json()
        later = api.post(
            "/tasks", json={"title": "t", "deadline": "2030-01-09T10:00:00+05:30"}
        ).json()
    assert (soon["priority"], later["priority"]) == ("urgent", "regular")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 501},
        {"title": "t", "priority": "critical"},
        {"title": "t", "deadline": "not a date"},
        {"title": "t", "notes": "n" * 5001},
    ],
)
def test_create_validation_errors(api, payload):
    assert api.post("/tasks", json=payload).status_code == 422


@pytest.mark.parametrize(
    "link",
    [
        "javascript:alert(1)",
        "JavaScript:alert(document.cookie)",
        "data:text/html,<script>alert(1)</script>",
        "ftp://example.com/file",
        "file:///etc/passwd",
        "mailto:a@example.test",
        "not a url",
        "example.com/path",
        "//evil.example/path",
        "http://",
        "https:///nohost",
        "https://exa mple.com",
        "https://example.com/\nX-Injected: 1",
        "https://example.com/" + "a" * 2000,
    ],
)
def test_manual_task_with_a_non_http_link_is_rejected(api, link):
    resp = api.post("/tasks", json={"title": "t", "link": link})
    assert resp.status_code == 422
    assert api.get("/tasks").json() == []  # nothing was created


def test_blank_link_means_no_link_and_http_is_accepted(api):
    assert api.post("/tasks", json={"title": "t", "link": "  "}).json()["link"] is None
    assert api.post("/tasks", json={"title": "t", "link": "http://example.com"}).status_code == 201


# ---------- list ----------


def test_list_filters_and_sort_order(api, engine, uid):
    soon, later = "2030-01-01T00:00:00+00:00", "2030-06-01T00:00:00+00:00"
    a = add_task(engine, uid, title="late", deadline=later, priority="regular")
    b = add_task(engine, uid, title="none", priority="regular")
    c = add_task(engine, uid, title="soon", deadline=soon, priority="important", source="nptel")
    d = add_task(engine, uid, title="done", status="done", deadline=soon, priority="regular")

    ids = [t["id"] for t in api.get("/tasks").json()]
    assert ids == [d, c, a, b]  # by deadline (ties: newest first), tasks without one last
    assert [t["id"] for t in api.get("/tasks?status=open").json()] == [c, a, b]
    assert [t["id"] for t in api.get("/tasks?priority=important").json()] == [c]
    assert [t["id"] for t in api.get("/tasks?source=nptel").json()] == [c]
    assert [t["id"] for t in api.get("/tasks?status=open&source=other").json()] == [a, b]
    assert api.get("/tasks?status=done&priority=urgent").json() == []


def test_list_pagination(api, engine, uid):
    for i in range(5):
        add_task(engine, uid, title=f"t{i}", deadline=f"2030-01-0{i + 1}T00:00:00+00:00")
    page = api.get("/tasks?limit=2&offset=2").json()
    assert [t["title"] for t in page] == ["t2", "t3"]


@pytest.mark.parametrize(
    "query", ["status=bogus", "priority=bogus", "source=bogus", "limit=0", "limit=501", "offset=-1"]
)
def test_list_validation_errors(api, query):
    assert api.get(f"/tasks?{query}").status_code == 422


def test_task_fields_for_an_automatic_task(api, engine, uid):
    add_task(
        engine, uid, title="Quiz", source="nptel", deadline="2030-01-22T18:29:00+00:00",
        link="https://onlinecourses.nptel.ac.in/q/1", link_type="action", link_confidence=0.83,
        confidence=0.9, priority="important",
    )  # fmt: skip
    body = api.get("/tasks").json()[0]
    assert body["deadline_display"] == "Tue, 22 Jan 2030, 11:59 PM IST"
    assert (body["link_domain"], body["link_type"], body["link_confidence"]) == (
        "onlinecourses.nptel.ac.in", "action", 0.83,
    )  # fmt: skip
    assert body["confidence"] == 0.9


def test_listing_applies_the_priority_recompute(client_factory, engine, uid):
    add_task(engine, uid, deadline="2030-01-02T12:00:00+00:00", priority="important")
    with freeze_time("2030-01-01 06:00:00"):
        api = client_factory(uid)
        assert api.get("/tasks").json()[0]["priority"] == "urgent"


def test_get_one_task_and_404(api, engine, uid):
    task_id = add_task(engine, uid, title="one")
    assert api.get(f"/tasks/{task_id}").json()["title"] == "one"
    assert api.get("/tasks/999999").status_code == 404
    assert api.get("/tasks/abc").status_code == 422


# ---------- update ----------


def test_patch_updates_only_the_fields_sent(api, engine, uid):
    task_id = add_task(engine, uid, title="old", notes="keep", priority="regular")
    body = api.patch(
        f"/tasks/{task_id}", json={"title": " new title ", "priority": "urgent"}
    ).json()
    assert (body["title"], body["priority"], body["notes"]) == ("new title", "urgent", "keep")


def test_patch_deadline_recomputes_priority_unless_one_is_given(api, engine, uid):
    task_id = add_task(engine, uid, priority="regular")
    soon = (datetime.now(UTC) + timedelta(hours=5)).isoformat()
    far = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    assert api.patch(f"/tasks/{task_id}", json={"deadline": soon}).json()["priority"] == "urgent"
    assert api.patch(f"/tasks/{task_id}", json={"deadline": far}).json()["priority"] == "regular"
    both = api.patch(f"/tasks/{task_id}", json={"deadline": soon, "priority": "important"}).json()
    assert both["priority"] == "important"


def test_patch_null_clears_deadline_notes_and_link(api, engine, uid):
    task_id = add_task(
        engine, uid, deadline="2030-01-01T00:00:00+00:00", notes="n",
        link="https://example.com/x", link_type="action",
    )  # fmt: skip
    body = api.patch(
        f"/tasks/{task_id}", json={"deadline": None, "notes": None, "link": None}
    ).json()
    assert body["deadline"] is None and body["notes"] is None
    assert body["link"] is None and body["link_type"] is None


def test_clearing_the_link_of_an_email_task_restores_the_gmail_fallback(api, engine, uid):
    task_id = add_task(
        engine, uid, source_ref="msg123", link="https://example.com/x", link_type="action"
    )
    body = api.patch(f"/tasks/{task_id}", json={"link": ""}).json()
    assert body["link"] == "https://mail.google.com/mail/u/0/#all/msg123"
    assert body["link_type"] == "gmail"


def test_patch_sets_a_user_link_as_an_action_link(api, engine, uid):
    task_id = add_task(engine, uid)
    body = api.patch(f"/tasks/{task_id}", json={"link": "https://forms.gle/abc"}).json()
    assert (body["link_type"], body["link_domain"], body["link_confidence"]) == (
        "action", "forms.gle", 1.0,
    )  # fmt: skip


def test_patch_status_values(api, engine, uid):
    task_id = add_task(engine, uid)
    assert api.patch(f"/tasks/{task_id}", json={"status": "done"}).json()["status"] == "done"
    assert api.patch(f"/tasks/{task_id}", json={"status": "open"}).json()["status"] == "open"
    assert api.patch(f"/tasks/{task_id}", json={"status": "needs_review"}).status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": None},
        {"title": ""},
        {"priority": None},
        {"priority": "meh"},
        {"status": None},
        {"deadline": "yesterday-ish"},
        {"link": "javascript:alert(1)"},
        {"link": "ftp://example.com"},
    ],
)
def test_patch_validation_errors(api, engine, uid, payload):
    task_id = add_task(engine, uid, title="stay")
    assert api.patch(f"/tasks/{task_id}", json=payload).status_code == 422
    assert api.get(f"/tasks/{task_id}").json()["title"] == "stay"


def test_patch_404(api):
    assert api.patch("/tasks/999999", json={"title": "x"}).status_code == 404


def test_patch_moves_updated_at_forward(api, engine, uid):
    task_id = add_task(engine, uid)
    before = api.get(f"/tasks/{task_id}").json()["updated_at"]
    after = api.patch(f"/tasks/{task_id}", json={"title": "x"}).json()["updated_at"]
    assert datetime.fromisoformat(after) > datetime.fromisoformat(before)


# ---------- delete / done / dismiss ----------


def test_delete_removes_the_task_and_its_reminders(api, engine, uid):
    task_id = add_task(engine, uid)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO reminders (task_id, fire_at, channel) VALUES (:t, now(), 'push')"),
            {"t": task_id},
        )
    assert api.delete(f"/tasks/{task_id}").status_code == 204
    assert api.get(f"/tasks/{task_id}").status_code == 404
    assert api.delete(f"/tasks/{task_id}").status_code == 404
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM reminders")).scalar_one() == 0


@pytest.mark.parametrize("action,status", [("done", "done"), ("dismiss", "dismissed")])
def test_done_and_dismiss(api, engine, uid, action, status):
    task_id = add_task(engine, uid)
    resp = api.post(f"/tasks/{task_id}/{action}")
    assert (resp.status_code, resp.json()["status"]) == (200, status)
    assert api.post(f"/tasks/{task_id}/{action}").status_code == 200  # idempotent
    assert api.post(f"/tasks/999999/{action}").status_code == 404


@pytest.mark.parametrize("how", ["done", "dismiss", "patch"])
def test_closing_a_task_cancels_pending_reminders_only(api, engine, uid, how):
    task_id = add_task(engine, uid)
    with engine.begin() as conn:
        for channel, sent in (("push", None), ("email", None), ("inapp", "now()")):
            conn.execute(
                text(
                    "INSERT INTO reminders (task_id, fire_at, channel, sent_at) "
                    f"VALUES (:t, now(), :c, {sent or 'NULL'})"
                ),
                {"t": task_id, "c": channel},
            )
    if how == "patch":
        api.patch(f"/tasks/{task_id}", json={"status": "done"})
    else:
        api.post(f"/tasks/{task_id}/{how}")
    with engine.connect() as conn:
        left = conn.execute(text("SELECT channel FROM reminders")).scalars().all()
    assert left == ["inapp"]  # the one already sent is history, the pending ones are gone


# ---------- review ----------


def test_review_lists_only_needs_review_tasks(api, engine, uid):
    a = add_task(
        engine, uid, title="a", status="needs_review", deadline="2030-02-01T00:00:00+00:00"
    )
    b = add_task(engine, uid, title="b", status="needs_review")
    add_task(engine, uid, title="c", status="open")
    assert [t["id"] for t in api.get("/review").json()] == [a, b]


def test_confirm_with_no_body_opens_the_task(api, engine, uid):
    task_id = add_task(engine, uid, status="needs_review", confidence=0.3)
    body = api.post(f"/review/{task_id}/confirm").json()
    assert (body["status"], body["confidence"]) == ("open", 1.0)
    assert api.get("/review").json() == []


def test_confirm_with_edits(api, engine, uid):
    task_id = add_task(engine, uid, title="old", status="needs_review", priority="regular")
    soon = (datetime.now(UTC) + timedelta(hours=10)).isoformat()
    body = api.post(
        f"/review/{task_id}/confirm",
        json={"title": "New title", "deadline": soon, "link": "https://example.com/go"},
    ).json()
    assert (body["title"], body["status"], body["priority"]) == ("New title", "open", "urgent")
    assert (body["link"], body["link_type"]) == ("https://example.com/go", "action")


def test_confirm_errors(api, engine, uid):
    open_id = add_task(engine, uid, status="open")
    review_id = add_task(engine, uid, status="needs_review")
    assert api.post(f"/review/{open_id}/confirm").status_code == 409
    assert api.post("/review/999999/confirm").status_code == 404
    assert (
        api.post(f"/review/{review_id}/confirm", json={"link": "javascript:1"}).status_code == 422
    )
    assert api.post(f"/review/{review_id}/confirm", json={"title": " "}).status_code == 422
    assert api.get(f"/tasks/{review_id}").json()["status"] == "needs_review"  # untouched


# ---------- quarantine ----------


def add_quarantine(engine, uid, message_id, status="held", subject="Suspicious subject") -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO quarantine "
                "(user_id, message_id, subject, sender, score, reasons, status) "
                "VALUES (:u, :m, :s, 'Sender <s@example.test>', 6, CAST(:r AS jsonb), :st) "
                "RETURNING id"
            ),
            {
                "u": uid,
                "m": message_id,
                "s": subject,
                "r": json.dumps(["Reason one"]),
                "st": status,
            },
        ).scalar_one()


def test_quarantine_list_defaults_to_held(api, engine, uid):
    held = add_quarantine(engine, uid, "m1")
    add_quarantine(engine, uid, "m2", status="rescued")
    rows = api.get("/quarantine").json()
    assert [r["id"] for r in rows] == [held]
    assert rows[0]["reasons"] == ["Reason one"] and rows[0]["score"] == 6
    assert len(api.get("/quarantine?status=all").json()) == 2
    assert len(api.get("/quarantine?status=rescued").json()) == 1
    assert api.get("/quarantine?status=bogus").status_code == 422


def test_rescuing_a_quarantined_message_creates_a_task_with_a_link(api, engine, uid):
    raw = raw_fixture("task_emails", "01_nptel_quiz_with_noise_links.json")
    qid = add_quarantine(engine, uid, raw["id"])
    use_gmail(FakeGmailApi({raw["id"]: raw}))

    resp = api.post(f"/quarantine/{qid}/rescue")

    assert resp.status_code == 200
    body = resp.json()
    assert body["task_created"] is True
    task = body["task"]
    assert task["link"].endswith("assessment=20") and task["link_type"] == "action"
    assert task["source"] == "other"  # the user vouched for it; no source decided it
    assert body["quarantine"]["status"] == "rescued"
    with engine.connect() as conn:  # a rescued task is the user's own: a rebuild must keep it
        assert conn.execute(text("SELECT user_confirmed FROM tasks")).scalar_one() is True
    assert api.get("/quarantine").json() == []
    assert [t["id"] for t in api.get("/tasks").json()] == [task["id"]]
    with engine.connect() as conn:
        row = conn.execute(text("SELECT decision, reason FROM processed_messages")).one()
    assert tuple(row) == ("passed", "rescued")


def test_rescued_message_without_links_still_gets_the_gmail_fallback(api, engine, uid):
    raw = raw_fixture("filter_emails", "scam", "s01_fake_nptel_certificate_fee.json")
    qid = add_quarantine(engine, uid, raw["id"])
    use_gmail(FakeGmailApi({raw["id"]: raw}))
    task = api.post(f"/quarantine/{qid}/rescue").json()["task"]
    assert task["link"] == f"https://mail.google.com/mail/u/0/#all/{raw['id']}"
    assert task["link_type"] == "gmail"


def test_a_rescued_email_without_a_deadline_goes_to_open_tasks_not_review(api, engine, uid):
    raw = raw_fixture("task_emails", "10_college_timetable_no_deadline.json")
    qid = add_quarantine(engine, uid, raw["id"])
    use_gmail(FakeGmailApi({raw["id"]: raw}))

    task = api.post(f"/quarantine/{qid}/rescue").json()["task"]

    assert task["deadline"] is None  # nothing for the extractor to find...
    assert task["status"] == "open"  # ...but the user vouched for it, so it is not "needs review"
    assert api.get("/review").json() == []
    assert [t["id"] for t in api.get("/tasks?status=open").json()] == [task["id"]]


def test_rescue_twice_and_unknown_id(api, engine, uid):
    raw = raw_fixture("task_emails", "04_gform_plain_link.json")
    qid = add_quarantine(engine, uid, raw["id"])
    use_gmail(FakeGmailApi({raw["id"]: raw}))
    assert api.post(f"/quarantine/{qid}/rescue").status_code == 200
    assert api.post(f"/quarantine/{qid}/rescue").status_code == 409
    assert api.post("/quarantine/999999/rescue").status_code == 404
    assert len(api.get("/tasks").json()) == 1  # no duplicate


@pytest.mark.parametrize(
    "error,status",
    [
        (MessageGone("m"), 410),
        (RateLimited("slow down"), 429),
        (http_error(500), 502),
    ],
)
def test_rescue_gmail_failures_leave_the_message_held(api, engine, uid, error, status):
    qid = add_quarantine(engine, uid, "mX")
    fake = FakeGmailApi({})
    fake.get_errors["mX"] = error
    use_gmail(fake)
    assert api.post(f"/quarantine/{qid}/rescue").status_code == status
    assert [r["id"] for r in api.get("/quarantine").json()] == [qid]
    assert api.get("/tasks").json() == []


# ---------- settings ----------

DEFAULT_SETTINGS = {
    "sources": [
        {"name": "NPTEL", "type": "nptel", "senders": ["nptel.ac.in"]},
        {"name": "Digii", "type": "digii", "senders": []},
        {"name": "Internship", "type": "internship", "senders": []},
    ],
    "watch_keywords": ["hackathon", "ideathon", "datathon"],
    "block_senders": [],
    "require_deadline": False,
    "digest_hour": 8,
}


def test_get_settings_creates_the_three_default_sources_and_keywords(api):
    assert api.get("/settings").json() == DEFAULT_SETTINGS


def test_put_settings_replaces_normalizes_and_feeds_the_gmail_query(api):
    resp = api.put(
        "/settings",
        json={
            "sources": [
                {"name": "NPTEL", "type": "nptel", "senders": ["Nptel.AC.in", "@nptel.ac.in"]},
                {"name": "My Digii", "type": "digii", "senders": ["app.digii.example"]},
                {"name": "Jobs", "type": "custom", "senders": ["Alerts@Jobs.test"]},
            ],
            "watch_keywords": ["Hackathon", "smart  india", "hackathon"],
            "block_senders": ["Spammy.Example", "spammy.example", "@news.test"],
            "require_deadline": True,
            "digest_hour": 20,
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "sources": [
            {"name": "NPTEL", "type": "nptel", "senders": ["nptel.ac.in"]},
            {"name": "My Digii", "type": "digii", "senders": ["app.digii.example"]},
            {"name": "Jobs", "type": "custom", "senders": ["alerts@jobs.test"]},
        ],
        "watch_keywords": ["hackathon", "smart india"],
        "block_senders": ["spammy.example", "news.test"],
        "require_deadline": True,
        "digest_hour": 20,
    }
    assert api.get("/settings").json() == resp.json()  # persists
    query = build_query(load_sources(resp.json()["sources"]), resp.json()["watch_keywords"])
    assert query == (
        "newer_than:30d -in:spam -in:trash (from:nptel.ac.in OR from:app.digii.example OR "
        'from:alerts@jobs.test OR hackathon OR "smart india")'
    )
    assert "spammy.example" not in query  # block senders are not part of the search


def test_a_source_may_have_no_senders_yet(api):
    body = {**DEFAULT_SETTINGS, "sources": [{"name": "Digii", "type": "digii", "senders": []}]}
    assert api.put("/settings", json=body).status_code == 200
    assert api.get("/settings").json()["sources"] == body["sources"]


@pytest.mark.parametrize(
    "change",
    [
        {"sources": [{"name": "X", "type": "banana", "senders": []}]},
        {"sources": [{"name": "", "type": "custom", "senders": []}]},
        {"sources": [{"name": "X", "type": "custom", "senders": ["javascript:alert(1)"]}]},
        {"sources": [{"name": "X", "type": "custom", "senders": ["a b"]}]},
        {
            "sources": [
                {"name": "X", "type": "custom", "senders": ["x.test OR from:boss@work.test"]}
            ]
        },
        {"sources": [{"name": "X", "type": "custom", "senders": [""]}]},
        {
            "sources": [
                {"name": "X", "type": "custom", "senders": [f"d{i}.test" for i in range(201)]}
            ]
        },
        {
            "sources": [
                {"name": "Same", "type": "custom", "senders": []},
                {"name": "same", "type": "nptel", "senders": []},
            ]
        },
        {"sources": [{"name": f"S{i}", "type": "custom", "senders": []} for i in range(21)]},
        {"watch_keywords": ["a) OR (from:boss@work.test"]},
        {"watch_keywords": ["in:inbox"]},
        {"watch_keywords": [""]},
        {"watch_keywords": [f"k{i}" for i in range(51)]},
        {"block_senders": ["-bad"]},
        {"block_senders": "linkedin.com"},
        {"digest_hour": 24},
        {"digest_hour": -1},
        {"require_deadline": "maybe"},
    ],
)
def test_put_settings_validation_errors(api, change):
    before = api.get("/settings").json()
    assert api.put("/settings", json={**DEFAULT_SETTINGS, **change}).status_code == 422
    assert api.get("/settings").json() == before


def test_put_settings_requires_every_field(api):
    for missing in ("sources", "watch_keywords", "block_senders", "digest_hour"):
        body = {k: v for k, v in DEFAULT_SETTINGS.items() if k != missing}
        assert api.put("/settings", json=body).status_code == 422


def test_settings_are_per_user(api, api_b):
    api.put("/settings", json={**DEFAULT_SETTINGS, "require_deadline": True})
    assert api_b.get("/settings").json()["require_deadline"] is False


# ---------- per-user isolation ----------


def test_user_a_cannot_read_edit_or_delete_user_bs_tasks(api, api_b, engine, uid):
    task_id = add_task(engine, uid, title="A's secret", status="needs_review")
    review_id = task_id

    assert api_b.get(f"/tasks/{task_id}").status_code == 404
    assert api_b.patch(f"/tasks/{task_id}", json={"title": "hacked"}).status_code == 404
    assert api_b.delete(f"/tasks/{task_id}").status_code == 404
    assert api_b.post(f"/tasks/{task_id}/done").status_code == 404
    assert api_b.post(f"/tasks/{task_id}/dismiss").status_code == 404
    assert api_b.post(f"/review/{review_id}/confirm").status_code == 404
    assert api_b.get("/tasks").json() == []
    assert api_b.get("/review").json() == []

    mine = api.get(f"/tasks/{task_id}").json()
    assert (mine["title"], mine["status"]) == ("A's secret", "needs_review")


def test_users_cannot_see_or_rescue_each_others_quarantine(api, api_b, engine, uid):
    qid = add_quarantine(engine, uid, "mA")
    use_gmail(FakeGmailApi({}))
    assert api_b.get("/quarantine?status=all").json() == []
    assert api_b.post(f"/quarantine/{qid}/rescue").status_code == 404
    assert [r["id"] for r in api.get("/quarantine").json()] == [qid]


def test_settings_and_new_tasks_are_per_user(api, api_b):
    api.put(
        "/settings", json={**DEFAULT_SETTINGS, "block_senders": ["only-a.test"], "digest_hour": 5}
    )
    assert api_b.get("/settings").json()["block_senders"] == []
    api.post("/tasks", json={"title": "mine"})
    assert api_b.get("/tasks").json() == []


def test_the_task_filters_never_leak_across_users(api, api_b, engine, uid):
    add_task(engine, uid, source="nptel", priority="urgent")
    assert api_b.get("/tasks?source=nptel&priority=urgent&status=open").json() == []


# ---------- priority recompute ----------


def recompute(session, **kw):
    n = recompute_priorities(session, **kw)
    session.commit()
    return n


def test_recompute_upgrades_a_task_once_its_deadline_is_within_48_hours(db_session, engine, uid):
    task_id = add_task(
        engine, uid, deadline="2026-01-10T12:00:00+00:00", priority="important", status="open"
    )

    def priority():
        return db_session.scalar(select(Task.priority).where(Task.id == task_id))

    with freeze_time("2026-01-07 11:00:00"):  # 73 hours before
        assert recompute(db_session) == 0
        db_session.expire_all()
        assert priority() == "important"
    with freeze_time("2026-01-08 13:00:00"):  # 47 hours before
        assert recompute(db_session) == 1
        db_session.expire_all()
        assert priority() == "urgent"
    with freeze_time("2026-01-08 14:00:00"):  # already urgent: nothing to do
        assert recompute(db_session) == 0


def test_recompute_boundary_is_exactly_48_hours(db_session, engine, uid):
    add_task(engine, uid, deadline="2026-01-10T12:00:00+00:00", priority="regular")
    with freeze_time("2026-01-08 11:59:59"):
        assert recompute(db_session) == 0
    with freeze_time("2026-01-08 12:00:00"):
        assert recompute(db_session) == 1


def test_recompute_covers_overdue_but_skips_non_open_and_deadline_less_tasks(
    db_session, engine, uid
):
    overdue = add_task(engine, uid, deadline="2026-01-01T00:00:00+00:00", priority="regular")
    add_task(engine, uid, deadline="2026-01-09T00:00:00+00:00", status="done", priority="regular")
    add_task(engine, uid, deadline="2026-01-09T00:00:00+00:00", status="needs_review")
    add_task(engine, uid, deadline="2026-01-09T00:00:00+00:00", status="dismissed")
    add_task(engine, uid, priority="regular")  # no deadline
    with freeze_time("2026-01-08 12:00:00"):
        assert recompute(db_session) == 1
    rows = dict(db_session.execute(select(Task.id, Task.priority)).all())
    assert rows[overdue] == "urgent"
    assert sorted(rows.values()).count("urgent") == 1


def test_recompute_can_be_limited_to_one_user(db_session, engine, uid, uid_b):
    add_task(engine, uid, deadline="2026-01-09T00:00:00+00:00", priority="regular")
    add_task(engine, uid_b, deadline="2026-01-09T00:00:00+00:00", priority="regular")
    with freeze_time("2026-01-08 12:00:00"):
        assert recompute(db_session, user_id=uid) == 1


def test_recompute_accepts_an_explicit_now(db_session, engine, uid):
    add_task(engine, uid, deadline="2030-01-02T00:00:00+00:00", priority="regular")
    assert recompute(db_session, now=datetime(2030, 1, 1, tzinfo=UTC)) == 1


def test_recompute_does_not_touch_reminders_or_other_columns(db_session, engine, uid):
    task_id = add_task(engine, uid, deadline="2026-01-09T00:00:00+00:00", title="keep")
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO reminders (task_id, fire_at, channel) VALUES (:t, now(), 'push')"),
            {"t": task_id},
        )
    with freeze_time("2026-01-08 12:00:00"):
        recompute(db_session)
    assert db_session.scalar(select(Task.title)) == "keep"
    assert db_session.scalar(select(Reminder.channel)) == "push"


# ---------- docs ----------


def test_docs_and_openapi_list_every_route(client_factory):
    c = client_factory()
    assert c.get("/docs").status_code == 200
    spec = c.get("/openapi.json").json()
    listed = {(m.upper(), p) for p, item in spec["paths"].items() for m in item}
    expected = {
        ("GET", "/tasks"), ("POST", "/tasks"),
        ("GET", "/tasks/{task_id}"), ("PATCH", "/tasks/{task_id}"), ("DELETE", "/tasks/{task_id}"),
        ("POST", "/tasks/{task_id}/done"), ("POST", "/tasks/{task_id}/dismiss"),
        ("GET", "/review"), ("POST", "/review/{task_id}/confirm"),
        ("GET", "/quarantine"), ("POST", "/quarantine/{quarantine_id}/rescue"),
        ("GET", "/settings"), ("PUT", "/settings"),
    }  # fmt: skip
    assert expected <= listed
    assert len(ALL_ROUTES) == len(expected)  # the auth test above covers each of them
