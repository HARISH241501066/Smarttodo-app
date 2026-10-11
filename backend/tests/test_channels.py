# ruff: noqa: E501  (long literals in fixtures and expected strings)
"""Module 9: tokens, mark-done page, email, push, SMS, notifications API, dev page."""

import logging
import smtplib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from pywebpush import WebPushException
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings
from app.models import Notification, PushSubscription, Reminder, Task
from app.notify.channels import CHANNELS, ChannelDisabled, Outgoing, StubSender, default_senders
from app.notify.email import EmailSender, build_email
from app.notify.push import PushSender, payload_for
from app.notify.sms import SmsSender, StubSmsProvider
from app.notify.tokens import (
    TokenError,
    TokenExpired,
    make_done_token,
    read_done_token,
)
from app.scheduler.dispatcher import MAX_ATTEMPTS, dispatch_due
from app.scheduler.reminders import sync_reminders
from app.security import sign

H = timedelta(hours=1)
NOW = datetime(2030, 1, 1, 6, 0, tzinfo=UTC)  # 11:30 IST
DEADLINE = NOW + 5 * H  # 16:30 IST


def settings(**kw) -> Settings:
    base = {
        "smtp_host": "smtp.test",
        "smtp_port": 587,
        "smtp_username": "sender@example.test",
        "smtp_password": SecretStr("s3cret-app-password"),
        "email_from": "Smart To-Do <sender@example.test>",
        "public_base_url": "https://app.example.test",
        "vapid_public_key": "PUBKEY",
        "vapid_private_key": SecretStr("PRIVKEY"),
        "vapid_subject": "mailto:me@example.test",
    }
    return Settings(_env_file=None, **(base | kw))


def outgoing(**kw) -> Outgoing:
    base = {
        "user_id": 1,
        "task_id": 7,
        "reminder_id": 70,
        "channel": "email",
        "kind": "immediate",
        "title": "Complete immediately: Quiz",
        "body": "Due x",
        "link": "https://onlinecourses.nptel.ac.in/noc/quiz/5",
        "user_email": "me@example.test",
        "deadline": datetime(2030, 1, 22, 18, 29, tzinfo=UTC),
    }
    return Outgoing(**(base | kw))


@pytest.fixture
def user(db_session):
    from app.models import User

    u = User(email="me@example.test")
    db_session.add(u)
    db_session.commit()
    return u


def make_task(session, user, deadline=DEADLINE, **kw) -> Task:
    values = {"title": "Quiz", "status": "open", "source": "other", "link": "https://example.com/q"}
    task = Task(user_id=user.id, deadline=deadline, **(values | kw))
    session.add(task)
    session.commit()
    return task


# ====================================================================== tokens


def test_token_round_trip():
    token = make_done_token(42, DEADLINE, now=NOW)
    parsed = read_done_token(token, now=NOW)
    assert parsed.task_id == 42 and parsed.expires_at == DEADLINE and len(parsed.nonce) >= 12


def test_each_token_has_its_own_nonce():
    a, b = make_done_token(1, DEADLINE), make_done_token(1, DEADLINE)
    assert read_done_token(a, now=NOW).nonce != read_done_token(b, now=NOW).nonce


def test_an_expired_token_is_rejected():
    token = make_done_token(1, DEADLINE, now=NOW)
    assert read_done_token(token, now=DEADLINE - timedelta(seconds=1))
    with pytest.raises(TokenExpired):
        read_done_token(token, now=DEADLINE)  # expires at the deadline
    with pytest.raises(TokenExpired):
        read_done_token(token, now=DEADLINE + 10 * H)


def test_a_tampered_token_is_rejected():
    token = make_done_token(1, DEADLINE, now=NOW)
    flipped = token[:-1] + ("A" if token[-1] != "A" else "B")
    body, _, rest = token.partition(".")
    swapped_payload = ("X" if body[0] != "X" else "Y") + body[1:] + "." + rest
    for bad in (flipped, swapped_payload, token + "x", token[:-5]):
        with pytest.raises(TokenError):
            read_done_token(bad, now=NOW)


@pytest.mark.parametrize("bad", [None, "", "garbage", "a.b.c", "....", " "])
def test_garbage_tokens_are_rejected(bad):
    with pytest.raises(TokenError):
        read_done_token(bad, now=NOW)


def test_a_token_signed_for_another_purpose_is_rejected():
    session_cookie = sign({"uid": 1}, "session")
    forged = sign({"t": 1, "n": "abc", "x": int(DEADLINE.timestamp())}, "session")
    for other in (session_cookie, forged):
        with pytest.raises(TokenError):
            read_done_token(other, now=NOW)


def test_a_token_with_the_wrong_payload_shape_is_rejected():
    from app.notify.tokens import SALT

    for payload in ({"t": "1", "n": "a", "x": 5}, {"t": 1}, {"t": 1, "n": 5, "x": 9}):
        with pytest.raises(TokenError):
            read_done_token(sign(payload, SALT), now=NOW)


def test_a_token_for_a_task_without_a_deadline_lasts_a_week():
    token = make_done_token(1, None, now=NOW)
    assert read_done_token(token, now=NOW + timedelta(days=6))
    with pytest.raises(TokenExpired):
        read_done_token(token, now=NOW + timedelta(days=8))


# ====================================================================== mark-done page


@pytest.fixture
def web(client_factory):
    return client_factory()  # no session cookie: the token is the credential


def task_in_db(engine, uid, **kw) -> int:
    values = {
        "user_id": uid,
        "title": "Quiz 5",
        "status": "open",
        "deadline": datetime.now(UTC) + 5 * H,
    }
    values |= kw
    cols, params = ", ".join(values), ", ".join(f":{k}" for k in values)
    with engine.begin() as conn:
        return conn.execute(
            text(f"INSERT INTO tasks ({cols}) VALUES ({params}) RETURNING id"), values
        ).scalar_one()


def done_url(task_id, deadline=None) -> str:
    return "/action/done?t=" + make_done_token(task_id, deadline or datetime.now(UTC) + 5 * H)


def status_of(engine, task_id) -> str:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT status FROM tasks WHERE id = :i"), {"i": task_id}
        ).scalar_one()


def test_get_shows_a_confirmation_page_and_changes_nothing(web, engine, uid):
    task_id = task_in_db(engine, uid)
    url = done_url(task_id)
    for _ in range(3):  # a link scanner may fetch it any number of times
        resp = web.get(url)
        assert resp.status_code == 200
        assert "Quiz 5" in resp.text and "<button" in resp.text and 'method="post"' in resp.text
    assert status_of(engine, task_id) == "open"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM used_action_tokens")).scalar_one() == 0


def test_clicking_the_button_posts_and_marks_the_task_done(web, engine, uid):
    task_id = task_in_db(engine, uid)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO reminders (task_id, fire_at, channel, kind) VALUES (:t, now(), 'email', 't24')"
            ),
            {"t": task_id},
        )
    url = done_url(task_id)
    page = web.get(url).text
    action = page.split('action="')[1].split('"')[0].replace("&amp;", "&")
    assert action == url  # the button posts to the same link

    resp = web.post(action)

    assert resp.status_code == 200 and "marked as done" in resp.text
    assert status_of(engine, task_id) == "done"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM reminders")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM used_action_tokens")).scalar_one() == 1


def test_a_second_click_shows_already_done(web, engine, uid):
    task_id = task_in_db(engine, uid)
    url = done_url(task_id)
    assert "marked as done" in web.post(url).text
    for resp in (web.get(url), web.post(url)):
        assert resp.status_code == 200
        assert "already marked as done" in resp.text and "<button" not in resp.text


def test_a_used_link_cannot_complete_a_reopened_task(web, engine, uid):
    task_id = task_in_db(engine, uid)
    url = done_url(task_id)
    web.post(url)
    with engine.begin() as conn:
        conn.execute(text("UPDATE tasks SET status = 'open' WHERE id = :i"), {"i": task_id})
    for resp in (web.get(url), web.post(url)):
        assert resp.status_code == 410 and "already used" in resp.text
    assert status_of(engine, task_id) == "open"


def test_a_link_claimed_by_a_parallel_click_is_not_used_twice(web, engine, uid):
    task_id = task_in_db(engine, uid)
    token = make_done_token(task_id, datetime.now(UTC) + 5 * H)
    nonce = read_done_token(token).nonce
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO used_action_tokens (nonce, task_id) VALUES (:n, :t)"),
            {"n": nonce, "t": task_id},
        )
    assert "already used" in web.post(f"/action/done?t={token}").text
    assert status_of(engine, task_id) == "open"


def test_expired_and_tampered_links_are_rejected_without_changing_anything(web, engine, uid):
    task_id = task_in_db(engine, uid)
    expired = "/action/done?t=" + make_done_token(task_id, datetime.now(UTC) - timedelta(seconds=5))
    tampered = done_url(task_id)[:-3] + "xyz"
    for method in (web.get, web.post):
        assert method(expired).status_code == 410
        assert "expired" in method(expired).text
        for url in (tampered, "/action/done?t=nonsense", "/action/done"):
            resp = method(url)
            assert resp.status_code == 400 and "not valid" in resp.text
    assert status_of(engine, task_id) == "open"


def test_unknown_and_inactive_tasks(web, engine, uid):
    assert web.get(done_url(999999)).status_code == 404
    for status in ("dismissed", "needs_review"):
        task_id = task_in_db(engine, uid, status=status)
        assert web.post(done_url(task_id)).status_code == 409
        assert status_of(engine, task_id) == status


def test_the_page_escapes_the_task_title_and_sets_safe_headers(web, engine, uid):
    task_id = task_in_db(engine, uid, title='<script>alert("x")</script> & co')
    resp = web.get(done_url(task_id))
    assert "<script>" not in resp.text and "&lt;script&gt;" in resp.text
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'none'" in resp.headers["content-security-policy"]


def test_marking_done_by_email_link_removes_the_calendar_event(web, engine, uid, monkeypatch):
    from app.api import actions

    fake = FakeCalendar()
    monkeypatch.setattr(actions, "calendar_for", lambda user, session: fake)
    task_id = task_in_db(engine, uid, gcal_event_id="evt-1")
    web.post(done_url(task_id))
    assert fake.deleted == ["evt-1"]
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT gcal_event_id FROM tasks WHERE id = :i"), {"i": task_id}
            ).scalar_one()
            is None
        )


# ====================================================================== email message


def test_email_subject_matches_the_spec():
    email = build_email(
        outgoing(), sender="a@example.test", base_url="https://app.example.test", now=NOW
    )
    assert email["Subject"] == "⏰ Complete immediately: Quiz — due Tue, 22 Jan 2030, 11:59 PM IST"
    assert email["To"] == "me@example.test" and email["From"] == "a@example.test"


def test_email_html_has_the_button_the_domain_and_the_mark_done_link():
    email = build_email(
        outgoing(), sender="a@example.test", base_url="https://app.example.test/", now=NOW
    )
    html = email.get_body(("html",)).get_content()
    plain = email.get_body(("plain",)).get_content()
    link = "https://onlinecourses.nptel.ac.in/noc/quiz/5"

    assert f'href="{link}"' in html and "Open &amp; Complete" in html
    assert "font-size:20px" in html  # a big button
    assert "Opens onlinecourses.nptel.ac.in" in html
    assert "Tue, 22 Jan 2030, 11:59 PM IST" in html
    assert 'href="https://app.example.test/action/done?t=' in html and ">Mark as done<" in html
    assert f"Open & Complete: {link}" in plain and "(opens onlinecourses.nptel.ac.in)" in plain
    assert "https://app.example.test/action/done?t=" in plain


def test_the_mark_done_token_in_the_email_is_valid_and_expires_at_the_deadline():
    email = build_email(outgoing(), sender="a@x.test", base_url="https://app.example.test", now=NOW)
    plain = email.get_body(("plain",)).get_content()
    token = plain.split("Mark as done: https://app.example.test/action/done?t=")[1].split()[0]
    parsed = read_done_token(token, now=NOW)
    assert parsed.task_id == 7 and parsed.expires_at == datetime(2030, 1, 22, 18, 29, tzinfo=UTC)


def test_email_escapes_html_in_the_title_and_cleans_the_subject():
    msg = outgoing(title='Fix <b>this</b> & "that"\r\nBcc: evil@example.test')
    email = build_email(msg, sender="a@x.test", base_url="https://app.example.test", now=NOW)
    html = email.get_body(("html",)).get_content()
    assert "<b>this</b>" not in html and "&lt;b&gt;this&lt;/b&gt;" in html
    assert "\n" not in email["Subject"] and "\r" not in email["Subject"]
    assert email["Bcc"] is None


@pytest.mark.parametrize("link", [None, "javascript:alert(1)", "mailto:x@example.test", ""])
def test_email_without_a_usable_link_has_no_button_but_keeps_mark_done(link):
    email = build_email(
        outgoing(link=link), sender="a@x.test", base_url="https://app.example.test", now=NOW
    )
    html = email.get_body(("html",)).get_content()
    assert "Open &amp; Complete" not in html and "javascript:" not in html
    assert ">Mark as done<" in html


def test_email_for_a_task_without_a_deadline():
    email = build_email(
        outgoing(deadline=None), sender="a@x.test", base_url="https://app.example.test", now=NOW
    )
    assert (
        " — due " not in email["Subject"]
        and "No deadline set" in email.get_body(("plain",)).get_content()
    )


# ====================================================================== SMTP sending


class FakeSMTP:
    """Records what the sender does. `errors` are raised, in order, by successive send attempts."""

    def __init__(self, log, errors):
        self.log, self.errors = log, errors

    def __enter__(self):
        self.log.append("connect")
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        self.log.append("ehlo")

    def starttls(self, context=None):
        self.log.append("starttls")

    def login(self, user, password):
        self.log.append(("login", user, password))

    def send_message(self, msg):
        self.log.append(("send", msg["To"]))
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error


def smtp_sender(errors=(), **kw):
    log, errors = [], list(errors)
    from tenacity import wait_none

    sender = EmailSender(
        settings(**kw), smtp_factory=lambda: FakeSMTP(log, errors), wait=wait_none()
    )
    return sender, log


def test_email_is_sent_over_starttls_with_login():
    sender, log = smtp_sender()
    sender.send(outgoing())
    assert log == ["connect", "ehlo", "starttls", "ehlo",
                   ("login", "sender@example.test", "s3cret-app-password"), ("send", "me@example.test")]  # fmt: skip


def test_ssl_mode_skips_starttls():
    sender, log = smtp_sender(smtp_security="ssl")
    sender.send(outgoing())
    assert "starttls" not in log and ("send", "me@example.test") in log


def test_a_transient_smtp_failure_is_retried_then_succeeds():
    sender, log = smtp_sender(errors=[smtplib.SMTPServerDisconnected("dropped"), None])
    sender.send(outgoing())
    assert log.count("connect") == 2 and log.count(("send", "me@example.test")) == 2


@pytest.mark.parametrize(
    "error",
    [
        smtplib.SMTPServerDisconnected("dropped"),
        smtplib.SMTPConnectError(421, "busy"),
        smtplib.SMTPResponseException(451, "try later"),
        TimeoutError("slow"),
        ConnectionResetError("reset"),
    ],
)
def test_smtp_failure_retries_three_times_then_raises(error):
    sender, log = smtp_sender(errors=[error, error, error, error])
    with pytest.raises(type(error)):
        sender.send(outgoing())
    assert log.count("connect") == 3  # one attempt plus two retries


@pytest.mark.parametrize(
    "error",
    [
        smtplib.SMTPAuthenticationError(535, b"bad credentials"),
        smtplib.SMTPRecipientsRefused({"me@example.test": (550, b"no")}),
        smtplib.SMTPResponseException(554, "rejected"),
    ],
)
def test_permanent_smtp_failures_are_not_retried(error):
    sender, log = smtp_sender(errors=[error])
    with pytest.raises(type(error)):
        sender.send(outgoing())
    assert log.count("connect") == 1


def test_email_needs_a_recipient():
    sender, log = smtp_sender()
    with pytest.raises(ValueError):
        sender.send(outgoing(user_email=None))
    assert log == []


def test_the_smtp_password_is_never_logged(caplog):
    sender, _ = smtp_sender(errors=[smtplib.SMTPServerDisconnected("x")] * 5)
    with caplog.at_level(logging.DEBUG), pytest.raises(smtplib.SMTPServerDisconnected):
        sender.send(outgoing())
    assert "s3cret-app-password" not in caplog.text and "me@example.test" not in caplog.text


def test_an_smtp_failure_retries_then_counts_an_attempt_without_crashing_the_dispatcher(
    db_session, user
):
    task = make_task(db_session, user, NOW + 6 * H)  # <24h left: the immediate email is queued
    sync_reminders(db_session, task, now=NOW, created=True)
    db_session.commit()
    sender, log = smtp_sender(errors=[smtplib.SMTPServerDisconnected("down")] * 60)
    senders = {c: StubSender(c) for c in CHANNELS}
    senders["email"] = sender

    result = dispatch_due(db_session, now=NOW, senders=senders)  # must not raise

    assert (result.sent, result.failed) == (2, 1)  # inapp + push went out, the email failed
    assert log.count("connect") == 3  # the sender retried before giving up
    email = db_session.scalar(select(Reminder).where(Reminder.channel == "email"))
    assert email.attempts == 1 and email.sent_at is None
    assert db_session.scalar(select(func.count()).select_from(Notification)) == 2
    # and after the max number of passes it is given up on, quietly
    for _ in range(MAX_ATTEMPTS + 2):
        dispatch_due(db_session, now=NOW, senders=senders)
    assert (
        db_session.scalar(select(Reminder.attempts).where(Reminder.channel == "email"))
        == MAX_ATTEMPTS
    )


def test_email_through_the_dispatcher_carries_the_task_link_and_recipient(db_session, user):
    task = make_task(db_session, user, NOW + 6 * H, link="https://forms.gle/abc")
    sync_reminders(db_session, task, now=NOW, created=True)
    db_session.commit()
    sender, log = smtp_sender()
    senders = {c: StubSender(c) for c in CHANNELS} | {"email": sender}
    dispatch_due(db_session, now=NOW, senders=senders)
    assert ("send", "me@example.test") in log
    assert (
        db_session.scalar(select(Reminder.sent_at).where(Reminder.channel == "email")) is not None
    )


# ====================================================================== default senders


def test_default_senders_use_real_channels_when_configured_and_stubs_otherwise():
    real = default_senders(settings())
    assert isinstance(real["email"], EmailSender) and isinstance(real["push"], PushSender)
    bare = default_senders(
        settings(smtp_host="", vapid_public_key="", vapid_private_key=SecretStr(""))
    )
    assert isinstance(bare["email"], StubSender) and isinstance(bare["push"], StubSender)
    assert set(real) == set(CHANNELS)


# ====================================================================== SMS


def test_sms_is_off_while_sms_enabled_is_false():
    sender = SmsSender(StubSmsProvider(), enabled=False)
    with pytest.raises(ChannelDisabled):
        sender.send(outgoing(channel="sms"))
    assert default_senders(settings())["sms"].enabled is False


def test_enabled_sms_uses_the_provider_interface():
    sent = []
    provider = SimpleNamespace(send_sms=lambda to, text: sent.append((to, text)))
    SmsSender(provider, enabled=True).send(outgoing(channel="sms"))
    assert sent == [(None, "Complete immediately: Quiz")]
    assert default_senders(settings(sms_enabled=True))["sms"].enabled is True


def test_a_stale_sms_reminder_is_dropped_not_sent_or_retried(db_session, user):
    task = make_task(db_session, user, NOW + 6 * H)
    db_session.add(Reminder(task_id=task.id, fire_at=NOW, channel="sms", kind="immediate"))
    db_session.commit()
    senders = {c: StubSender(c) for c in CHANNELS} | {
        "sms": SmsSender(StubSmsProvider(), enabled=False)
    }
    result = dispatch_due(db_session, now=NOW, senders=senders)
    assert (result.sent, result.skipped, result.failed) == (0, 1, 0)
    assert db_session.scalar(select(func.count()).select_from(Reminder)) == 0
    assert db_session.scalar(select(func.count()).select_from(Notification)) == 0


# ====================================================================== web push


class FakePush:
    def __init__(self, outcomes=None):
        self.calls = []
        self.outcomes = outcomes or {}  # endpoint -> status code to fail with

    def __call__(self, **kw):
        self.calls.append(kw)
        status = self.outcomes.get(kw["subscription_info"]["endpoint"])
        if status:
            raise WebPushException("push failed", response=SimpleNamespace(status_code=status))
        return SimpleNamespace(status_code=201)


def add_sub(session, user, n=1) -> str:
    endpoint = f"https://push.example.test/send/{user.id}-{n}"
    session.add(
        PushSubscription(user_id=user.id, endpoint=endpoint, p256dh=f"key{n}", auth=f"auth{n}")
    )
    session.commit()
    return endpoint


def push_sender(engine, fake) -> PushSender:
    return PushSender(
        settings(), lambda: sessionmaker(engine, expire_on_commit=False)(), webpush_fn=fake
    )


def sub_count(session, user=None) -> int:
    stmt = select(func.count()).select_from(PushSubscription)
    if user is not None:
        stmt = stmt.where(PushSubscription.user_id == user.id)
    session.expire_all()
    return session.scalar(stmt)


def test_push_payload_opens_the_action_link_on_click(db_session, user, engine):
    add_sub(db_session, user)
    fake = FakePush()
    push_sender(engine, fake).send(outgoing(user_id=user.id, channel="push", kind="created"))
    call = fake.calls[0]
    import json

    payload = json.loads(call["data"])
    assert payload["url"] == "https://onlinecourses.nptel.ac.in/noc/quiz/5"  # what a click opens
    assert payload["title"] == "Complete immediately: Quiz" and payload["tag"] == "task-7"
    assert call["subscription_info"]["keys"] == {"p256dh": "key1", "auth": "auth1"}
    assert call["vapid_claims"] == {"sub": "mailto:me@example.test"}
    assert call["vapid_private_key"] == "PRIVKEY"


def test_push_payload_falls_back_to_the_app_when_there_is_no_usable_link():
    for link in (None, "javascript:alert(1)"):
        data = payload_for(outgoing(link=link), "https://app.example.test")
        assert '"url": "https://app.example.test"' in data


@pytest.mark.parametrize("status", [410, 404])
def test_a_gone_push_response_deletes_that_subscription(db_session, user, engine, status):
    gone, alive = add_sub(db_session, user, 1), add_sub(db_session, user, 2)
    fake = FakePush({gone: status})
    push_sender(engine, fake).send(outgoing(user_id=user.id, channel="push"))  # no error
    remaining = {s.endpoint for s in db_session.scalars(select(PushSubscription))}
    assert remaining == {alive}
    assert len(fake.calls) == 2


def test_a_410_when_it_is_the_only_subscription_is_not_an_error(db_session, user, engine):
    only = add_sub(db_session, user)
    push_sender(engine, FakePush({only: 410})).send(outgoing(user_id=user.id, channel="push"))
    assert sub_count(db_session) == 0


def test_other_push_errors_keep_the_subscription_and_raise_when_nothing_was_delivered(
    db_session, user, engine
):
    endpoint = add_sub(db_session, user)
    with pytest.raises(RuntimeError):
        push_sender(engine, FakePush({endpoint: 500})).send(
            outgoing(user_id=user.id, channel="push")
        )
    assert sub_count(db_session) == 1


def test_push_succeeds_if_at_least_one_subscription_got_it(db_session, user, engine):
    bad, _good = add_sub(db_session, user, 1), add_sub(db_session, user, 2)
    push_sender(engine, FakePush({bad: 503})).send(outgoing(user_id=user.id, channel="push"))
    assert sub_count(db_session) == 2


def test_a_user_without_subscriptions_is_not_an_error(user, engine):
    fake = FakePush()
    push_sender(engine, fake).send(outgoing(user_id=user.id, channel="push"))
    assert fake.calls == []


def test_push_only_goes_to_the_reminders_owner(db_session, user, engine):
    from app.models import User

    other = User(email="other@example.test")
    db_session.add(other)
    db_session.commit()
    mine, theirs = add_sub(db_session, user), add_sub(db_session, other)
    fake = FakePush()
    push_sender(engine, fake).send(outgoing(user_id=user.id, channel="push"))
    assert [c["subscription_info"]["endpoint"] for c in fake.calls] == [mine]
    assert theirs


def test_a_push_failure_goes_through_the_dispatcher_retry_path(db_session, user, engine):
    endpoint = add_sub(db_session, user)
    task = make_task(db_session, user, NOW + 5 * 24 * H)
    db_session.add(Reminder(task_id=task.id, fire_at=NOW, channel="push", kind="created"))
    db_session.commit()
    senders = {c: StubSender(c) for c in CHANNELS} | {
        "push": push_sender(engine, FakePush({endpoint: 500}))
    }
    assert dispatch_due(db_session, now=NOW, senders=senders).failed == 1
    assert db_session.scalar(select(Reminder.attempts)) == 1


# ====================================================================== push + notification API


SUB = {"endpoint": "https://fcm.example.test/send/abc", "keys": {"p256dh": "BPk", "auth": "au"}}


def test_push_routes_require_login(client_factory):
    c = client_factory()
    assert c.post("/push/subscribe", json=SUB).status_code == 401
    assert (
        c.request("DELETE", "/push/subscribe", json={"endpoint": SUB["endpoint"]}).status_code
        == 401
    )
    assert c.get("/notifications").status_code == 401
    assert c.post("/notifications/read-all").status_code == 401
    assert c.post("/notifications/1/read").status_code == 401


def test_subscribe_stores_and_refreshes_a_subscription(api, db_session, uid):
    first = api.post("/push/subscribe", json=SUB)
    assert first.status_code == 201
    again = api.post(
        "/push/subscribe",
        json={**SUB, "keys": {"p256dh": "NEW", "auth": "NEWAUTH"}, "expirationTime": None},
    )
    assert again.json()["id"] == first.json()["id"]
    rows = db_session.scalars(select(PushSubscription)).all()
    assert len(rows) == 1 and (rows[0].p256dh, rows[0].auth, rows[0].user_id) == (
        "NEW",
        "NEWAUTH",
        uid,
    )


def test_a_browser_resubscribing_under_another_account_moves_to_that_account(
    api, api_b, db_session, uid_b
):
    api.post("/push/subscribe", json=SUB)
    api_b.post("/push/subscribe", json=SUB)
    row = db_session.scalar(select(PushSubscription))
    assert (
        row.user_id == uid_b
        and db_session.scalar(select(func.count()).select_from(PushSubscription)) == 1
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"endpoint": "https://x.example.test"},
        {"endpoint": "http://insecure.example.test/x", "keys": {"p256dh": "a", "auth": "b"}},
        {"endpoint": "javascript:alert(1)", "keys": {"p256dh": "a", "auth": "b"}},
        {"endpoint": "not a url", "keys": {"p256dh": "a", "auth": "b"}},
        {"endpoint": "https://x.example.test/a", "keys": {"p256dh": "", "auth": "b"}},
        {"endpoint": "https://x.example.test/a", "keys": {"p256dh": "a"}},
        {"endpoint": "https://x.example.test/" + "a" * 2000, "keys": {"p256dh": "a", "auth": "b"}},
    ],
)
def test_subscribe_validation_errors(api, db_session, payload):
    assert api.post("/push/subscribe", json=payload).status_code == 422
    assert db_session.scalar(select(func.count()).select_from(PushSubscription)) == 0


def test_unsubscribe_deletes_only_your_own_subscription(api, api_b, db_session):
    api.post("/push/subscribe", json=SUB)
    body = {"endpoint": SUB["endpoint"]}
    assert api_b.request("DELETE", "/push/subscribe", json=body).status_code == 404
    assert db_session.scalar(select(func.count()).select_from(PushSubscription)) == 1
    assert api.request("DELETE", "/push/subscribe", json=body).status_code == 204
    assert api.request("DELETE", "/push/subscribe", json=body).status_code == 404
    assert api.request("DELETE", "/push/subscribe", json={"endpoint": "ftp://x"}).status_code == 422


def test_public_key_route(client_factory, monkeypatch):
    c = client_factory()
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "")
    get_settings.cache_clear()
    assert c.get("/push/public-key").status_code == 503
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "BPublicKey")
    get_settings.cache_clear()
    assert c.get("/push/public-key").json() == {"public_key": "BPublicKey"}


def add_note(session, uid, title, *, read=False, minutes=0, kind="reminder") -> int:
    note = Notification(
        user_id=uid, title=title, read=read, kind=kind,
        created_at=datetime(2030, 1, 1, tzinfo=UTC) + timedelta(minutes=minutes),
    )  # fmt: skip
    session.add(note)
    session.commit()
    return note.id


def test_notifications_list_unread_first_then_newest(api, db_session, uid):
    add_note(db_session, uid, "old unread", minutes=1)
    add_note(db_session, uid, "read newest", read=True, minutes=9)
    add_note(db_session, uid, "new unread", minutes=5)
    body = api.get("/notifications").json()
    assert [n["title"] for n in body["items"]] == ["new unread", "old unread", "read newest"]
    assert body["unread_count"] == 2
    assert [n["title"] for n in api.get("/notifications?unread_only=true").json()["items"]] == [
        "new unread", "old unread",
    ]  # fmt: skip
    assert len(api.get("/notifications?limit=1").json()["items"]) == 1
    assert api.get("/notifications?limit=0").status_code == 422


def test_mark_one_read_and_404(api, api_b, db_session, uid):
    note_id = add_note(db_session, uid, "n")
    assert api_b.post(f"/notifications/{note_id}/read").status_code == 404  # not theirs
    assert api.get("/notifications").json()["unread_count"] == 1
    resp = api.post(f"/notifications/{note_id}/read")
    assert resp.status_code == 200 and resp.json()["read"] is True
    assert api.get("/notifications").json()["unread_count"] == 0
    assert api.post("/notifications/999999/read").status_code == 404


def test_read_all_only_touches_the_session_users_notifications(api, api_b, db_session, uid, uid_b):
    add_note(db_session, uid, "a1")
    add_note(db_session, uid, "a2")
    add_note(db_session, uid, "a3", read=True)
    add_note(db_session, uid_b, "b1")
    assert api.post("/notifications/read-all").json() == {"updated": 2}
    assert api.get("/notifications").json()["unread_count"] == 0
    assert api_b.get("/notifications").json()["unread_count"] == 1
    assert api.post("/notifications/read-all").json() == {"updated": 0}


def test_notifications_carry_the_action_link(api, db_session, uid):
    db_session.add(Notification(user_id=uid, title="t", link="https://example.com/go"))
    db_session.commit()
    item = api.get("/notifications").json()["items"][0]
    assert item["link"] == "https://example.com/go" and item["read"] is False


# ====================================================================== calendar


class FakeCalendar:
    def __init__(self, fail=False, gone=()):
        self.events: dict[str, object] = {}
        self.calls: list[tuple] = []
        self.created: list = []
        self.updated: list = []
        self.deleted: list = []
        self.fail, self.gone = fail, set(gone)
        self._n = 0

    def _check(self):
        if self.fail:
            raise RuntimeError("google is down")

    def create_event(self, spec):
        self._check()
        self._n += 1
        event_id = f"evt-{self._n}"
        self.events[event_id] = spec
        self.created.append(spec)
        self.calls.append(("create", event_id))
        return event_id

    def update_event(self, event_id, spec):
        from app.notify.calendar import EventGone

        self._check()
        if event_id in self.gone or event_id not in self.events:
            raise EventGone(event_id)
        self.events[event_id] = spec
        self.updated.append((event_id, spec))
        self.calls.append(("update", event_id))

    def delete_event(self, event_id):
        self._check()
        self.events.pop(event_id, None)
        self.deleted.append(event_id)
        self.calls.append(("delete", event_id))


@pytest.fixture
def cal():
    from app.main import app
    from app.notify.calendar import get_calendar_api

    fake = FakeCalendar()
    app.dependency_overrides[get_calendar_api] = lambda: fake
    return fake


def task_row(session, task_id) -> Task:
    session.expire_all()
    return session.get(Task, task_id)


def test_event_body_has_the_link_the_deadline_and_popup_reminders():
    from app.notify.calendar import EventSpec, event_body

    body = event_body(
        EventSpec("Quiz 5", datetime(2030, 1, 22, 18, 29, tzinfo=UTC), "https://forms.gle/abc")
    )
    assert body["summary"] == "Quiz 5"
    assert "https://forms.gle/abc" in body["description"] and "forms.gle" in body["description"]
    assert body["start"] == {"dateTime": "2030-01-22T23:59:00+05:30", "timeZone": "Asia/Kolkata"}
    assert body["end"]["dateTime"] == "2030-01-23T00:14:00+05:30"
    assert body["reminders"] == {
        "useDefault": False,
        "overrides": [{"method": "popup", "minutes": 1440}, {"method": "popup", "minutes": 600}],
    }
    assert body["source"]["url"] == "https://forms.gle/abc"


def test_event_body_without_a_usable_link():
    from app.notify.calendar import EventSpec, event_body

    for link in (None, "javascript:alert(1)"):
        body = event_body(EventSpec("t", NOW, link))
        assert "source" not in body and "javascript" not in body["description"]


def test_creating_a_task_creates_the_calendar_event_with_the_link(api, cal, db_session):
    resp = api.post(
        "/tasks",
        json={
            "title": "Quiz",
            "deadline": "2030-01-22T23:59:00+05:30",
            "link": "https://forms.gle/abc",
        },
    )
    assert resp.status_code == 201
    assert len(cal.created) == 1
    spec = cal.created[0]
    assert (spec.title, spec.link) == ("Quiz", "https://forms.gle/abc")
    assert spec.deadline == datetime(2030, 1, 22, 18, 29, tzinfo=UTC)
    task = task_row(db_session, resp.json()["id"])
    assert task.gcal_event_id == "evt-1" and task.gcal_synced_at is not None


def test_a_task_without_a_deadline_gets_no_event(api, cal):
    api.post("/tasks", json={"title": "Someday"})
    assert cal.calls == []


def test_editing_a_task_updates_the_event(api, cal, db_session):
    task_id = api.post(
        "/tasks", json={"title": "Quiz", "deadline": "2030-01-22T23:59:00+05:30"}
    ).json()["id"]
    api.patch(f"/tasks/{task_id}", json={"title": "Quiz (renamed)"})
    api.patch(f"/tasks/{task_id}", json={"deadline": "2030-01-25T10:00:00+05:30"})
    api.patch(f"/tasks/{task_id}", json={"link": "https://example.com/new"})
    assert [c[0] for c in cal.calls] == ["create", "update", "update", "update"]
    last = cal.updated[-1][1]
    assert (last.title, last.link) == ("Quiz (renamed)", "https://example.com/new")
    assert last.deadline == datetime(2030, 1, 25, 4, 30, tzinfo=UTC)
    assert len(cal.events) == 1  # still one event


@pytest.mark.parametrize("how", ["done", "dismiss", "status", "no_deadline"])
def test_done_dismissed_or_deadline_removed_deletes_the_event(api, cal, db_session, how):
    task_id = api.post(
        "/tasks", json={"title": "Quiz", "deadline": "2030-01-22T23:59:00+05:30"}
    ).json()["id"]
    if how == "status":
        api.patch(f"/tasks/{task_id}", json={"status": "done"})
    elif how == "no_deadline":
        api.patch(f"/tasks/{task_id}", json={"deadline": None})
    else:
        api.post(f"/tasks/{task_id}/{how}")
    assert cal.deleted == ["evt-1"] and cal.events == {}
    assert task_row(db_session, task_id).gcal_event_id is None


def test_reopening_a_done_task_creates_a_fresh_event(api, cal):
    task_id = api.post(
        "/tasks", json={"title": "Quiz", "deadline": "2030-01-22T23:59:00+05:30"}
    ).json()["id"]
    api.post(f"/tasks/{task_id}/done")
    api.patch(f"/tasks/{task_id}", json={"status": "open"})
    assert [c[0] for c in cal.calls] == ["create", "delete", "create"] and len(cal.events) == 1


def test_deleting_a_task_deletes_its_event(api, cal, db_session):
    task_id = api.post(
        "/tasks", json={"title": "Quiz", "deadline": "2030-01-22T23:59:00+05:30"}
    ).json()["id"]
    assert api.delete(f"/tasks/{task_id}").status_code == 204
    assert cal.deleted == ["evt-1"] and cal.events == {}


def test_an_event_deleted_in_google_is_recreated_on_the_next_edit(api, cal, db_session):
    task_id = api.post(
        "/tasks", json={"title": "Quiz", "deadline": "2030-01-22T23:59:00+05:30"}
    ).json()["id"]
    cal.events.clear()  # the user deleted it in Google Calendar
    api.patch(f"/tasks/{task_id}", json={"title": "Quiz!"})
    assert task_row(db_session, task_id).gcal_event_id == "evt-2" and len(cal.events) == 1


def test_a_google_outage_never_fails_the_task_request(api, cal, db_session):
    cal.fail = True
    resp = api.post("/tasks", json={"title": "Quiz", "deadline": "2030-01-22T23:59:00+05:30"})
    assert resp.status_code == 201
    assert task_row(db_session, resp.json()["id"]).gcal_event_id is None
    assert api.patch(f"/tasks/{resp.json()['id']}", json={"title": "x"}).status_code == 200
    assert api.post(f"/tasks/{resp.json()['id']}/done").status_code == 200


def test_review_confirm_creates_the_event_and_review_tasks_have_none(
    api, cal, engine, db_session, uid
):
    task_id = task_in_db(
        engine, uid, status="needs_review", deadline=datetime(2030, 1, 22, tzinfo=UTC)
    )
    assert cal.calls == []
    api.post(f"/review/{task_id}/confirm")
    assert [c[0] for c in cal.calls] == ["create"]


def make_user_with_token(engine, email) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO users (email, encrypted_refresh_token) VALUES (:e, 'x') RETURNING id"
            ),
            {"e": email},
        ).scalar_one()


def test_reconcile_repairs_what_a_google_outage_missed(api, cal, engine, db_session):
    from app.notify.calendar import reconcile_calendar

    uid = make_user_with_token(engine, "token@example.test")
    never_synced = task_in_db(engine, uid, title="A", deadline=datetime(2030, 1, 22, tzinfo=UTC))
    task_in_db(engine, uid, title="B", deadline=datetime(2030, 1, 23, tzinfo=UTC),
                         gcal_event_id="evt-old", gcal_synced_at=datetime(2020, 1, 1, tzinfo=UTC))  # fmt: skip
    leftover = task_in_db(engine, uid, title="C", status="done", deadline=datetime(2030, 1, 24, tzinfo=UTC),
                          gcal_event_id="evt-left", gcal_synced_at=datetime(2030, 1, 1, tzinfo=UTC))  # fmt: skip
    synced = task_in_db(engine, uid, title="D", deadline=datetime(2030, 1, 25, tzinfo=UTC),
                        gcal_event_id="evt-ok", gcal_synced_at=datetime(2999, 1, 1, tzinfo=UTC))  # fmt: skip
    cal.events["evt-old"] = None
    factory = sessionmaker(engine, expire_on_commit=False)

    counts = reconcile_calendar(factory, lambda user, session: cal)

    assert counts["synced"] == 3 and counts["failed"] == 0
    assert task_row(db_session, never_synced).gcal_event_id is not None
    assert "evt-old" in {c[1] for c in cal.calls if c[0] == "update"}
    assert task_row(db_session, leftover).gcal_event_id is None and "evt-left" in cal.deleted
    assert task_row(db_session, synced).gcal_event_id == "evt-ok"  # untouched
    again = reconcile_calendar(factory, lambda user, session: cal)
    assert again["synced"] == 0  # nothing left to do


def test_a_failed_delete_of_a_removed_task_is_retried_by_reconcile(api, cal, engine, db_session):
    from app.models import CalendarOrphan
    from app.notify.calendar import reconcile_calendar

    uid = make_user_with_token(engine, "orphan@example.test")
    db_session.expire_all()
    from app.auth.deps import get_current_user
    from app.main import app
    from app.models import User

    user = db_session.get(User, uid)
    app.dependency_overrides[get_current_user] = lambda: user
    task_id = task_in_db(
        engine, uid, gcal_event_id="evt-doomed", deadline=datetime(2030, 1, 22, tzinfo=UTC)
    )
    cal.fail = True
    try:
        assert api.delete(f"/tasks/{task_id}").status_code == 204
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert [o.event_id for o in db_session.scalars(select(CalendarOrphan))] == ["evt-doomed"]

    cal.fail = False
    counts = reconcile_calendar(sessionmaker(engine, expire_on_commit=False), lambda u, s: cal)
    assert counts["orphans_deleted"] == 1 and cal.deleted == ["evt-doomed"]
    assert db_session.scalar(select(func.count()).select_from(CalendarOrphan)) == 0


def test_reconcile_stops_for_a_user_whose_google_calls_fail_and_never_raises(engine):
    from app.notify.calendar import reconcile_calendar

    uid = make_user_with_token(engine, "flaky@example.test")
    for n in range(3):
        task_in_db(engine, uid, title=f"T{n}", deadline=datetime(2030, 1, 22 + n, tzinfo=UTC))
    broken = FakeCalendar(fail=True)
    counts = reconcile_calendar(sessionmaker(engine, expire_on_commit=False), lambda u, s: broken)
    assert counts["failed"] == 1 and counts["synced"] == 0 and broken.calls == []


def test_calendar_for_is_off_without_a_token_or_when_disabled(db_session, user, monkeypatch):
    from app.notify.calendar import LazyCalendar, calendar_for

    assert calendar_for(user, db_session) is None  # conftest disables it, and there is no token
    monkeypatch.setenv("CALENDAR_ENABLED", "true")
    get_settings.cache_clear()
    assert calendar_for(user, db_session) is None  # still no Google token
    user.encrypted_refresh_token = "x"
    assert isinstance(calendar_for(user, db_session), LazyCalendar)
    user.needs_reauth = True
    assert calendar_for(user, db_session) is None


class FakeRequest:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


def http_error(status):
    from googleapiclient.errors import HttpError

    return HttpError(SimpleNamespace(status=status, reason="x"), b"{}")


def test_google_calendar_api_maps_missing_events_and_retries_server_errors():
    from tenacity import wait_none

    from app.notify.calendar import EventGone, EventSpec, GoogleCalendarApi

    attempts = {"insert": 0}

    def insert():
        attempts["insert"] += 1
        if attempts["insert"] < 3:
            raise http_error(503)
        return {"id": "evt-9"}

    def missing():
        raise http_error(404)

    events = SimpleNamespace(
        insert=lambda calendarId, body: FakeRequest(insert),
        patch=lambda calendarId, eventId, body: FakeRequest(missing),
        delete=lambda calendarId, eventId: FakeRequest(missing),
    )
    api = GoogleCalendarApi(SimpleNamespace(events=lambda: events), wait=wait_none())
    spec = EventSpec("t", NOW, None)
    assert api.create_event(spec) == "evt-9" and attempts["insert"] == 3
    with pytest.raises(EventGone):
        api.update_event("gone", spec)
    api.delete_event("gone")  # already gone is fine


def test_google_calendar_api_surfaces_real_errors():
    from googleapiclient.errors import HttpError
    from tenacity import wait_none

    from app.notify.calendar import EventSpec, GoogleCalendarApi

    def forbidden():
        raise http_error(403)

    events = SimpleNamespace(
        patch=lambda calendarId, eventId, body: FakeRequest(forbidden),
        delete=lambda calendarId, eventId: FakeRequest(forbidden),
    )
    api = GoogleCalendarApi(SimpleNamespace(events=lambda: events), wait=wait_none())
    with pytest.raises(HttpError):
        api.update_event("e", EventSpec("t", NOW, None))
    with pytest.raises(HttpError):
        api.delete_event("e")


# ====================================================================== dev push test page


def test_the_push_test_page_is_404_when_the_flag_is_off(client_factory):
    c = client_factory()
    assert get_settings().dev_push_test_page is False
    assert c.get("/dev/push-test").status_code == 404
    assert c.get("/dev/sw.js").status_code == 404


def test_the_push_test_page_is_served_when_the_flag_is_on(client_factory, monkeypatch):
    monkeypatch.setenv("DEV_PUSH_TEST_PAGE", "true")
    get_settings.cache_clear()
    c = client_factory()
    page = c.get("/dev/push-test")
    assert page.status_code == 200 and "/push/subscribe" in page.text and "/dev/sw.js" in page.text
    worker = c.get("/dev/sw.js")
    assert worker.status_code == 200 and "javascript" in worker.headers["content-type"]
    assert "notificationclick" in worker.text and "openWindow" in worker.text
    monkeypatch.setenv("DEV_PUSH_TEST_PAGE", "false")
    get_settings.cache_clear()
    assert c.get("/dev/push-test").status_code == 404  # flag is checked on every request


def test_the_new_routes_are_in_the_api_docs(client_factory):
    spec = client_factory().get("/openapi.json").json()
    listed = {(m.upper(), p) for p, item in spec["paths"].items() for m in item}
    for route in (
        ("GET", "/notifications"), ("POST", "/notifications/{notification_id}/read"),
        ("POST", "/notifications/read-all"), ("POST", "/push/subscribe"),
        ("DELETE", "/push/subscribe"), ("GET", "/push/public-key"),
        ("GET", "/action/done"), ("POST", "/action/done"),
    ):  # fmt: skip
        assert route in listed
    assert not any(p.startswith("/dev/") for p in spec["paths"])  # the test page is not advertised


def test_config_defaults_are_safe():
    s = Settings(_env_file=None)
    assert s.public_base_url == "http://localhost:8000"
    assert s.dev_push_test_page is False and s.sms_enabled is False and s.scheduler_enabled is False
    assert s.smtp_host == "" and s.smtp_security == "starttls"


# ====================================================================== dashboard additions


def add_quarantine_row(engine, uid, message_id="q1", status="held") -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO quarantine (user_id, message_id, subject, sender, score, reasons, status) "
                "VALUES (:u, :m, 's', 'x@example.test', 6, '[\"r\"]'::jsonb, :s) RETURNING id"
            ),
            {"u": uid, "m": message_id, "s": status},
        ).scalar_one()


def test_confirm_scam_closes_the_quarantine_item(api, api_b, engine, uid):
    qid = add_quarantine_row(engine, uid)
    assert api_b.post(f"/quarantine/{qid}/confirm-scam").status_code == 404  # not theirs
    resp = api.post(f"/quarantine/{qid}/confirm-scam")
    assert resp.status_code == 200 and resp.json()["status"] == "confirmed_scam"
    assert api.get("/quarantine").json() == []  # no longer held
    assert [r["id"] for r in api.get("/quarantine?status=confirmed_scam").json()] == [qid]
    assert api.post(f"/quarantine/{qid}/confirm-scam").status_code == 409
    from app.api.sync import get_gmail_api
    from app.main import app
    from tests.gmail_fakes import FakeGmailApi

    app.dependency_overrides[get_gmail_api] = lambda: FakeGmailApi({})
    assert api.post(f"/quarantine/{qid}/rescue").status_code == 409  # already closed
    assert api.post("/quarantine/999999/confirm-scam").status_code == 404


def test_confirm_scam_requires_login(client_factory):
    assert client_factory().post("/quarantine/1/confirm-scam").status_code == 401


class FakeSender:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send(self, message):
        if self.fail:
            raise RuntimeError("push service down")
        self.sent.append(message)


def use_push_sender(sender):
    from app.api.push import get_push_sender
    from app.main import app

    app.dependency_overrides[get_push_sender] = lambda: sender


def test_test_push_needs_login_configuration_and_a_subscription(
    client_factory, api, db_session, uid
):
    assert client_factory().post("/push/test").status_code == 401
    use_push_sender(None)
    assert api.post("/push/test").status_code == 503  # Web Push not configured
    use_push_sender(FakeSender())
    assert api.post("/push/test").status_code == 409  # nothing subscribed yet
    assert api.get("/notifications").json()["items"] == []


def test_test_push_sends_to_the_user_and_adds_a_bell_entry(api, api_b, db_session, uid):
    api.post("/push/subscribe", json=SUB)
    fake = FakeSender()
    use_push_sender(fake)
    resp = api.post("/push/test")
    assert resp.status_code == 200 and resp.json() == {"subscriptions": 1}
    assert [(m.user_id, m.title, m.link) for m in fake.sent] == [
        (uid, "Test notification", get_settings().frontend_url)
    ]
    items = api.get("/notifications").json()["items"]
    assert [n["title"] for n in items] == ["Test notification"]
    assert api_b.get("/notifications").json()["items"] == []  # only the caller's bell


def test_test_push_reports_a_failing_push_service(api):
    api.post("/push/subscribe", json=SUB)
    use_push_sender(FakeSender(fail=True))
    assert api.post("/push/test").status_code == 502
    assert api.get("/notifications").json()["items"] == []  # nothing claimed as delivered


def test_the_frontend_url_defaults_to_the_local_dashboard():
    assert Settings(_env_file=None).frontend_url == "http://localhost:3000"
