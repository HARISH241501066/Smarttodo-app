"""Module 8: reminder ladder, outbox dispatcher, notifications, digest, scheduler and restart."""

from datetime import UTC, datetime, timedelta

import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi.testclient import TestClient
from freezegun import freeze_time
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.main import app
from app.models import Notification, Reminder, Task, User, UserSettings
from app.notify.channels import CHANNELS, Outgoing
from app.priority import recompute_priorities
from app.scheduler import jobs
from app.scheduler.core import JOBS, TIMEZONE, build_scheduler, register_jobs, start_scheduler
from app.scheduler.digest import run_digest
from app.scheduler.dispatcher import MAX_ATTEMPTS, dispatch_due
from app.scheduler.ladder import build_ladder
from app.scheduler.reminders import sync_reminders
from tests.gmail_fakes import FakeGmailApi

H = timedelta(hours=1)
NOW = datetime(2030, 1, 1, 6, 0, tzinfo=UTC)  # 11:30 IST


# ---------- helpers ----------


class Recorder:
    """A sender for every channel that records what it was asked to send."""

    def __init__(self, fail_on=()):
        self.sent: list[Outgoing] = []
        self.attempted = 0
        self.fail_on = set(fail_on)

    def send(self, message: Outgoing) -> None:
        self.attempted += 1
        if message.channel in self.fail_on:
            raise RuntimeError("provider down")
        self.sent.append(message)

    def senders(self):
        return {c: self for c in CHANNELS}

    def channels(self):
        return sorted(m.channel for m in self.sent)


@pytest.fixture
def user(db_session):
    u = User(email="me@example.test")
    db_session.add(u)
    db_session.commit()
    return u


def make_task(session, user, deadline, *, status="open", title="Submit report", **kw) -> Task:
    task = Task(
        user_id=user.id, title=title, deadline=deadline, status=status, source="other", **kw
    )
    session.add(task)
    session.commit()
    return task


def ladder_of(session, task, *, unsent_only=False) -> set[tuple]:
    stmt = select(Reminder.fire_at, Reminder.channel, Reminder.kind).where(
        Reminder.task_id == task.id
    )
    if unsent_only:
        stmt = stmt.where(Reminder.sent_at.is_(None))
    return {(r.fire_at, r.channel, r.kind) for r in session.execute(stmt)}


def make_ladder(session, task, **kw):
    sync_reminders(session, task, created=True, **kw)
    session.commit()
    return ladder_of(session, task)


# ---------- the ladder ----------


@freeze_time("2030-01-01 06:00:00")
def test_ladder_for_a_task_due_in_5_days(db_session, user):
    deadline = NOW + timedelta(days=5)
    task = make_task(db_session, user, deadline)
    assert make_ladder(db_session, task) == {
        (NOW, "inapp", "created"),
        (NOW, "push", "created"),
        (deadline - 48 * H, "push", "t48"),
        (deadline - 24 * H, "email", "t24"),
        (deadline - 10 * H, "email", "t10"),
        (deadline - 1 * H, "push", "t1"),
    }


@freeze_time("2030-01-01 06:00:00")
def test_ladder_for_a_task_due_in_30_hours_skips_the_48h_push(db_session, user):
    deadline = NOW + 30 * H
    task = make_task(db_session, user, deadline)
    assert make_ladder(db_session, task) == {
        (NOW, "inapp", "created"),
        (NOW, "push", "created"),
        (NOW + 6 * H, "email", "t24"),  # 30h - 24h
        (NOW + 20 * H, "email", "t10"),
        (NOW + 29 * H, "push", "t1"),
    }


@freeze_time("2030-01-01 06:00:00")
def test_ladder_for_a_task_due_in_6_hours_emails_immediately(db_session, user):
    deadline = NOW + 6 * H
    task = make_task(db_session, user, deadline)
    assert make_ladder(db_session, task) == {
        (NOW, "inapp", "created"),
        (NOW, "push", "created"),
        (NOW, "email", "immediate"),
        (NOW + 5 * H, "push", "t1"),
    }


def test_ladder_boundaries():
    def kinds(hours):
        return sorted((s.kind, s.channel) for s in build_ladder(NOW + hours * H, NOW))

    assert ("t24", "email") in kinds(24)  # an offset that is exactly now still fires
    assert not any(k == "immediate" for k, _ in kinds(24))
    assert ("immediate", "email") in kinds(23.9)
    assert ("t10", "email") in kinds(10) and ("immediate", "email") not in kinds(10)  # one email
    assert kinds(0.5) == [("immediate", "email")]  # t1 is past too
    assert kinds(-3) == []  # overdue: nothing to schedule


def test_no_deadline_and_not_created_means_no_steps_and_created_adds_only_the_pair():
    assert build_ladder(None, NOW) == []
    assert [(s.channel, s.kind) for s in build_ladder(None, NOW, created=True)] == [
        ("inapp", "created"),
        ("push", "created"),
    ]


@freeze_time("2030-01-01 06:00:00")
def test_tasks_that_are_not_open_get_no_reminders(db_session, user):
    for status in ("needs_review", "done", "dismissed"):
        task = make_task(db_session, user, NOW + 5 * 24 * H, status=status)
        assert make_ladder(db_session, task) == set()


@freeze_time("2030-01-01 06:00:00")
def test_with_sms_disabled_no_sms_reminders_are_created(db_session, user):
    task = make_task(db_session, user, NOW + 6 * H)
    assert get_settings().sms_enabled is False
    sync_reminders(db_session, task, created=True)
    assert db_session.scalar(select(func.count()).where(Reminder.channel == "sms")) == 0
    task2 = make_task(db_session, user, NOW + 5 * 24 * H)
    sync_reminders(db_session, task2, created=True, sms_enabled=False)
    assert db_session.scalar(select(func.count()).where(Reminder.channel == "sms")) == 0


@freeze_time("2030-01-01 06:00:00")
def test_with_sms_enabled_every_email_step_gets_an_sms(db_session, user):
    deadline = NOW + 5 * 24 * H
    task = make_task(db_session, user, deadline)
    sync_reminders(db_session, task, created=True, sms_enabled=True)
    db_session.commit()
    sms = {(f, k) for f, c, k in ladder_of(db_session, task) if c == "sms"}
    assert sms == {(deadline - 24 * H, "t24"), (deadline - 10 * H, "t10")}
    near = make_task(db_session, user, NOW + 6 * H)
    sync_reminders(db_session, near, created=True, sms_enabled=True)
    assert (NOW, "sms", "immediate") in ladder_of(db_session, near)


def test_sms_setting_comes_from_the_environment(db_session, user, monkeypatch):
    monkeypatch.setenv("SMS_ENABLED", "true")
    get_settings.cache_clear()
    task = make_task(db_session, user, NOW + 6 * H)
    sync_reminders(db_session, task, now=NOW, created=True)
    assert db_session.scalar(select(func.count()).where(Reminder.channel == "sms")) == 1


def test_regenerating_is_idempotent(db_session, user):
    task = make_task(db_session, user, NOW + 5 * 24 * H)
    first = make_ladder(db_session, task, now=NOW)
    sync_reminders(db_session, task, now=NOW, created=True)
    db_session.commit()
    assert ladder_of(db_session, task) == first


# ---------- dispatcher ----------


def test_a_task_found_with_less_than_24h_left_sends_its_email_immediately(db_session, user):
    task = make_task(db_session, user, NOW + 6 * H, title="Lab record")
    sync_reminders(db_session, task, now=NOW, created=True)
    db_session.commit()
    rec = Recorder()

    result = dispatch_due(db_session, now=NOW, senders=rec.senders())

    emails = [m for m in rec.sent if m.channel == "email"]
    assert len(emails) == 1
    assert emails[0].kind == "immediate" and emails[0].title == "Complete immediately: Lab record"
    assert rec.channels() == ["email", "inapp", "push"]  # the create pair goes out with it
    assert result.sent == 3


def test_running_the_dispatcher_twice_sends_each_reminder_exactly_once(db_session, user):
    task = make_task(db_session, user, NOW + 6 * H)
    sync_reminders(db_session, task, now=NOW, created=True)
    db_session.commit()
    rec = Recorder()

    first = dispatch_due(db_session, now=NOW, senders=rec.senders())
    second = dispatch_due(db_session, now=NOW, senders=rec.senders())

    assert (first.sent, second.sent) == (3, 0)
    assert len(rec.sent) == 3
    # later ticks send only what has since become due, again once
    later = dispatch_due(db_session, now=NOW + 5 * H, senders=rec.senders())
    again = dispatch_due(db_session, now=NOW + 5 * H, senders=rec.senders())
    assert (later.sent, again.sent) == (1, 0) and len(rec.sent) == 4


def test_reminders_are_not_sent_before_they_are_due(db_session, user):
    task = make_task(db_session, user, NOW + 5 * 24 * H)
    sync_reminders(db_session, task, now=NOW, created=True)
    db_session.commit()
    rec = Recorder()
    dispatch_due(db_session, now=NOW, senders=rec.senders())
    assert rec.channels() == ["inapp", "push"]
    assert db_session.scalar(select(func.count()).where(Reminder.sent_at.is_(None))) == 4


def test_marking_a_task_done_means_none_of_its_pending_reminders_are_sent_via_the_api(
    client_factory, api, engine, db_session, uid
):
    with freeze_time("2030-01-01 06:00:00"):
        client = client_factory(uid)
        task_id = client.post(
            "/tasks", json={"title": "t", "deadline": (NOW + 5 * 24 * H).isoformat()}
        ).json()["id"]
        db_session.expire_all()
        assert db_session.scalar(select(func.count()).where(Reminder.task_id == task_id)) == 6
        assert client.post(f"/tasks/{task_id}/done").status_code == 200

        rec = Recorder()
        dispatch_due(db_session, now=NOW + 10 * 24 * H, senders=rec.senders())
    assert rec.sent == []
    assert db_session.scalar(select(func.count()).where(Reminder.task_id == task_id)) == 0


def test_a_reminder_for_a_task_that_is_no_longer_open_is_dropped_not_sent(db_session, user):
    task = make_task(db_session, user, NOW + 5 * 24 * H)
    sync_reminders(db_session, task, now=NOW, created=True)
    db_session.commit()
    task.status = "done"  # changed behind the reminders' back
    db_session.commit()
    rec = Recorder()
    result = dispatch_due(db_session, now=NOW + 130 * H, senders=rec.senders())
    assert rec.sent == [] and result.skipped == 6
    assert db_session.scalar(select(func.count()).select_from(Reminder)) == 0
    assert db_session.scalar(select(func.count()).select_from(Notification)) == 0


def test_needs_review_tasks_are_skipped_too(db_session, user):
    task = make_task(db_session, user, NOW + 3 * H, status="needs_review")
    db_session.add(Reminder(task_id=task.id, fire_at=NOW, channel="email", kind="immediate"))
    db_session.commit()
    rec = Recorder()
    assert dispatch_due(db_session, now=NOW, senders=rec.senders()).skipped == 1
    assert rec.sent == []


def test_a_late_reminder_for_a_passed_deadline_is_dropped(db_session, user):
    task = make_task(db_session, user, NOW - 2 * H)
    db_session.add(Reminder(task_id=task.id, fire_at=NOW - 20 * H, channel="push", kind="t1"))
    db_session.commit()
    rec = Recorder()
    assert dispatch_due(db_session, now=NOW, senders=rec.senders()).skipped == 1
    assert rec.sent == []


def test_failures_count_attempts_and_stop_after_five(db_session, user):
    task = make_task(db_session, user, NOW + 5 * 24 * H)
    db_session.add(Reminder(task_id=task.id, fire_at=NOW, channel="push", kind="created"))
    db_session.commit()
    rec = Recorder(fail_on={"push"})

    for expected in range(1, MAX_ATTEMPTS + 1):
        result = dispatch_due(db_session, now=NOW, senders=rec.senders())
        assert result.failed == 1
        assert db_session.scalar(select(Reminder.attempts)) == expected
    again = dispatch_due(db_session, now=NOW, senders=rec.senders())

    assert (again.sent, again.failed) == (0, 0)
    assert rec.attempted == MAX_ATTEMPTS
    assert db_session.scalar(select(Reminder.sent_at)) is None
    assert db_session.scalar(select(func.count()).select_from(Notification)) == 0


def test_a_reminder_that_fails_then_succeeds_is_sent_once(db_session, user):
    task = make_task(db_session, user, NOW + 5 * 24 * H)
    db_session.add(Reminder(task_id=task.id, fire_at=NOW, channel="email", kind="t24"))
    db_session.commit()
    assert dispatch_due(db_session, now=NOW, senders=Recorder(fail_on={"email"}).senders()).failed
    ok = Recorder()
    assert dispatch_due(db_session, now=NOW, senders=ok.senders()).sent == 1
    assert dispatch_due(db_session, now=NOW, senders=ok.senders()).sent == 0
    assert db_session.scalar(select(Reminder.attempts)) == 1
    assert db_session.scalar(select(func.count()).select_from(Notification)) == 1


def test_one_failing_channel_does_not_block_the_others(db_session, user):
    task = make_task(db_session, user, NOW + 6 * H)
    sync_reminders(db_session, task, now=NOW, created=True)
    db_session.commit()
    rec = Recorder(fail_on={"push"})
    result = dispatch_due(db_session, now=NOW, senders=rec.senders())
    assert (result.sent, result.failed) == (2, 1)
    assert rec.channels() == ["email", "inapp"]


# ---------- notifications ----------


def test_every_sent_reminder_creates_exactly_one_notification(db_session, user):
    deadline = NOW + 5 * 24 * H
    task = make_task(db_session, user, deadline, title="Quiz", link="https://example.com/quiz")
    sync_reminders(db_session, task, now=NOW, created=True)
    db_session.commit()
    rec = Recorder()
    for moment in (NOW, deadline - 48 * H, deadline - 24 * H, deadline - 10 * H, deadline - H):
        dispatch_due(db_session, now=moment, senders=rec.senders())
        dispatch_due(db_session, now=moment, senders=rec.senders())  # a second pass adds nothing

    sent = list(db_session.scalars(select(Reminder).where(Reminder.sent_at.is_not(None))))
    notes = list(db_session.scalars(select(Notification)))
    assert len(sent) == 6 == len(notes) == len(rec.sent)
    assert sorted(n.reminder_id for n in notes) == sorted(r.id for r in sent)
    assert all(n.user_id == user.id and n.task_id == task.id for n in notes)
    assert all(n.link == "https://example.com/quiz" and n.read is False for n in notes)
    assert all(n.kind == "reminder" and "Quiz" in n.title for n in notes)


def test_notification_wording_follows_the_ladder_step(db_session, user):
    deadline = NOW + 5 * 24 * H
    task = make_task(db_session, user, deadline, title="Quiz")
    sync_reminders(db_session, task, now=NOW, created=True)
    db_session.commit()
    dispatch_due(db_session, now=deadline - H, senders=Recorder().senders())
    titles = {n.title for n in db_session.scalars(select(Notification))}
    assert titles == {
        "New task: Quiz",
        "Due in 2 days: Quiz",
        "Complete immediately: Quiz",
        "Final reminder: Quiz",
        "Due in 1 hour: Quiz",
    }
    body = db_session.scalar(
        select(Notification.body).where(Notification.title == "New task: Quiz")
    )
    assert body.endswith("IST") and "2030" in body


def test_notifications_are_unique_per_reminder(db_session, user):
    task = make_task(db_session, user, NOW + 5 * 24 * H)
    reminder = Reminder(task_id=task.id, fire_at=NOW, channel="inapp", kind="created")
    db_session.add(reminder)
    db_session.commit()
    for _ in range(2):
        db_session.add(
            Notification(user_id=user.id, task_id=task.id, reminder_id=reminder.id, title="x")
        )
    with pytest.raises(Exception, match="uq_notifications_reminder_id"):
        db_session.commit()
    db_session.rollback()


# ---------- regeneration when the task changes ----------


def test_a_deadline_change_regenerates_reminders_with_no_stale_ones(
    client_factory, db_session, uid
):
    old, new = NOW + 5 * 24 * H, NOW + 30 * H
    with freeze_time("2030-01-01 06:00:00"):
        client = client_factory(uid)
        task_id = client.post("/tasks", json={"title": "t", "deadline": old.isoformat()}).json()[
            "id"
        ]
        rec = Recorder()
        dispatch_due(db_session, now=NOW, senders=rec.senders())  # the create pair goes out
        sent_before = {(r.fire_at, r.channel) for r in db_session.scalars(
            select(Reminder).where(Reminder.sent_at.is_not(None)))}  # fmt: skip

        assert (
            client.patch(f"/tasks/{task_id}", json={"deadline": new.isoformat()}).status_code == 200
        )

    db_session.expire_all()
    task = db_session.get(Task, task_id)
    unsent = ladder_of(db_session, task, unsent_only=True)
    expected = {(s.fire_at, s.channel, s.kind) for s in build_ladder(new, NOW)}
    assert unsent == expected
    # nothing from the old 5-day ladder survives
    assert not any(
        fire_at in {old - 48 * H, old - 24 * H, old - 10 * H, old - H} for fire_at, *_ in unsent
    )
    # the reminders that had already gone out stay as history
    assert {(r.fire_at, r.channel) for r in db_session.scalars(
        select(Reminder).where(Reminder.sent_at.is_not(None)))} == sent_before  # fmt: skip


def test_an_immediate_email_is_not_repeated_when_the_deadline_moves_again(
    client_factory, db_session, uid
):
    with freeze_time("2030-01-01 06:00:00"):
        client = client_factory(uid)
        task_id = client.post(
            "/tasks", json={"title": "t", "deadline": (NOW + 20 * H).isoformat()}
        ).json()["id"]
        dispatch_due(db_session, now=NOW, senders=Recorder().senders())
        client.patch(f"/tasks/{task_id}", json={"deadline": (NOW + 18 * H).isoformat()})
    db_session.expire_all()
    emails = db_session.scalars(
        select(Reminder).where(Reminder.task_id == task_id, Reminder.channel == "email")
    ).all()
    assert [e.kind for e in emails if e.sent_at is not None] == ["immediate"]
    assert [e.kind for e in emails if e.sent_at is None] == ["t10"]  # only the future one


def test_clearing_the_deadline_drops_the_offset_reminders(client_factory, db_session, uid):
    with freeze_time("2030-01-01 06:00:00"):
        client = client_factory(uid)
        task_id = client.post(
            "/tasks", json={"title": "t", "deadline": (NOW + 5 * 24 * H).isoformat()}
        ).json()["id"]
        client.patch(f"/tasks/{task_id}", json={"deadline": None})
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).where(Reminder.task_id == task_id)) == 0


def test_reopening_a_done_task_rebuilds_its_reminders(client_factory, db_session, uid):
    with freeze_time("2030-01-01 06:00:00"):
        client = client_factory(uid)
        task_id = client.post(
            "/tasks", json={"title": "t", "deadline": (NOW + 5 * 24 * H).isoformat()}
        ).json()["id"]
        client.post(f"/tasks/{task_id}/done")
        assert db_session.scalar(select(func.count()).where(Reminder.task_id == task_id)) == 0
        client.patch(f"/tasks/{task_id}", json={"status": "open"})
    db_session.expire_all()
    # 4 ladder steps; the create pair is not repeated for a reopened task
    assert db_session.scalar(select(func.count()).where(Reminder.task_id == task_id)) == 4


def test_confirming_a_review_task_starts_its_ladder(client_factory, engine, db_session, uid):
    with engine.begin() as conn:
        task_id = conn.execute(
            text(
                "INSERT INTO tasks (user_id, title, status, deadline) "
                "VALUES (:u, 't', 'needs_review', :d) RETURNING id"
            ),
            {"u": uid, "d": NOW + 5 * 24 * H},
        ).scalar_one()
    with freeze_time("2030-01-01 06:00:00"):
        client = client_factory(uid)
        assert db_session.scalar(select(func.count()).where(Reminder.task_id == task_id)) == 0
        assert client.post(f"/review/{task_id}/confirm").status_code == 200
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).where(Reminder.task_id == task_id)) == 6


def test_a_title_only_edit_leaves_reminders_untouched(client_factory, db_session, uid):
    with freeze_time("2030-01-01 06:00:00"):
        client = client_factory(uid)
        task_id = client.post(
            "/tasks", json={"title": "t", "deadline": (NOW + 5 * 24 * H).isoformat()}
        ).json()["id"]
        before = sorted(db_session.scalars(select(Reminder.id).where(Reminder.task_id == task_id)))
        client.patch(f"/tasks/{task_id}", json={"title": "renamed"})
    db_session.expire_all()
    assert (
        sorted(db_session.scalars(select(Reminder.id).where(Reminder.task_id == task_id))) == before
    )


def test_tasks_created_by_the_sync_pipeline_get_reminders(engine, user_with_mail):
    session_factory, gmail = user_with_mail
    outcomes = jobs.poll_sync(
        session_factory,
        lambda user, session: gmail,
        now=lambda: datetime(2026, 1, 1, 6, tzinfo=UTC),
    )
    assert list(outcomes.values())[0].startswith("ok")
    with session_factory() as s:
        task = s.scalars(select(Task).where(Task.status == "open")).first()
        assert task is not None
        kinds = {r.kind for r in s.scalars(select(Reminder).where(Reminder.task_id == task.id))}
        assert "created" in kinds


# ---------- digest ----------

DIGEST_NOW = datetime(2030, 1, 1, 3, 0, tzinfo=UTC)  # 08:30 IST


def add_user(session, email, digest_hour=None):
    u = User(email=email, encrypted_refresh_token="x")
    session.add(u)
    session.flush()
    if digest_hour is not None:
        session.add(UserSettings(user_id=u.id, denylist=[], allowlist=[], digest_hour=digest_hour))
    session.commit()
    return u


def test_digest_lists_everything_due_in_the_next_72_hours(db_session, user):
    make_task(db_session, user, DIGEST_NOW + 10 * H, title="Soon")
    make_task(db_session, user, DIGEST_NOW + 71 * H, title="Edge")
    make_task(db_session, user, DIGEST_NOW + 73 * H, title="Later")
    make_task(db_session, user, DIGEST_NOW - H, title="Overdue")
    make_task(db_session, user, DIGEST_NOW + 5 * H, title="Done", status="done")
    make_task(db_session, user, DIGEST_NOW + 5 * H, title="Review", status="needs_review")
    make_task(db_session, user, None, title="No deadline")

    assert run_digest(db_session, now=DIGEST_NOW) == 1

    note = db_session.scalar(select(Notification))
    assert note.kind == "digest" and note.task_id is None and note.read is False
    assert "2 task(s)" in note.title
    assert note.body.splitlines()[0].startswith("- Soon") and "Edge" in note.body
    for absent in ("Later", "Overdue", "Done", "Review", "No deadline"):
        assert absent not in note.body


def test_digest_is_sent_once_per_day_at_or_after_the_digest_hour(db_session, user):
    make_task(db_session, user, DIGEST_NOW + 10 * H)
    before_hour = DIGEST_NOW - 2 * H  # 06:30 IST, hour 6 < 8
    assert run_digest(db_session, now=before_hour) == 0
    assert run_digest(db_session, now=DIGEST_NOW) == 1
    assert run_digest(db_session, now=DIGEST_NOW) == 0
    assert run_digest(db_session, now=DIGEST_NOW + 5 * H) == 0  # same IST day: no second digest
    next_day = DIGEST_NOW + 24 * H
    make_task(db_session, user, next_day + 10 * H, title="Tomorrow")
    assert run_digest(db_session, now=next_day) == 1
    assert db_session.scalar(select(func.count()).select_from(Notification)) == 2


def test_digest_respects_each_users_hour_and_isolates_users(db_session, user):
    early = add_user(db_session, "early@example.test", digest_hour=5)
    late = add_user(db_session, "late@example.test", digest_hour=20)
    for u in (user, early, late):
        make_task(db_session, u, DIGEST_NOW + 10 * H, title=f"for {u.email}")
    assert run_digest(db_session, now=DIGEST_NOW) == 2  # default hour 8 and hour 5; not hour 20
    got = {n.user_id: n.body for n in db_session.scalars(select(Notification))}
    assert set(got) == {user.id, early.id}
    assert f"for {early.email}" in got[early.id] and "late@" not in got[early.id]


def test_no_digest_when_nothing_is_due(db_session, user):
    make_task(db_session, user, DIGEST_NOW + 100 * H)
    assert run_digest(db_session, now=DIGEST_NOW) == 0


# ---------- jobs ----------


@pytest.fixture
def user_with_mail(engine):
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as s:
        add_user(s, "poller@example.test")
    import json
    from pathlib import Path

    raw = json.loads(
        Path(__file__)
        .parent.joinpath("fixtures", "task_emails", "01_nptel_quiz_with_noise_links.json")
        .read_text()
    )
    raw["payload"]["headers"].append(
        {"name": "Authentication-Results", "value": "mx.google.com; dmarc=pass"}
    )
    return factory, FakeGmailApi({raw["id"]: raw})


def test_polling_sync_one_users_failure_does_not_stop_the_others(engine):
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as s:
        a = add_user(s, "a@example.test").id
        b = add_user(s, "b@example.test").id
        c = add_user(s, "c@example.test").id
        reauth = add_user(s, "d@example.test")
        reauth.needs_reauth = True
        s.commit()

    calls = []

    def gmail_factory(user, session):
        calls.append(user.id)
        if user.id == a:
            raise RuntimeError("boom")
        return FakeGmailApi({})

    outcomes = jobs.poll_sync(factory, gmail_factory)
    assert outcomes[a] == "error_RuntimeError"
    assert outcomes[b].startswith("ok") and outcomes[c].startswith("ok")
    assert sorted(calls) == [a, b, c]  # the user who needs to sign in again is not polled


def test_priority_job_upgrades_tasks_across_all_users(db_session, user):
    other = add_user(db_session, "o@example.test")
    for u in (user, other):
        make_task(db_session, u, NOW + 10 * H, priority="regular")
    assert recompute_priorities(db_session, now=NOW) == 2
    db_session.commit()


def test_job_wrappers_swallow_errors(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(jobs, "dispatch_due", boom)
    monkeypatch.setattr(jobs, "run_digest", boom)
    monkeypatch.setattr(jobs, "recompute_priorities", boom)
    monkeypatch.setattr(jobs, "poll_sync", boom)
    monkeypatch.setattr(jobs, "get_engine", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    for job in (
        jobs.dispatch_job,
        jobs.digest_job,
        jobs.priority_recompute_job,
        jobs.poll_sync_job,
    ):
        job()  # must not raise inside the scheduler thread


# ---------- scheduler configuration and restart ----------


def db_url(engine) -> str:
    return engine.url.render_as_string(hide_password=False)


def job_summary(scheduler: BackgroundScheduler) -> dict[str, tuple]:
    return {
        j.id: (j.func_ref, j.max_instances, str(j.trigger), j.coalesce)
        for j in scheduler.get_jobs()
    }


def test_the_four_jobs_are_configured_as_specified(engine):
    scheduler = build_scheduler(db_url(engine))
    register_jobs(scheduler)
    scheduler.start(paused=True)
    try:
        jobs_by_id = {j.id: j for j in scheduler.get_jobs()}
        assert set(jobs_by_id) == {"dispatch", "poll_sync", "priority_recompute", "digest"}
        assert set(JOBS) == set(jobs_by_id)
        assert all(j.max_instances == 1 for j in jobs_by_id.values())
        assert str(scheduler.timezone) == TIMEZONE == "Asia/Kolkata"
        assert jobs_by_id["dispatch"].trigger.interval == timedelta(minutes=1)
        assert jobs_by_id["poll_sync"].trigger.interval == timedelta(minutes=15)
        assert jobs_by_id["priority_recompute"].trigger.interval == timedelta(minutes=30)
        assert "minute='0'" in str(jobs_by_id["digest"].trigger)
        assert jobs_by_id["digest"].trigger.timezone.key == "Asia/Kolkata"
        assert "dispatch_job" in jobs_by_id["dispatch"].func_ref
    finally:
        scheduler.shutdown(wait=False)


def test_stopping_and_starting_loses_no_scheduled_jobs(engine):
    url = db_url(engine)
    first = start_scheduler(url, paused=True)
    before = job_summary(first)
    first.shutdown(wait=False)
    assert len(before) == 4

    # a brand-new scheduler that registers nothing still finds the jobs in Postgres
    plain = build_scheduler(url)
    plain.start(paused=True)
    try:
        assert job_summary(plain) == before
    finally:
        plain.shutdown(wait=False)

    # restarting through the normal path neither loses nor duplicates them
    for _ in range(2):
        again = start_scheduler(url, paused=True)
        assert job_summary(again) == before
        again.shutdown(wait=False)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM apscheduler_jobs")).scalar_one() == 4


def test_restarting_the_app_keeps_the_scheduled_jobs(engine, monkeypatch):
    url = db_url(engine)
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(
        jobs, "get_engine", lambda: engine
    )  # jobs that fire must not touch dev data
    get_settings.cache_clear()
    seen = []

    for _ in range(2):  # start, stop, start, stop
        with TestClient(app):
            scheduler = app.state.scheduler
            assert scheduler.running
            seen.append(job_summary(scheduler))
        assert not scheduler.running

    assert seen[0] == seen[1] and len(seen[0]) == 4
    check = build_scheduler(url)
    check.start(paused=True)
    try:
        assert set(job_summary(check)) == set(JOBS)
    finally:
        check.shutdown(wait=False)


def test_the_scheduler_is_off_by_default(engine):
    get_settings.cache_clear()
    assert get_settings().scheduler_enabled is False
    with TestClient(app):
        assert not hasattr(app.state, "scheduler") or not app.state.scheduler.running
