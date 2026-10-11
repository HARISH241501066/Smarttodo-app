# PROJECT_CONTEXT.md

## Project
Smart Inbox To-Do. The user signs in with Google. The backend scans Gmail, filters out promotions, LinkedIn noise, spam and scam emails, and turns genuinely actionable emails (NPTEL, internships, Google Forms, college mail, and any other real to-do) into tasks with extracted deadlines and priorities (urgent / important / regular). Reminders go out via Telegram and Google Calendar. Suspected scams go to a "Suspicious" queue. Low-confidence items go to "Needs review". Users can also add, edit and delete tasks manually.

## Tech stack (do not substitute without asking)
- Backend: Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic, Pydantic v2
- DB: PostgreSQL 16+ (local install; no Docker required. Dev currently runs PostgreSQL 18 on port 5433)
- Google: google-auth, google-auth-oauthlib, google-api-python-client (Gmail, Calendar), Cloud Pub/Sub
- Parsing: dateparser, beautifulsoup4, rapidfuzz
- Scheduler: APScheduler with a Postgres job store
- Notify: Telegram Bot API via httpx; Google Calendar API
- Security: cryptography (Fernet / MultiFernet)
- Retries: tenacity
- Tests: pytest, pytest-asyncio, freezegun, respx
- Lint: ruff
- Frontend: Next.js (App Router, TypeScript, Tailwind)
- Time: store UTC (TIMESTAMPTZ); display Asia/Kolkata

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
