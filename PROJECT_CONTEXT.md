# PROJECT_CONTEXT.md

## Project
Smart Inbox To-Do. The user signs in with Google. The backend scans Gmail, filters out promotions, LinkedIn noise, spam and scam emails, and turns genuinely actionable emails (NPTEL, internships, Google Forms, college mail, and any other real to-do) into tasks with extracted deadlines and priorities (urgent / important / regular). Reminders go out via in-app notifications, Web Push, email and Google Calendar (SMS optional, off by default). Suspected scams go to a "Suspicious" queue. Low-confidence items go to "Needs review". Users can also add, edit and delete tasks manually.

## Tech stack (do not substitute without asking)
- Backend: Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic, Pydantic v2
- DB: PostgreSQL 16+ (local install; no Docker required. Dev currently runs PostgreSQL 18 on port 5433)
- Google: google-auth, google-auth-oauthlib, google-api-python-client (Gmail, Calendar), Cloud Pub/Sub
- Parsing: dateparser, beautifulsoup4, rapidfuzz
- Scheduler: APScheduler with a Postgres job store
- Notify: in-app (notifications table), Web Push (pywebpush + VAPID), email (SMTP, pluggable sender), optional SMS plug-in, Google Calendar API
- Security: cryptography (Fernet / MultiFernet)
- Retries: tenacity
- Tests: pytest, pytest-asyncio, freezegun, respx
- Lint: ruff
- Frontend: Next.js (App Router, TypeScript, Tailwind); installable PWA (manifest + service worker)
- Time: store UTC (TIMESTAMPTZ); display Asia/Kolkata

## Notification channels
- In-app: `notifications` table + notification bell in the dashboard.
- Web Push: browser push via service worker + VAPID keys (pywebpush on the backend). The frontend is an installable PWA.
- Email: escalation emails via SMTP. The sender is pluggable; the provider is set in .env.
- SMS: optional plug-in interface, disabled by default (SMS_ENABLED=false). SMS to Indian numbers generally requires DLT registration.
- Google Calendar: one event per task, with the action link in the description.
- Telegram is NOT part of the project.

## Action link
Every task has an action link: the direct URL to complete the task (NPTEL quiz page, Google Form, portal page). Fallback: an "Open in Gmail" link built from the message ID.

## Reminder ladder
- On task creation: in-app notification + push + Calendar event
- T-48h: push
- T-24h: EMAIL "Complete immediately" with the action link
- T-10h: final EMAIL with the action link
- T-1h: push
- Offsets that are already past are skipped. Marking a task done cancels all pending reminders.

## Module notes
- Module 11 (dashboard): notification bell; an "Open & Complete" button on each task card showing the link's domain; push opt-in; PWA manifest + service worker.
- Module 12 (monitoring): health alerts go by email.

## Repo structure
/backend/app: main.py, config.py, db.py, security.py, /models, /schemas, /api, /auth, /ingest, /filters, /scheduler, /notify, /monitoring
/backend/alembic, /backend/tests (/fixtures/emails — synthetic only)
/frontend (Next.js)
.env.example, README.md (docker-compose.yml is optional, not part of any gate)

## Rules for every module
1. Read PROJECT_CONTEXT.md and PROGRESS.md before starting.
2. Only work on the module you are given. Never start the next module.
3. Plan first: list the files you will create or change and the tests you will write. Then implement.
4. Gate check at the end: run every "Done when" item and report in this exact format:
   ---- GATE CHECK: MODULE <N> ----
   [PASS/FAIL] <check> — <evidence: command output / test name / screenshot>
   RESULT: ALL PASS  or  BLOCKED — <cause, fix attempted>
5. If any check fails: fix it and re-run the full gate. After 3 failed attempts, stop and ask me.
6. Never mark PASS without real evidence. Never weaken, skip or delete tests to pass.
7. Manual steps (OAuth client, tokens, GCP setup): stop, tell me exactly what to do, and wait.
8. Secrets live only in .env. Keep .env.example updated. .env, client_secret*.json and token files must be gitignored.
9. Privacy: all test fixtures are synthetic. Never log or commit email bodies. Log message IDs and decisions only.
10. If you change earlier-module code, re-run that module's tests and say what changed.
11. When the gate passes, append to PROGRESS.md: "Module <N>: PASS — <date> — <tests count> — <notes>".

## Change log
- 2026-10-11: Telegram removed. Added in-app, Web Push (PWA), email and optional SMS channels plus Google Calendar; added the per-task action link; replaced the reminder ladder; updated Module 11 (dashboard) and Module 12 (monitoring) notes. Follow-up for a later module: `users.telegram_chat_id` (Module 1 schema) is now obsolete and should be dropped by a migration.
