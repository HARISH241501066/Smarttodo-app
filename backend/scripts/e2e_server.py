# ruff: noqa: E501, I001  (long literals; imports follow the sys.path set-up on purpose)
"""A throwaway backend for the dashboard's browser tests. It never touches real data:

* creates a temporary Postgres database and migrates it,
* seeds one test user with tasks, notifications and quarantined/needs-review items,
* fakes Gmail (for "Rescue") and Web Push (for "Send test notification"),
* writes a signed session cookie for that user to frontend/e2e/.state.json,
* serves the real FastAPI app on E2E_BACKEND_PORT (default 8001),
* drops the database on exit (and any left behind by a run that was killed).

    backend\\.venv\\Scripts\\python.exe backend\\scripts\\e2e_server.py
"""

import json
import os
import signal
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from cryptography.fernet import Fernet  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import base64  # noqa: E402

from app.config import get_settings  # noqa: E402

PORT = int(os.environ.get("E2E_BACKEND_PORT", "8001"))
FRONTEND_PORT = int(os.environ.get("E2E_FRONTEND_PORT", "3100"))
STATE_FILE = Path(os.environ.get("E2E_STATE", BACKEND.parent / "frontend" / "e2e" / ".state.json"))
DB_PREFIX = "smart_todo_e2e_"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def admin_exec(admin, statement: str) -> None:
    """CREATE/DROP DATABASE, retrying while a background Postgres process holds the database."""
    for attempt in range(20):
        try:
            with admin.connect() as conn:
                conn.execute(text(statement))
            return
        except DBAPIError as exc:
            transient = "permission denied to terminate" in str(exc) or "other users" in str(exc)
            if not transient or attempt == 19:
                raise
            time.sleep(0.5)


def main() -> None:
    base = make_url(get_settings().database_url)  # the real settings, only to reach Postgres
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        stale = (
            conn.execute(
                text("SELECT datname FROM pg_database WHERE datname LIKE :p"),
                {"p": DB_PREFIX + "%"},
            )
            .scalars()
            .all()
        )
    for name in stale:  # left behind by a run that was killed
        admin_exec(admin, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')

    name = DB_PREFIX + uuid.uuid4().hex[:10]
    admin_exec(admin, f'CREATE DATABASE "{name}"')
    url = base.set(database=name).render_as_string(hide_password=False)

    key = ec.generate_private_key(ec.SECP256R1())
    vapid_public = b64url(
        key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )
    vapid_private = b64url(key.private_numbers().private_value.to_bytes(32, "big"))
    os.environ.update(
        DATABASE_URL=url,
        SESSION_SECRET="e2e-session-secret-" + "x" * 40,
        FERNET_KEY=Fernet.generate_key().decode(),
        GOOGLE_CLIENT_ID="e2e.apps.googleusercontent.com",
        GOOGLE_CLIENT_SECRET="e2e-secret",
        COOKIE_SECURE="false",
        SCHEDULER_ENABLED="false",
        CALENDAR_ENABLED="false",
        SMS_ENABLED="false",
        SMTP_HOST="",
        VAPID_PUBLIC_KEY=vapid_public,
        VAPID_PRIVATE_KEY=vapid_private,
        VAPID_SUBJECT="mailto:e2e@example.test",
        FRONTEND_URL=f"http://localhost:{FRONTEND_PORT}",
        PUBLIC_BASE_URL=f"http://localhost:{FRONTEND_PORT}",
    )
    get_settings.cache_clear()

    def cleanup(*_):
        try:
            admin_exec(admin, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            os._exit(0)

    signal.signal(signal.SIGTERM, cleanup)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, cleanup)

    try:
        from alembic import command
        from tests.helpers import alembic_cfg

        command.upgrade(alembic_cfg(url), "head")
        state = seed(url)
        serve(state)
    finally:
        admin_exec(admin, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.dispose()


def seed(url: str) -> dict:
    from app.models import Notification, Quarantine, Task, User, UserSettings
    from app.security import sign

    now = datetime.now(UTC)
    engine = create_engine(url)
    ids: dict[str, int] = {}
    with sessionmaker(engine, expire_on_commit=False)() as s:
        user = User(email="e2e@example.test")
        s.add(user)
        s.flush()
        s.add(
            UserSettings(
                user_id=user.id,
                sources=[
                    {"name": "NPTEL", "type": "nptel", "senders": ["nptel.ac.in"]},
                    {"name": "Digii", "type": "digii", "senders": ["digii.example"]},
                    {"name": "Internship", "type": "internship", "senders": []},
                ],
            )
        )

        def task(key, title, **kw) -> Task:
            t = Task(user_id=user.id, title=title, status="open", **kw)
            s.add(t)
            s.flush()
            ids[key] = t.id
            return t

        task("nptel", "Submit NPTEL Assignment 5", source="nptel", source_name="NPTEL", priority="urgent",
             deadline=now + timedelta(hours=30), confidence=0.9,
             link="https://onlinecourses.nptel.ac.in/noc26_cs10/assignment/5",
             link_type="action", link_confidence=0.83, source_ref="src03")  # fmt: skip
        task("form", "Fill the hackathon registration form", source="gform", priority="important",
             deadline=now + timedelta(days=5), confidence=0.9, link="https://forms.gle/Hackathon26",
             link_type="action", link_confidence=0.9, source_ref="seed-form")  # fmt: skip
        task("circular", "Read the exam circular", source="college", priority="regular",
             deadline=now + timedelta(days=10), confidence=0.9,
             link="https://mail.google.com/mail/u/0/#all/seedcircular", link_type="gmail",
             link_confidence=0.0, source_ref="seedcircular")  # fmt: skip
        task("digii", "Digii quiz without a date", source="digii", source_name="Digii",
             priority="important", link="https://mail.google.com/mail/u/0/#all/seeddigii",
             link_type="gmail", link_confidence=0.0, source_ref="seeddigii")  # fmt: skip
        # Email text is attacker-controlled: it must be shown as plain text, never as markup.
        task("xss", "<script>window.__xss = 1</script><b>bold</b> hostile subject", source="nptel",
             source_name="NPTEL", priority="regular", confidence=0.9, source_ref="seed-xss",
             link="https://mail.google.com/mail/u/0/#all/seedxss", link_type="gmail",
             link_confidence=0.0)  # fmt: skip
        task("edit", "Edit me", source="manual", priority="regular", confidence=1.0,
             user_confirmed=True)  # fmt: skip
        task("delete", "Delete me", source="manual", priority="regular", confidence=1.0,
             user_confirmed=True, link="https://example.com/delete-me", link_type="action", link_confidence=1.0)  # fmt: skip
        task("done", "Mark me done", source="manual", priority="regular", confidence=1.0,
             user_confirmed=True, link="https://example.com/done-me", link_type="action", link_confidence=1.0)  # fmt: skip

        def review(key, title, sender=None, ref=None, **kw) -> None:
            ref = ref or key
            t = task(key, title, source="other", priority="regular", confidence=0.3,
                     link="https://mail.google.com/mail/u/0/#all/" + ref, link_type="gmail",
                     link_confidence=0.0, source_ref=ref, sender_email=sender, **kw)  # fmt: skip
            t.status = "needs_review"

        review("review_confirm", "Workshop invite (confirm me)", "events@college.example.edu")
        review("review_edit", "Seminar notice (edit me)", "events@college.example.edu")
        review("review_dismiss", "Newsletter digest (dismiss me)", "events@college.example.edu")
        review(
            "review_stays",
            "Two different deadlines (stays in review)",
            "office@college.example.edu",
        )
        # a sender with several items: "Confirm all from this sender"
        for i in range(1, 4):
            review(
                f"bulk_confirm_{i}", f"Course notice {i} (bulk confirm)", "bulk-a@news.example.test"
            )
        # another one: "Dismiss all from this sender"
        for i in range(1, 3):
            review(f"bulk_dismiss_{i}", f"Promo {i} (bulk dismiss)", "bulk-b@promo.example.test")

        reasons = ["Asks for a registration, processing or similar fee",
                   "Display name claims to be nptel but the sender domain differs"]  # fmt: skip
        for key, mid, subject in (
            ("quarantine_rescue", "task01", "NPTEL certificate fee - pay now"),
            ("quarantine_scam", "scam02", "You are selected without interview"),
        ):
            q = Quarantine(user_id=user.id, message_id=mid, subject=subject,
                           sender="NPTEL Team <nptel.help@gmail.com>", score=7, reasons=reasons)  # fmt: skip
            s.add(q)
            s.flush()
            ids[key] = q.id

        for key, title, body, task_key, minutes, read in (
            (
                "n1",
                "Complete immediately: Submit NPTEL Assignment 5",
                "Due soon",
                "nptel",
                5,
                False,
            ),
            (
                "n2",
                "New task: Fill the hackathon registration form",
                "Due in 5 days",
                "form",
                10,
                False,
            ),
            ("n3", "Daily digest: 2 task(s) due in the next 72 hours", "- a\n- b", None, 20, False),
            ("n4", "Old reminder", "Already seen", "circular", 600, True),
        ):
            n = Notification(
                user_id=user.id, task_id=ids.get(task_key), title=title, body=body, read=read,
                kind="digest" if task_key is None else "reminder",
                created_at=now - timedelta(minutes=minutes),
            )  # fmt: skip
            s.add(n)
            s.flush()
            ids[key] = n.id
        s.commit()
        uid = user.id
    engine.dispose()

    state = {
        "cookie": sign({"uid": uid}, "session"),
        "backend": f"http://127.0.0.1:{PORT}",
        "email": "e2e@example.test",
        "ids": ids,
        "vapid_public": os.environ["VAPID_PUBLIC_KEY"],
    }
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))
    return state


class RecordingSender:
    """Stands in for Web Push so "Send test notification" can be checked without a push service."""

    sent: list[dict] = []

    def send(self, message) -> None:
        self.sent.append({"title": message.title, "body": message.body, "link": message.link})


def serve(state: dict) -> None:
    import uvicorn
    from fastapi import APIRouter

    from app.api.push import get_push_sender
    from app.api.sync import get_gmail_api
    from app.main import app
    from tests.gmail_fakes import FIXTURE_DIR as EMAIL_FIXTURES, FakeGmailApi, load_fixture

    FIXTURE_DIR = EMAIL_FIXTURES.parent

    raw = load_fixture("../task_emails/01_nptel_quiz_with_noise_links")
    raw["payload"]["headers"].append(
        {"name": "Authentication-Results", "value": "mx.google.com; dmarc=pass"}
    )
    messages = {raw["id"]: raw}
    manifest = json.loads((FIXTURE_DIR / "source_emails" / "expected.manifest").read_text())
    for name, meta in manifest.items():  # what "Rebuild from sources" reads
        messages[meta["id"]] = json.loads(
            (FIXTURE_DIR / "source_emails" / f"{name}.json").read_text()
        )
    fake_gmail = FakeGmailApi(messages)
    sender = RecordingSender()
    app.dependency_overrides[get_gmail_api] = lambda: fake_gmail
    app.dependency_overrides[get_push_sender] = lambda: sender

    harness = APIRouter(prefix="/__e2e", include_in_schema=False)

    @harness.get("/pushes")
    def pushes() -> list[dict]:
        return sender.sent

    app.include_router(harness)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
