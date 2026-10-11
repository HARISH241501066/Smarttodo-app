"""Smart folders: rules (OR inside a type, AND across types), pins, counts, preview, isolation."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text


def iso(**delta) -> str:
    return (datetime.now(UTC) + timedelta(**delta)).isoformat()


def email_task(engine, uid, title, source_name="NPTEL", source="nptel", priority="regular",
               deadline=None, status="open", tag=None, snoozed_until=None) -> int:  # fmt: skip
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO tasks (user_id, title, source, source_name, source_ref, status, "
                "priority, deadline, tag, snoozed_until) VALUES (:u, :t, :s, :n, :r, :st, :p, :d, "
                ":tag, :sn) RETURNING id"
            ),
            {"u": uid, "t": title, "s": source, "n": source_name, "r": f"ref-{title}", "st": status,
             "p": priority, "d": deadline, "tag": tag, "sn": snoozed_until},
        ).scalar_one()  # fmt: skip


def manual(api, title, **kw) -> int:
    resp = api.post("/tasks", json={"title": title, **kw})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def folder(api, name="F", **rules) -> dict:
    show = rules.pop("show_completed", False)
    resp = api.post("/folders", json={"name": name, "rules": rules, "show_completed": show})
    assert resp.status_code == 201, resp.text
    return resp.json()


def titles(api, folder_id) -> list[str]:
    groups = api.get(f"/folders/{folder_id}/tasks").json()["groups"]
    return [t["title"] for g in groups for t in g["tasks"]]


# ---------- rules ----------


def test_a_folder_holds_only_the_tasks_its_sources_name_and_new_ones_arrive_by_themselves(
    api, engine, uid
):
    email_task(engine, uid, "digii quiz", "Digii", "digii")
    email_task(engine, uid, "nptel quiz", "NPTEL", "nptel")
    email_task(engine, uid, "internship alert", "Internship", "internship")
    email_task(engine, uid, "hack", "Hackathon", "keyword")
    manual(api, "my own")
    f = folder(api, "College", sources=["Digii", "nptel"])  # case is forgiven, spelling is stored
    assert f["rules"]["sources"] == ["Digii", "NPTEL"]
    assert sorted(titles(api, f["id"])) == ["digii quiz", "nptel quiz"]

    email_task(engine, uid, "brand new nptel task", "NPTEL", "nptel")  # no extra step
    assert sorted(titles(api, f["id"])) == ["brand new nptel task", "digii quiz", "nptel quiz"]


def test_hackathon_and_manual_are_sources_too(api, engine, uid):
    email_task(engine, uid, "hack", "Hackathon", "keyword")
    email_task(engine, uid, "ideas", "Ideathon", "keyword")
    email_task(engine, uid, "nptel", "NPTEL", "nptel")
    manual(api, "mine")
    assert sorted(titles(api, folder(api, "H", sources=["hackathon"])["id"])) == ["hack", "ideas"]
    assert titles(api, folder(api, "M", sources=["manual"])["id"]) == ["mine"]
    assert sorted(titles(api, folder(api, "Both", sources=["manual", "hackathon"])["id"])) == [
        "hack", "ideas", "mine",
    ]  # fmt: skip


def test_or_inside_a_rule_type_and_across_types(api, engine, uid):
    email_task(engine, uid, "digii urgent", "Digii", "digii", priority="urgent")
    email_task(engine, uid, "nptel urgent", "NPTEL", "nptel", priority="urgent")
    email_task(engine, uid, "nptel regular", "NPTEL", "nptel", priority="regular")
    email_task(engine, uid, "internship urgent", "Internship", "internship", priority="urgent")
    f = folder(api, "Urgent college", sources=["Digii", "NPTEL"], priorities=["urgent"])
    assert sorted(titles(api, f["id"])) == ["digii urgent", "nptel urgent"]
    either = folder(api, "Two priorities", priorities=["urgent", "regular"], sources=["NPTEL"])
    assert sorted(titles(api, either["id"])) == ["nptel regular", "nptel urgent"]


def test_tag_and_title_rules(api, engine, uid):
    manual(api, "Write essay", tag="Uni")
    manual(api, "Buy milk", tag="Home")
    manual(api, "Essay review", tag="Home")
    manual(api, "No tag essay")
    assert sorted(titles(api, folder(api, "Uni", tags=["uni"])["id"])) == ["Write essay"]
    assert sorted(titles(api, folder(api, "Essays", title_contains="ESSAY")["id"])) == [
        "Essay review", "No tag essay", "Write essay",
    ]  # fmt: skip
    both = folder(api, "Home essays", tags=["Home"], title_contains="essay")
    assert titles(api, both["id"]) == ["Essay review"]
    # % and _ in the text are plain characters, not wildcards
    manual(api, "100% done")
    assert titles(api, folder(api, "pct", title_contains="100%")["id"]) == ["100% done"]
    assert titles(api, folder(api, "wild", title_contains="%")["id"]) == ["100% done"]


def test_a_folder_without_rules_shows_only_pinned_tasks(api):
    manual(api, "a")
    b = manual(api, "b")
    f = folder(api, "Empty")
    assert titles(api, f["id"]) == []
    assert f["open_count"] == 0
    assert api.post(f"/folders/{f['id']}/pins/{b}").status_code == 204
    assert titles(api, f["id"]) == ["b"]


def test_unknown_sources_and_bad_values_are_rejected(api):
    for rules in (
        {"sources": ["Nonexistent"]},
        {"priorities": ["super"]},
        {"sources": [" "]},
        {"title_contains": "x" * 101},
        {"tags": ["x" * 61]},
    ):
        resp = api.post("/folders", json={"name": "Bad", "rules": rules})
        assert resp.status_code == 422, rules
    for body in ({"name": " "}, {"name": "x" * 61}, {"name": "ok", "color": "red"}, {}):
        assert api.post("/folders", json=body).status_code == 422
    assert api.get("/folders").json() == []


# ---------- pins ----------


def test_a_pinned_task_appears_even_if_it_does_not_match_and_unpinning_removes_it(api, engine, uid):
    email_task(engine, uid, "nptel one", "NPTEL", "nptel")
    mine = manual(api, "my manual")
    f = folder(api, "College", sources=["NPTEL"])
    assert titles(api, f["id"]) == ["nptel one"]

    assert api.post(f"/folders/{f['id']}/pins/{mine}").status_code == 204
    assert api.post(f"/folders/{f['id']}/pins/{mine}").status_code == 204  # idempotent
    body = api.get(f"/folders/{f['id']}/tasks").json()
    assert sorted(titles(api, f["id"])) == ["my manual", "nptel one"]
    assert (
        body["pinned_ids"] == [mine] and len(body["rule_ids"]) == 1 and mine not in body["rule_ids"]
    )

    assert api.delete(f"/folders/{f['id']}/pins/{mine}").status_code == 204
    assert api.delete(f"/folders/{f['id']}/pins/{mine}").status_code == 204  # already gone
    assert titles(api, f["id"]) == ["nptel one"]


def test_a_pinned_task_that_also_matches_is_listed_once(api, engine, uid):
    t = email_task(engine, uid, "both ways", "NPTEL", "nptel")
    f = folder(api, "F", sources=["NPTEL"])
    api.post(f"/folders/{f['id']}/pins/{t}")
    body = api.get(f"/folders/{f['id']}/tasks").json()
    assert titles(api, f["id"]) == ["both ways"]
    assert body["pinned_ids"] == [t] and body["rule_ids"] == [t]


def test_the_same_task_can_be_in_two_folders(api, engine, uid):
    t = email_task(engine, uid, "shared", "NPTEL", "nptel", priority="urgent")
    a = folder(api, "A", sources=["NPTEL"])
    b = folder(api, "B", priorities=["urgent"])
    c = folder(api, "C")
    api.post(f"/folders/{c['id']}/pins/{t}")
    for f in (a, b, c):
        assert titles(api, f["id"]) == ["shared"]


def test_task_to_folder_pinned_flag(api):
    t = manual(api, "t")
    a = folder(api, "A")
    b = folder(api, "B")
    api.post(f"/folders/{b['id']}/pins/{t}")
    flags = {f["name"]: f["pinned"] for f in api.get(f"/folders?task_id={t}").json()}
    assert flags == {"A": False, "B": True}
    assert all(f["pinned"] is None for f in api.get("/folders").json())
    assert a["id"]


# ---------- deleting ----------


def test_deleting_a_folder_leaves_every_task_and_deleting_a_task_removes_its_pins(api, engine, uid):
    email_task(engine, uid, "nptel", "NPTEL", "nptel")
    mine = manual(api, "mine")
    f = folder(api, "F", sources=["NPTEL"])
    api.post(f"/folders/{f['id']}/pins/{mine}")
    before = sorted(t["title"] for t in api.get("/tasks?limit=500").json())

    assert api.delete(f"/folders/{f['id']}").status_code == 204
    assert api.get(f"/folders/{f['id']}/tasks").status_code == 404
    assert sorted(t["title"] for t in api.get("/tasks?limit=500").json()) == before
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM folder_pins")).scalar_one() == 0

    g = folder(api, "G")
    api.post(f"/folders/{g['id']}/pins/{mine}")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM folder_pins")).scalar_one() == 1
    assert api.delete(f"/tasks/{mine}").status_code == 204
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM folder_pins")).scalar_one() == 0
    assert api.get("/folders").json()[0]["name"] == "G"  # the folder itself stays


def test_deleting_a_source_in_settings_removes_it_from_folder_rules_with_a_notice(api):
    f = folder(api, "College", sources=["Digii", "NPTEL"])
    other = folder(api, "Other", sources=["Digii"], priorities=["urgent"])
    settings = api.get("/settings").json()
    settings["sources"] = [s for s in settings["sources"] if s["name"] != "Digii"]
    resp = api.put("/settings", json=settings)
    assert resp.status_code == 200
    notices = resp.json()["notices"]
    assert len(notices) == 2 and all("Digii" in n for n in notices)
    assert any("College" in n for n in notices) and any("Other" in n for n in notices)
    rules = {x["id"]: x["rules"] for x in api.get("/folders").json()}
    assert rules[f["id"]]["sources"] == ["NPTEL"]
    assert rules[other["id"]]["sources"] == [] and rules[other["id"]]["priorities"] == ["urgent"]
    # saving again, with nothing removed, gives no notice
    assert api.put("/settings", json=settings).json()["notices"] == []


# ---------- visibility ----------


def test_snoozed_tasks_are_hidden_and_done_tasks_only_when_the_folder_asks(api, engine, uid):
    email_task(engine, uid, "visible", "NPTEL", "nptel")
    email_task(
        engine,
        uid,
        "snoozed",
        "NPTEL",
        "nptel",
        snoozed_until=datetime.now(UTC) + timedelta(days=1),
    )
    email_task(
        engine,
        uid,
        "wake up",
        "NPTEL",
        "nptel",
        snoozed_until=datetime.now(UTC) - timedelta(hours=1),
    )
    email_task(engine, uid, "finished", "NPTEL", "nptel", status="done")
    email_task(engine, uid, "dismissed", "NPTEL", "nptel", status="dismissed")
    email_task(engine, uid, "review", "NPTEL", "nptel", status="needs_review")
    plain = folder(api, "plain", sources=["NPTEL"])
    with_done = folder(api, "with done", sources=["NPTEL"], show_completed=True)
    assert sorted(titles(api, plain["id"])) == ["visible", "wake up"]
    assert sorted(titles(api, with_done["id"])) == ["finished", "visible", "wake up"]
    done_group = [
        g
        for g in api.get(f"/folders/{with_done['id']}/tasks").json()["groups"]
        if g["key"] == "completed"
    ]
    assert [t["title"] for t in done_group[0]["tasks"]] == ["finished"]
    assert {f["name"]: f["open_count"] for f in api.get("/folders").json()} == {
        "plain": 2,
        "with done": 2,
    }


def test_a_pinned_snoozed_task_is_hidden_too(api):
    t = manual(api, "later")
    f = folder(api, "F")
    api.post(f"/folders/{f['id']}/pins/{t}")
    assert titles(api, f["id"]) == ["later"]
    assert api.patch(f"/tasks/{t}", json={"snoozed_until": iso(days=2)}).status_code == 200
    assert titles(api, f["id"]) == []
    assert "later" not in [x["title"] for x in api.get("/tasks?status=open").json()]
    assert "later" not in [x["title"] for x in api.get("/deadlines").json()]
    assert api.patch(f"/tasks/{t}", json={"snoozed_until": None}).status_code == 200
    assert titles(api, f["id"]) == ["later"]


def test_expired_email_tasks_leave_a_folder_by_themselves(api, engine, uid):
    email_task(
        engine, uid, "late email", "NPTEL", "nptel", deadline=datetime.now(UTC) - timedelta(hours=2)
    )
    f = folder(api, "F", sources=["NPTEL"])
    assert titles(api, f["id"]) == []
    assert api.get("/folders").json()[0]["open_count"] == 0


def test_the_folder_page_is_grouped_like_the_deadlines_page(api, engine, uid):
    manual(api, "overdue", deadline=iso(days=-2))
    manual(api, "today", deadline=iso(minutes=2))
    manual(api, "week", deadline=iso(days=3, hours=1))
    manual(api, "later", deadline=iso(days=20))
    manual(api, "none")
    f = folder(api, "All manual", sources=["manual"])
    groups = api.get(f"/folders/{f['id']}/tasks").json()["groups"]
    by_key = {g["key"]: [t["title"] for t in g["tasks"]] for g in groups}
    assert by_key == {
        "overdue": ["overdue"],
        "today": ["today"],
        "week": ["week"],
        "later": ["later"],
        "none": ["none"],
    }
    assert [g["key"] for g in groups] == ["overdue", "today", "week", "later", "none"]
    assert [g["label"] for g in groups][0] == "Overdue"


def test_open_count_and_overdue_dot(api, engine, uid):
    manual(api, "late", deadline=iso(days=-1))
    manual(api, "ok", deadline=iso(days=5))
    f = folder(api, "Manual", sources=["manual"])
    quiet = folder(api, "Quiet", title_contains="ok")
    flags = {x["id"]: (x["open_count"], x["has_overdue"]) for x in api.get("/folders").json()}
    assert flags[f["id"]] == (2, True)
    assert flags[quiet["id"]] == (1, False)


# ---------- preview ----------


def test_preview_counts_what_the_folder_would_hold(api, engine, uid):
    email_task(engine, uid, "d", "Digii", "digii", priority="urgent")
    email_task(engine, uid, "n", "NPTEL", "nptel", priority="urgent")
    email_task(engine, uid, "n2", "NPTEL", "nptel")
    email_task(engine, uid, "done", "NPTEL", "nptel", status="done")
    mine = manual(api, "pinned one")
    f = folder(api, "F", sources=["Digii", "NPTEL"], priorities=["urgent"])
    api.post(f"/folders/{f['id']}/pins/{mine}")

    count = lambda q: api.get(f"/folders/preview?{q}").json()["count"]  # noqa: E731
    assert count("sources=Digii&sources=NPTEL&priorities=urgent") == 2
    assert count("sources=NPTEL") == 2
    assert count("") == 0  # no rules, nothing pinned
    assert count("sources=NPTEL&show_completed=true") == 3
    assert (
        count(f"sources=Digii&sources=NPTEL&priorities=urgent&folder_id={f['id']}") == 3
    )  # + the pin
    # and it always equals the real contents
    real = len(titles(api, f["id"]))
    assert count(f"sources=Digii&sources=NPTEL&priorities=urgent&folder_id={f['id']}") == real


def test_preview_rejects_unknown_sources_and_foreign_folders(api, api_b):
    assert api.get("/folders/preview?sources=Nope").status_code == 422
    other = folder(api_b, "B's")
    assert api.get(f"/folders/preview?folder_id={other['id']}").status_code == 404


# ---------- order ----------


def test_folders_keep_the_order_they_are_given(api):
    a, b, c = (folder(api, n) for n in "ABC")
    assert [f["name"] for f in api.get("/folders").json()] == ["A", "B", "C"]
    moved = api.patch("/folders/reorder", json={"ids": [c["id"], a["id"], b["id"]]})
    assert [f["name"] for f in moved.json()] == ["C", "A", "B"]
    assert [f["name"] for f in api.get("/folders").json()] == ["C", "A", "B"]
    for bad in ([a["id"]], [a["id"], a["id"], b["id"]], [a["id"], b["id"], c["id"], 999], []):
        assert api.patch("/folders/reorder", json={"ids": bad}).status_code == 422
    assert folder(api, "D")["position"] == 3


def test_edit_a_folder(api, engine, uid):
    email_task(engine, uid, "nptel", "NPTEL", "nptel")
    email_task(engine, uid, "digii", "Digii", "digii")
    f = folder(api, "F", sources=["NPTEL"])
    assert titles(api, f["id"]) == ["nptel"]
    edited = api.patch(
        f"/folders/{f['id']}",
        json={
            "rules": {"sources": ["Digii"]},
            "name": " Renamed ",
            "emoji": "🎓",
            "color": "#AA00FF",
        },
    )
    assert edited.status_code == 200
    assert (edited.json()["name"], edited.json()["emoji"], edited.json()["color"]) == (
        "Renamed",
        "🎓",
        "#aa00ff",
    )
    assert titles(api, f["id"]) == ["digii"]  # applies instantly
    assert api.patch(f"/folders/{f['id']}", json={}).status_code == 422
    assert api.patch(f"/folders/{f['id']}", json={"name": None}).status_code == 422
    assert (
        api.patch(f"/folders/{f['id']}", json={"rules": {"sources": ["Nope"]}}).status_code == 422
    )


# ---------- isolation ----------


def test_a_user_cannot_see_or_edit_another_users_folders(api, api_b, engine, uid):
    mine = manual(api, "mine")
    f = folder(api, "Private", sources=["manual"])
    api.post(f"/folders/{f['id']}/pins/{mine}")
    assert api_b.get("/folders").json() == []
    fid = f["id"]
    assert api_b.get(f"/folders/{fid}/tasks").status_code == 404
    assert api_b.patch(f"/folders/{fid}", json={"name": "x"}).status_code == 404
    assert api_b.delete(f"/folders/{fid}").status_code == 404
    assert api_b.post(f"/folders/{fid}/pins/{mine}").status_code == 404
    assert api_b.delete(f"/folders/{fid}/pins/{mine}").status_code == 404
    assert api_b.patch("/folders/reorder", json={"ids": [fid]}).status_code == 422
    # and cannot pin its own folder with my task
    theirs = folder(api_b, "Theirs")
    assert api_b.post(f"/folders/{theirs['id']}/pins/{mine}").status_code == 404
    # nothing of mine leaked into their folder of the same rules
    other = folder(api_b, "Same rules", sources=["manual"])
    assert titles(api_b, other["id"]) == []
    assert api.get("/folders").json()[0]["name"] == "Private"


def test_folders_need_a_login(client_factory):
    client = client_factory()
    assert client.get("/folders").status_code == 401
    assert client.post("/folders", json={"name": "x"}).status_code == 401


@pytest.mark.parametrize("n", [51])
def test_the_number_of_folders_is_capped(api, n):
    for i in range(50):
        assert api.post("/folders", json={"name": f"F{i}"}).status_code == 201
    assert api.post("/folders", json={"name": "one too many"}).status_code == 422
