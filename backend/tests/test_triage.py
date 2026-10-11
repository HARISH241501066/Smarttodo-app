# ruff: noqa: E501  (long literals in fixtures and expected strings)
"""Module 11b: sorting email into task / update / ignore / review."""

import base64
import http.client
import json
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import func, inspect, select, text

from app.filters.categorize import categorize
from app.filters.deadline import extract_deadline, priority_for
from app.filters.links import pick_action_link
from app.filters.pipeline import make_filter_sink
from app.filters.reprocess import reprocess_review
from app.filters.task_stage import make_triage_stage
from app.filters.triage import classify
from app.filters.urls import unwrap_tracking_url
from app.ingest.mime import Link, ParsedMessage, parse_message
from app.ingest.sync import run_sync
from app.models import EmailUpdate, ProcessedMessage, Quarantine, Reminder, Task, User, UserSettings
from tests.gmail_fakes import FakeGmailApi

FIXTURES = Path(__file__).parent / "fixtures" / "triage_emails"
NOW = datetime(2026, 1, 1, 6, 0, tzinfo=UTC)
TRUSTED = "mx.google.com; dkim=pass; spf=pass; dmarc=pass"


def manifest() -> dict[str, dict]:
    return json.loads((FIXTURES / "expected.manifest").read_text())


def raw_of(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def parsed_of(name: str) -> ParsedMessage:
    return parse_message(raw_of(name))


def raw_mail(message_id, subject, body, sender="Ravi <ravi@gmail.com>", auth=TRUSTED):
    data = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    headers = [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}]
    if auth:
        headers.append({"name": "Authentication-Results", "value": auth})
    return {
        "id": message_id,
        "threadId": f"t-{message_id}",
        "labelIds": ["INBOX"],
        "internalDate": "1767225600000",
        "payload": {
            "mimeType": "text/plain",
            "headers": headers,
            "body": {"size": len(body), "data": data},
        },
    }


@pytest.fixture
def user(db_session):
    u = User(email="me@example.test")
    db_session.add(u)
    db_session.flush()
    db_session.add(UserSettings(user_id=u.id, denylist=[], allowlist=["college.example.edu"]))
    db_session.commit()
    return u


def sync(session, user, messages):
    session.execute(text("UPDATE sync_state SET history_id = NULL"))
    session.commit()
    sink = make_filter_sink(session, make_triage_stage(session, lambda: NOW))
    return run_sync(user, session, FakeGmailApi(messages), sink)


def outcome(session, message_id: str) -> str:
    """What the pipeline did with a message: task, review, update, ignore or quarantined."""
    session.expire_all()
    decision = session.scalar(
        select(ProcessedMessage.decision).where(ProcessedMessage.message_id == message_id)
    )
    if decision == "ignored":
        return "ignore"
    if decision == "quarantined":
        return "quarantined"
    if session.scalar(select(EmailUpdate.id).where(EmailUpdate.message_id == message_id)):
        return "update"
    task = session.scalar(select(Task).where(Task.source_ref == message_id))
    if task is None:
        return f"nothing (decision={decision})"
    return "review" if task.status == "needs_review" else "task"


# ====================================================================== the brief's fixtures


def test_every_fixture_is_sorted_as_expected(db_session, user):
    names = manifest()
    raws = {meta["id"]: raw_of(name) for name, meta in names.items()}
    result = sync(db_session, user, raws)
    assert result.failed == 0

    wrong = {}
    for name, meta in names.items():
        got = outcome(db_session, meta["id"])
        if got != meta["kind"]:
            wrong[name] = f"expected {meta['kind']}, got {got}"
    assert not wrong, wrong

    kinds = [m["kind"] for m in names.values()]
    assert {"task", "update", "ignore", "review"} <= set(kinds) and len(names) >= 15
    assert result.decisions["ignored"] == kinds.count("ignore")


def test_the_brief_examples_are_covered_by_name():
    names = " ".join(manifest())
    for needle in (
        "assignment_is_live",
        "quiz_summary_score",
        "secure_signin_link",
        "otp",
        "payment_receipt",
        "lecture_uploaded",
        "two_conflicting_deadlines",
        "digii_new_quiz_table",
    ):
        assert needle in names


def test_a_task_email_with_no_deadline_becomes_an_open_task_not_review(db_session, user):
    name = "01_task_assignment_is_live"
    sync(db_session, user, {"tri01": raw_of(name)})
    task = db_session.scalar(select(Task).where(Task.source_ref == "tri01"))
    assert (task.status, task.deadline, task.source) == ("open", None, "nptel")
    assert task.priority == "important"
    assert task.link == "https://onlinecourses.nptel.ac.in/noc26_cs10/assignment/9"
    assert task.sender_email == "noreply@nptel.ac.in"
    assert (
        db_session.scalar(
            select(func.count()).select_from(Task).where(Task.status == "needs_review")
        )
        == 0
    )


def test_ignored_mail_stores_nothing_but_the_fact_it_was_handled(db_session, user):
    sync(
        db_session,
        user,
        {"tri04": raw_of("04_ignore_otp"), "tri05": raw_of("05_ignore_payment_receipt")},
    )
    rows = db_session.execute(
        select(ProcessedMessage.message_id, ProcessedMessage.decision, ProcessedMessage.reason)
    ).all()
    assert {(r.message_id, r.decision) for r in rows} == {
        ("tri04", "ignored"),
        ("tri05", "ignored"),
    }
    assert all(r.reason.startswith("ignore:") for r in rows)
    assert db_session.scalar(select(func.count()).select_from(Task)) == 0
    assert db_session.scalar(select(func.count()).select_from(EmailUpdate)) == 0


def test_an_update_keeps_title_source_link_and_date_but_no_body(db_session, user, engine):
    sync(db_session, user, {"tri02": raw_of("02_update_quiz_summary_score")})
    row = db_session.scalar(select(EmailUpdate))
    assert (row.title, row.source, row.message_id) == ("Quiz summary / score", "nptel", "tri02")
    assert row.received_at == datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
    columns = {c["name"] for c in inspect(engine).get_columns("updates")}
    assert columns == {
        "id",
        "user_id",
        "message_id",
        "title",
        "source",
        "link",
        "link_type",
        "received_at",
        "created_at",
    }
    stored = " ".join(str(v) for v in db_session.execute(text("SELECT * FROM updates")).one())
    assert "scored 8 out of 10" not in stored


def test_scam_filtering_still_comes_first(db_session, user):
    scam = raw_mail(
        "q1", "Verification code needed",
        "Your OTP is 123456. Pay a registration fee of Rs. 500 via UPI immediately to unlock.",
        sender="NPTEL Team <nptel.help@gmail.com>", auth="mx.google.com; spf=fail; dmarc=fail",
    )  # fmt: skip
    result = sync(db_session, user, {"q1": scam})
    assert result.decisions == {"quarantined": 1}
    assert db_session.scalar(select(func.count()).select_from(Quarantine)) == 1
    assert outcome(db_session, "q1") == "quarantined"


def test_sorting_an_email_twice_is_harmless(db_session, user):
    raws = {m["id"]: raw_of(n) for n, m in manifest().items()}
    sync(db_session, user, raws)
    before = (
        db_session.scalar(select(func.count()).select_from(Task)),
        db_session.scalar(select(func.count()).select_from(EmailUpdate)),
    )
    again = sync(db_session, user, raws)
    assert again.decisions == {"skipped": len(raws)}
    after = (
        db_session.scalar(select(func.count()).select_from(Task)),
        db_session.scalar(select(func.count()).select_from(EmailUpdate)),
    )
    assert before == after


# ====================================================================== the rules themselves


def m(subject, body="", sender="NPTEL <noreply@nptel.ac.in>"):
    return ParsedMessage(
        message_id="x", thread_id="t", labels=(), received_at=None, sender_email="",
        sender=sender, subject=subject, body_text=body, has_attachments=False,
    )  # fmt: skip


@pytest.mark.parametrize(
    "subject,body,kind",
    [
        # ignore
        ("Your verification code", "", "ignore"),
        ("Your OTP for login", "", "ignore"),
        ("Login code: 4821", "", "ignore"),
        ("Your secure sign-in link", "", "ignore"),
        ("Sign in to your account", "Click the link below to sign in.", "ignore"),
        ("Reset your password", "", "ignore"),
        ("Password reset request", "", "ignore"),
        ("Security alert for your account", "", "ignore"),
        ("New sign-in from Chrome on Windows", "", "ignore"),
        ("Payment receipt", "", "ignore"),
        ("Receipt for your payment", "", "ignore"),
        ("Order #1234 confirmed", "", "ignore"),
        ("Your order has shipped", "", "ignore"),
        ("Thank you for your submission", "", "ignore"),
        ("Please verify your email", "", "ignore"),
        ("Hi", "Your one-time password is 884210. Do not share.", "ignore"),
        ("Hi", "482913 is your verification code for the portal.", "ignore"),
        # update
        ("Quiz summary", "", "update"),
        ("Your score", "You scored 9/10", "update"),
        ("Results declared", "", "update"),
        ("Your grade for Week 4", "", "update"),
        ("Certificate issued", "Your certificate is ready to download.", "update"),
        ("Week 3 lecture uploaded", "", "update"),
        ("Course content is now live", "", "update"),
        ("Announcement", "The library will open late on Monday.", "update"),
        ("Hello", "Hope you are well.", "update"),
        ("For your information", "Nothing needed from you.", "update"),
        # task
        ("Assignment 4 is live", "", "task"),
        ("Assignment released", "", "task"),
        ("New assignment published", "", "task"),
        ("New quiz published", "", "task"),
        ("Attempt the quiz", "", "task"),
        ("Take quiz now", "", "task"),
        ("Quiz is open", "", "task"),
        ("Quiz 3 closes soon", "", "task"),
        ("Registration closes on 20 Jan", "", "task"),
        ("Last date to apply", "", "task"),
        ("Submit your project by Friday", "", "task"),
        ("Deadline extended", "", "task"),
        ("Fill the form", "", "task"),
        ("Action required", "", "task"),
        ("Please register for the workshop", "", "task"),
        ("Assignment published", "", "task"),
        # review: strong cues on both sides
        ("Quiz score published, Assignment 5 is due", "", "review"),
        ("Results out; submit your form by 20 Jan", "", "review"),
    ],
)
def test_the_rules(subject, body, kind):
    assert classify(m(subject, body)).kind == kind, classify(m(subject, body))


def test_content_live_with_an_assignment_is_a_task_without_one_it_is_an_update():
    assert classify(m("Week 9 content & Assignment is live")).kind == "task"
    assert classify(m("Week 9 content is live")).kind == "update"
    assert classify(m("Week 9 lecture uploaded")).kind == "update"


def test_result_wording_does_not_turn_a_score_into_a_task():
    for subject in (
        "Quiz 3 score published",
        "Assignment 4 results published",
        "Quiz results are out",
    ):
        assert classify(m(subject)).kind == "update", subject


def test_ignore_beats_every_other_signal_and_the_reason_says_why():
    t = classify(m("Your verification code for the assignment portal", "Assignment is due"))
    assert t.kind == "ignore" and t.reason == "ignore: one-time code"


def test_a_body_that_merely_mentions_a_receipt_is_not_ignored():
    assert classify(m("Assignment 2 is live", "Keep your payment receipt handy.")).kind == "task"


def test_a_conflict_is_review_not_a_guess():
    t = classify(m("Quiz score published", "Assignment 5 is due on 20 Jan."))
    assert t.kind == "review" and "vs" in t.reason


# ====================================================================== tracked senders


def test_a_tracked_senders_otp_email_is_still_ignored(db_session, user):
    row = db_session.get(UserSettings, user.id)
    row.always_track = ["portal.example.test"]
    db_session.commit()
    portal = "Portal <noreply@portal.example.test>"
    otp = raw_mail("t1", "Your login", "Your OTP is 482913.", sender=portal)
    receipt = raw_mail("t2", "Payment receipt", "Thanks.", sender=portal)
    plain = raw_mail(
        "t3", "Monthly statement", "Nothing to do.", sender=portal
    )  # tracked: always a task

    sync(db_session, user, {"t1": otp, "t2": receipt, "t3": plain})

    assert outcome(db_session, "t1") == "ignore" and outcome(db_session, "t2") == "ignore"
    assert outcome(db_session, "t3") == "task"
    task = db_session.scalar(select(Task).where(Task.source_ref == "t3"))
    assert (task.source, task.status, task.priority, task.deadline) == (
        "tracked",
        "open",
        "important",
        None,
    )


def test_a_tracked_sender_that_fails_authentication_gets_no_special_treatment(db_session, user):
    row = db_session.get(UserSettings, user.id)
    row.always_track = ["portal.example.test"]
    db_session.commit()
    spoof = raw_mail(
        "t4",
        "Monthly statement",
        "Nothing to do.",
        sender="Portal <noreply@portal.example.test>",
        auth="mx.google.com; spf=fail; dmarc=fail",
    )
    sync(db_session, user, {"t4": spoof})
    assert outcome(db_session, "t4") == "update"  # an ordinary email without action cues


# ====================================================================== priorities


@pytest.mark.parametrize(
    "category,expected",
    [
        ("nptel", "important"),
        ("internship", "important"),
        ("gform", "important"),
        ("tracked", "important"),
        ("college", "regular"),
        ("unknown", "regular"),
    ],
)
def test_priority_without_a_deadline(category, expected):
    assert priority_for(category, None, NOW) == expected


def test_priorities_through_the_pipeline(db_session, user):
    friend = raw_mail("p1", "Please submit the lab record", "Hi.")  # unknown source
    nptel = raw_mail("p2", "Assignment 7 is live", "Open it.", sender="NPTEL <noreply@nptel.ac.in>")
    form = raw_mail(
        "p3",
        "Please fill this survey",
        "https://forms.gle/Survey1",
        sender="Prof <prof@elsewhere.test>",
    )
    sync(db_session, user, {"p1": friend, "p2": nptel, "p3": form})
    got = {
        t.source_ref: (t.source, t.priority, t.status, t.deadline)
        for t in db_session.scalars(select(Task))
    }
    assert got == {
        "p1": ("other", "regular", "open", None),
        "p2": ("nptel", "important", "open", None),
        "p3": ("gform", "important", "open", None),
    }


# ====================================================================== wrapped links


WRAPPED = "https://url1234.mail.example.com/ls/click?upn=abc&url=https%3A%2F%2Fonlinecourses.nptel.ac.in%2Fnoc%2Fquiz%2F5%3Fa%3D1%26b%3D2&data=zzz"
REAL = "https://onlinecourses.nptel.ac.in/noc/quiz/5?a=1&b=2"


@pytest.mark.parametrize(
    "param",
    [
        "url",
        "u",
        "uri",
        "redirect",
        "redirect_url",
        "redirect_uri",
        "target",
        "dest",
        "destination",
        "link",
    ],
)
def test_unwrapping_reads_the_common_destination_parameters(param):
    wrapped = (
        f"https://track.mailer.example/click?id=7&{param}=https%3A%2F%2Fexample.org%2Ftask%3Fx%3D1"
    )
    assert unwrap_tracking_url(wrapped) == "https://example.org/task?x=1"


def test_unwrapping_edge_cases():
    assert unwrap_tracking_url(WRAPPED) == REAL
    # double-encoded, and nested wrappers
    double = "https://t.example/c?url=https%253A%252F%252Fexample.org%252Fgo"
    assert unwrap_tracking_url(double) == "https://example.org/go"
    inner = "https://t2.example/c?target=https%3A%2F%2Fexample.org%2Fgo"
    outer = f"https://t1.example/c?redirect={inner.replace(':', '%3A').replace('/', '%2F').replace('?', '%3F').replace('=', '%3D')}"
    assert unwrap_tracking_url(outer) == "https://example.org/go"
    # not links to follow: opaque tokens, non-http destinations, plain pages
    assert (
        unwrap_tracking_url("https://t.example/c?upn=AAAABBBB")
        == "https://t.example/c?upn=AAAABBBB"
    )
    assert (
        unwrap_tracking_url("https://t.example/c?url=javascript%3Aalert(1)")
        == "https://t.example/c?url=javascript%3Aalert(1)"
    )
    assert unwrap_tracking_url("https://t.example/c?u=12345") == "https://t.example/c?u=12345"
    assert unwrap_tracking_url("https://example.org/page") == "https://example.org/page"
    assert (
        unwrap_tracking_url("https://example.org/page?x=1&y=2")
        == "https://example.org/page?x=1&y=2"
    )


def message_with_links(*links, sender="NPTEL <noreply@nptel.ac.in>"):
    return ParsedMessage(
        message_id="w1", thread_id="t", labels=(), received_at=NOW, sender_email="", sender=sender,
        subject="Quiz", body_text="Take the quiz.", has_attachments=False, links=tuple(links),
    )  # fmt: skip


def test_a_wrapped_link_resolves_to_the_real_domain_and_no_http_request_is_made(
    monkeypatch, db_session, user
):
    calls = []

    def forbid(*args, **kwargs):
        calls.append(args)
        raise AssertionError("network access attempted")

    monkeypatch.setattr(urllib.request, "urlopen", forbid)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", forbid)
    monkeypatch.setattr(http.client.HTTPSConnection, "connect", forbid)

    message = message_with_links(
        Link(WRAPPED, "Take Quiz"), Link("https://nptel.ac.in/unsubscribe?u=1", "Unsubscribe")
    )
    with respx.mock(assert_all_called=False) as router:
        router.route().mock(return_value=httpx.Response(200))
        choice = pick_action_link(message)
        raw = raw_mail(
            "w2",
            "Quiz is open",
            f"Attempt the quiz: {WRAPPED}",
            sender="NPTEL <noreply@nptel.ac.in>",
        )
        sync(db_session, user, {"w2": raw})
        assert len(router.calls) == 0

    assert (choice.url, choice.link_type) == (REAL, "action")
    task = db_session.scalar(select(Task).where(Task.source_ref == "w2"))
    assert task.link == REAL and task.link_type == "action"
    assert calls == []


def test_the_real_destination_is_what_gets_scored_not_the_tracker_host():
    # the tracker host has nothing to do with the sender; the destination is the sender's own site
    wrapped_other = "https://clicks.sendmail.example/c?url=https%3A%2F%2Fnews.example%2Farticle"
    message = message_with_links(Link(wrapped_other, "Read more"), Link(WRAPPED, "Take Quiz"))
    assert pick_action_link(message).url == REAL


def test_social_and_unsubscribe_destinations_behind_a_wrapper_are_still_dropped():
    wrapped_unsub = "https://t.example/c?url=https%3A%2F%2Fnptel.ac.in%2Funsubscribe%3Fu%3D1"
    wrapped_social = "https://t.example/c?url=https%3A%2F%2Ftwitter.com%2Fnptelindia"
    message = message_with_links(Link(wrapped_unsub, "Click here"), Link(wrapped_social, "Follow"))
    assert pick_action_link(message).link_type == "gmail"


def test_a_google_form_behind_a_wrapper_is_still_a_form():
    wrapped = "https://t.example/c?url=https%3A%2F%2Fforms.gle%2FAbc123"
    message = message_with_links(Link(wrapped, "Open form"), sender="Prof <prof@elsewhere.test>")
    assert categorize(message, []) == "gform"
    assert pick_action_link(message).url == "https://forms.gle/Abc123"


# ====================================================================== deadline text layouts


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Closes on : 18 Oct 2026 | 11:59 PM", "2026-10-18 23:59"),
        ("Closes on: 18 Oct 2026 | 11:59 PM IST", "2026-10-18 23:59"),
        ("Closes on : 18 Oct 2026 | 9 AM", "2026-10-18 09:00"),
        ("Closes on : 18 Oct 2026|11:59 PM", "2026-10-18 23:59"),
        ("Closes on : 18 Oct 2026 | 18:30", "2026-10-18 18:30"),
        (
            "Closes on\n: 18 Oct 2026 | 11:59 PM",
            "2026-10-18 23:59",
        ),  # label and value on separate lines
        ("Closes on :\n18 Oct 2026 | 11:59 PM", "2026-10-18 23:59"),
        ("Last date : 18 Oct 2026", "2026-10-18 23:59"),
        ("Deadline | 18 Oct 2026 | 11:59 PM", "2026-10-18 23:59"),
    ],
)
def test_label_value_and_pipe_layouts(text, expected):
    ist = extract_deadline(text, NOW, NOW).deadline.astimezone(
        __import__("zoneinfo").ZoneInfo("Asia/Kolkata")
    )
    assert ist.strftime("%Y-%m-%d %H:%M") == expected


def test_a_pipe_elsewhere_is_left_alone():
    result = extract_deadline("Home | Courses | Closes on : 18 Oct 2026", NOW, NOW)
    assert result.candidates == 1


def test_the_digii_style_html_table_email(db_session, user):
    raw = raw_of("08_task_digii_new_quiz_table")
    parsed = parse_message(raw)
    assert (
        "Closes on : 18 Oct 2026 | 11:59 PM" in parsed.body_text
    )  # the table text really reads like this
    sync(db_session, user, {"tri08": raw})
    task = db_session.scalar(select(Task).where(Task.source_ref == "tri08"))
    assert task.status == "open"
    assert task.deadline.astimezone(UTC) == datetime(2026, 10, 18, 18, 29, tzinfo=UTC)  # 23:59 IST
    assert (task.link, task.link_type) == (
        "https://app.digii.example/student/quiz/start?quizId=9",
        "action",
    )
    assert task.title == "New Quiz Published: Week 9 Quiz"


def test_two_different_deadlines_far_apart_go_to_review_but_the_same_one_twice_does_not(
    db_session, user
):
    twice = raw_mail(
        "d1", "Assignment 4", "Last date to submit: 12 Jan 2026.\nDeadline: 12 Jan 2026 (again)."
    )
    two = raw_mail(
        "d2",
        "Assignment 6",
        "Last date to submit: 12 Jan 2026.\nLate registration ends on 28 Feb 2026.",
    )
    close = raw_mail(
        "d3", "Assignment 8", "Closes on 12 Jan 2026 11:59 PM.\nSubmit by 12 Jan 2026 11:00 PM."
    )
    sync(db_session, user, {"d1": twice, "d2": two, "d3": close})
    assert (outcome(db_session, "d1"), outcome(db_session, "d2"), outcome(db_session, "d3")) == (
        "task",
        "review",
        "task",
    )


# ====================================================================== re-sorting the review queue


def review_task(session, user, message_id, title="Old review item", source="other", **kw):
    link = f"https://mail.google.com/mail/u/0/#all/{message_id}" if message_id else None
    task = Task(
        user_id=user.id, title=title, status="needs_review", source=source, source_ref=message_id,
        link=link, link_type="gmail" if link else None, **kw,
    )  # fmt: skip
    session.add(task)
    session.commit()
    return task


def seed_review_queue(session, user):
    """Review items as the old rules left them, with the emails still in Gmail."""
    ids = {
        "to_task": "tri01",  # assignment is live, no deadline
        "to_update": "tri02",  # quiz summary
        "to_ignore": "tri04",  # OTP
        "stays": "tri07",  # two conflicting deadlines
        "digii": "tri08",
    }
    for key, mid in ids.items():
        review_task(session, user, mid, title=f"item {key}")
    review_task(session, user, "gone-from-gmail", title="item gone")
    review_task(session, user, None, title="item by hand", source="manual")
    raws = {meta["id"]: raw_of(name) for name, meta in manifest().items()}
    return ids, FakeGmailApi(raws)


def snapshot(session):
    session.expire_all()
    return (
        sorted((t.id, t.status, t.source, t.priority) for t in session.scalars(select(Task))),
        sorted(u.message_id for u in session.scalars(select(EmailUpdate))),
        sorted((p.message_id, p.decision) for p in session.scalars(select(ProcessedMessage))),
        session.scalar(select(func.count()).select_from(Reminder)),
    )


def test_reprocess_moves_each_review_item_to_the_right_place(db_session, user):
    ids, gmail = seed_review_queue(db_session, user)

    result = reprocess_review(db_session, user, gmail, now=NOW)

    assert (
        result.total,
        result.task,
        result.update,
        result.ignore,
        result.review,
        result.gone,
    ) == (7, 2, 1, 1, 2, 1)
    assert outcome(db_session, ids["to_task"]) == "task"
    assert outcome(db_session, ids["digii"]) == "task"
    assert outcome(db_session, ids["to_update"]) == "update"
    assert outcome(db_session, ids["to_ignore"]) == "ignore"
    assert outcome(db_session, ids["stays"]) == "review"
    still_waiting = {
        t.source_ref for t in db_session.scalars(select(Task).where(Task.status == "needs_review"))
    }
    assert still_waiting == {
        "tri07",
        "gone-from-gmail",
        None,
    }  # conflict, deleted-in-Gmail, entered by hand

    task = db_session.scalar(select(Task).where(Task.source_ref == "tri01"))
    assert (task.status, task.source, task.deadline, task.sender_email) == (
        "open",
        "nptel",
        None,
        "noreply@nptel.ac.in",
    )
    digii = db_session.scalar(select(Task).where(Task.source_ref == "tri08"))
    assert digii.deadline.astimezone(UTC) == datetime(2026, 10, 18, 18, 29, tzinfo=UTC)
    assert digii.link == "https://app.digii.example/student/quiz/start?quizId=9"
    update = db_session.scalar(select(EmailUpdate))
    assert (update.title, update.message_id) == ("item to_update", "tri02")
    assert (
        db_session.scalar(select(Task).where(Task.source_ref == "tri04")) is None
    )  # the OTP item is gone


def test_reprocess_is_idempotent(db_session, user):
    _, gmail = seed_review_queue(db_session, user)
    reprocess_review(db_session, user, gmail, now=NOW)
    first = snapshot(db_session)

    again = reprocess_review(db_session, user, gmail, now=NOW)

    assert (again.task, again.update, again.ignore) == (0, 0, 0)
    assert again.review + again.gone == again.total == 3  # the same three are still waiting
    assert snapshot(db_session) == first
    third = reprocess_review(db_session, user, gmail, now=NOW)
    assert (third.task, third.update, third.ignore) == (0, 0, 0)
    assert snapshot(db_session) == first


def test_reprocess_does_not_flood_the_user_with_new_task_notifications(db_session, user):
    _, gmail = seed_review_queue(db_session, user)
    reprocess_review(db_session, user, gmail, now=NOW)
    kinds = set(db_session.scalars(select(Reminder.kind)))
    assert "created" not in kinds  # moved items are not "new tasks"
    # the one task with a real deadline gets its future countdown reminders, nothing is due now
    assert kinds <= {"t48", "t24", "t10", "t1"}
    assert (
        db_session.scalar(select(func.count()).select_from(Reminder).where(Reminder.fire_at <= NOW))
        == 0
    )


def test_reprocess_works_in_batches_with_a_cursor(db_session, user):
    _, gmail = seed_review_queue(db_session, user)
    totals = dict(task=0, update=0, ignore=0, review=0, gone=0)
    cursor, calls = 0, 0
    while True:
        batch = reprocess_review(db_session, user, gmail, now=NOW, after_id=cursor, limit=3)
        calls += 1
        for key in totals:
            totals[key] += getattr(batch, key)
        if batch.next_after_id is None:
            break
        cursor = batch.next_after_id
    assert calls == 3
    assert totals == dict(task=2, update=1, ignore=1, review=2, gone=1)


def test_reprocess_leaves_other_users_and_other_statuses_alone(db_session, user):
    other = User(email="other@example.test")
    db_session.add(other)
    db_session.commit()
    review_task(db_session, other, "tri02", title="not mine")
    db_session.add(
        Task(
            user_id=user.id, title="already open", status="open", source="other", source_ref="tri02"
        )
    )
    db_session.commit()
    result = reprocess_review(
        db_session, user, FakeGmailApi({"tri02": raw_of("02_update_quiz_summary_score")}), now=NOW
    )
    assert result.total == 0
    assert db_session.scalar(select(Task.status).where(Task.title == "not mine")) == "needs_review"


def test_reprocess_moves_a_tracked_senders_review_item_to_tasks(db_session, user):
    row = db_session.get(UserSettings, user.id)
    row.always_track = ["portal.example.test"]
    db_session.commit()
    review_task(db_session, user, "tk1", title="Statement")
    mail = raw_mail(
        "tk1", "Monthly statement", "Nothing to do.", sender="Portal <n@portal.example.test>"
    )
    result = reprocess_review(db_session, user, FakeGmailApi({"tk1": mail}), now=NOW)
    assert result.task == 1
    task = db_session.scalar(select(Task).where(Task.source_ref == "tk1"))
    assert (task.status, task.source, task.priority) == ("open", "tracked", "important")


def test_a_rate_limited_reprocess_keeps_the_progress_it_made(db_session, user):
    from app.ingest.gmail import RateLimited

    ids, gmail = seed_review_queue(db_session, user)
    gmail.get_errors["tri04"] = RateLimited("slow down")  # fails part way through
    with pytest.raises(RateLimited):
        reprocess_review(db_session, user, gmail, now=NOW)
    assert outcome(db_session, ids["to_task"]) == "task"  # already committed before the failure
    gmail.get_errors.clear()
    reprocess_review(db_session, user, gmail, now=NOW)
    assert outcome(db_session, ids["to_ignore"]) == "ignore"
