"""Module 5 task stage: task creation, extensions, idempotency, and no network on email links."""

import http.client
import json
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import func, select, text

from app.filters.pipeline import make_filter_sink
from app.ingest.mime import parse_message
from app.ingest.sync import run_sync
from app.models import Task, User, UserSettings
from tests.gmail_fakes import FakeGmailApi

FIXTURES = Path(__file__).parent / "fixtures" / "task_emails"
DMARC = "mx.google.com; dmarc=pass header.from=x"


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
                {"name": "College", "type": "custom", "senders": ["college.example.edu"]},
            ],
        )
    )
    db_session.commit()
    return u


def fixture_messages() -> dict[str, dict]:
    out = {}
    for path in sorted(FIXTURES.glob("*.json")):
        raw = json.loads(path.read_text())
        raw["payload"]["headers"].append({"name": "Authentication-Results", "value": DMARC})
        out[raw["id"]] = raw
    return out


def clock(year=2026, month=1, day=1, hour=6):
    return lambda: datetime(year, month, day, hour, tzinfo=UTC)


def sync(session, user, messages, now=None):
    """Each call syncs exactly `messages`, in the given order."""
    sink = make_filter_sink(session, now or clock())
    return run_sync(user, session, FakeGmailApi(messages), sink)


def tasks(session, user) -> dict[str, Task]:
    return {t.source_ref: t for t in session.scalars(select(Task).where(Task.user_id == user.id))}


def raw_mail(message_id, subject, body, received_ms, sender="NPTEL <noreply@nptel.ac.in>"):
    import base64

    data = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return {
        "id": message_id,
        "threadId": f"t-{message_id}",
        "labelIds": ["INBOX"],
        "internalDate": str(received_ms),
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
                {"name": "Authentication-Results", "value": DMARC},
            ],
            "body": {"size": len(body), "data": data},
        },
    }


T0 = 1_767_225_600_000  # 2026-01-01T00:00Z
HOUR = 3_600_000


# ---------- creation ----------


def test_quiz_fixture_becomes_a_task_with_expected_fields(db_session, user):
    result = sync(db_session, user, {k: v for k, v in fixture_messages().items() if k == "task01"})
    quiz = tasks(db_session, user)["task01"]

    assert result.failed == 0
    assert quiz.title == "NPTEL: Week 5 quiz is live"
    assert (quiz.source, quiz.source_name, quiz.link_type, quiz.status) == (
        "nptel",
        "NPTEL",
        "action",
        "open",
    )
    assert quiz.user_confirmed is False
    assert quiz.link.endswith("unit?unit=5&assessment=20")
    assert quiz.confidence == 0.9
    assert quiz.deadline.astimezone(UTC).isoformat() == "2026-01-22T18:29:00+00:00"
    assert quiz.priority == "important"
    assert quiz.notes is None  # the body is never stored


def test_a_source_mail_without_a_date_is_an_open_task_with_the_gmail_fallback(db_session, user):
    mail = raw_mail("n0", "Assignment 3", "The assignment is live. Details soon.", T0)
    sync(db_session, user, {"n0": mail})
    task = tasks(db_session, user)["n0"]
    assert (task.status, task.deadline, task.confidence) == ("open", None, None)
    assert (task.link_type, task.link) == ("gmail", "https://mail.google.com/mail/u/0/#all/n0")
    assert task.priority == "important"  # from a source the user chose


def test_several_different_deadlines_go_to_review_with_the_earliest_filled_in(db_session, user):
    body = "Last date: 12 Jan 2026.\nLast date with late fee: 20 Jan 2026."
    mail = raw_mail("rv1", "Registration open", body, T0)
    sync(db_session, user, {"rv1": mail})
    task = tasks(db_session, user)["rv1"]
    # 12 Jan and 20 Jan are ambiguous, so a person confirms; the earliest is pre-filled
    assert (task.status, task.confidence) == ("needs_review", 0.6)
    assert task.deadline.astimezone(UTC).day == 12


def test_deadline_within_48_hours_is_urgent(db_session, user):
    mail = raw_mail("u1", "Lab record", "Last date: 2 Jan 2026.", T0 + HOUR)
    sync(db_session, user, {"u1": mail}, clock(hour=7))
    assert tasks(db_session, user)["u1"].priority == "urgent"


def test_keyword_mail_from_an_unknown_authenticated_sender_becomes_a_keyword_task(db_session, user):
    mail = raw_mail(
        "o1",
        "Hackathon registration",
        "Deadline: 20 Jan 2026.",
        T0,
        sender="Ravi <ravi@acme.example>",
    )
    sync(db_session, user, {"o1": mail})
    task = tasks(db_session, user)["o1"]
    assert (task.source, task.source_name) == ("keyword", "Hackathon")


def test_unknown_senders_without_a_keyword_are_dropped(db_session, user):
    mail = raw_mail("o2", "Hello", "Deadline: 20 Jan 2026.", T0, sender="Ravi <ravi@gmail.com>")
    result = sync(db_session, user, {"o2": mail})
    assert tasks(db_session, user) == {} and result.decisions == {"dropped": 1}


def test_processing_a_message_twice_creates_one_task(db_session, user):
    mail = {"d1": raw_mail("d1", "Assignment 3", "Last date: 20 Jan 2026.", T0)}
    sync(db_session, user, mail)
    db_session.execute(text("DELETE FROM processed_messages"))  # force a reprocess
    db_session.commit()
    sync(db_session, user, mail)
    assert db_session.scalar(select(func.count()).select_from(Task)) == 1


# ---------- extensions ----------


def test_extension_email_updates_the_existing_task_without_adding_one(db_session, user):
    original = raw_mail("e1", "NPTEL: Assignment 3 reminder", "Last date: 20 Jan 2026.", T0)
    extension = raw_mail(
        "e2", "Assignment 3 deadline extended", "The deadline is extended till 25 Jan 2026.",
        T0 + 24 * HOUR,
    )  # fmt: skip
    sync(db_session, user, {"e1": original})
    before = db_session.scalar(select(func.count()).select_from(Task))
    old_deadline = tasks(db_session, user)["e1"].deadline

    sync(db_session, user, {"e2": extension})

    assert before == 1
    assert db_session.scalar(select(func.count()).select_from(Task)) == 1
    task = tasks(db_session, user)["e1"]
    assert task.deadline > old_deadline
    assert task.deadline.astimezone(UTC).isoformat() == "2026-01-25T18:29:00+00:00"
    assert "e2" not in tasks(db_session, user)


def test_extension_also_updates_the_link_when_it_has_a_better_one(db_session, user):
    original = raw_mail("l1", "Assignment 3", "Last date: 20 Jan 2026.", T0)
    extension = raw_mail(
        "l2", "Assignment 3 extended", "Last date: 25 Jan 2026.\nhttps://onlinecourses.nptel.ac.in/c/assignment/3",
        T0 + HOUR,
    )  # fmt: skip
    sync(db_session, user, {"l1": original})
    assert tasks(db_session, user)["l1"].link_type == "gmail"
    sync(db_session, user, {"l2": extension})
    task = tasks(db_session, user)["l1"]
    assert (task.link_type, task.link) == (
        "action",
        "https://onlinecourses.nptel.ac.in/c/assignment/3",
    )


def test_an_older_email_processed_late_does_not_overwrite_a_newer_extension(db_session, user):
    original = raw_mail("n1", "Assignment 3 reminder", "Last date: 20 Jan 2026.", T0)
    extension = raw_mail(
        "n2", "Assignment 3 deadline extended", "Extended till 25 Jan 2026.", T0 + 48 * HOUR
    )
    sync(db_session, user, {"n2": extension})  # newest arrives first, as in a backfill
    sync(db_session, user, {"n1": original})
    task = tasks(db_session, user)["n2"]
    assert task.deadline.astimezone(UTC).isoformat() == "2026-01-25T18:29:00+00:00"
    assert db_session.scalar(select(func.count()).select_from(Task)) == 1


def test_different_numbered_notices_stay_separate_tasks(db_session, user):
    a = raw_mail("s1", "NPTEL: Assignment 3 reminder", "Last date: 20 Jan 2026.", T0)
    b = raw_mail("s2", "NPTEL: Assignment 4 reminder", "Last date: 27 Jan 2026.", T0 + HOUR)
    sync(db_session, user, {"s1": a, "s2": b})
    assert db_session.scalar(select(func.count()).select_from(Task)) == 2


def test_same_subject_in_a_different_source_is_not_merged(db_session, user):
    a = raw_mail("x1", "Registration open", "Last date: 20 Jan 2026.", T0)
    b = raw_mail(
        "x2",
        "Registration open",
        "Last date: 22 Jan 2026.",
        T0 + HOUR,
        sender="Dept <office@college.example.edu>",
    )
    sync(db_session, user, {"x1": a, "x2": b})
    assert db_session.scalar(select(func.count()).select_from(Task)) == 2


def test_done_tasks_are_not_reopened_by_a_similar_email(db_session, user):
    sync(db_session, user, {"c1": raw_mail("c1", "Assignment 3", "Last date: 20 Jan 2026.", T0)})
    tasks(db_session, user)["c1"].status = "done"
    db_session.commit()
    sync(
        db_session,
        user,
        {"c2": raw_mail("c2", "Assignment 3", "Last date: 25 Jan 2026.", T0 + HOUR)},
    )
    assert db_session.scalar(select(func.count()).select_from(Task)) == 2
    assert tasks(db_session, user)["c1"].status == "done"


def test_an_extension_gives_a_deadline_to_an_earlier_task_that_had_none(db_session, user):
    sync(
        db_session,
        user,
        {"r1": raw_mail("r1", "Assignment 3", "The assignment is live. Details soon.", T0)},
    )
    first = tasks(db_session, user)["r1"]
    assert (first.status, first.deadline) == ("open", None)  # no deadline is not a reason to wait
    sync(
        db_session,
        user,
        {"r2": raw_mail("r2", "Assignment 3 update", "Last date: 25 Jan 2026.", T0 + HOUR)},
    )
    task = tasks(db_session, user)["r1"]
    assert (task.status, task.confidence) == ("open", 0.9)
    assert task.deadline is not None and len(tasks(db_session, user)) == 1


# ---------- no network access for email links ----------


def test_no_http_request_is_made_to_any_email_link(db_session, user, monkeypatch):
    attempts = []

    def forbid(*args, **kwargs):
        attempts.append(args)
        raise AssertionError("network access attempted")

    monkeypatch.setattr(urllib.request, "urlopen", forbid)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", forbid)
    monkeypatch.setattr(http.client.HTTPSConnection, "connect", forbid)

    with respx.mock(assert_all_called=False) as router:
        router.route().mock(return_value=httpx.Response(200))
        result = sync(db_session, user, fixture_messages())
        # also exercise the link picker directly on every fixture message
        from app.filters.links import pick_action_link

        for raw in fixture_messages().values():
            assert pick_action_link(parse_message(raw)).url
        assert len(router.calls) == 0

    assert attempts == []
    assert result.failed == 0
    assert sum(len(parse_message(r).links) for r in fixture_messages().values()) > 20


def test_tasks_reject_an_unknown_link_type(db_session, user):
    db_session.add(Task(user_id=user.id, title="t", link_type="bogus"))
    with pytest.raises(Exception, match="ck_tasks_link_type"):
        db_session.commit()
    db_session.rollback()
