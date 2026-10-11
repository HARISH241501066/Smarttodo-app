# Smart Inbox To-Do

See PROJECT_CONTEXT.md.

## Backend dev
    cp .env.example .env
    docker compose up -d
    cd backend && py -3.13 -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt
    .venv/Scripts/uvicorn app.main:app --reload
    .venv/Scripts/pytest

## Migrations and tests
    cd backend && .venv/Scripts/alembic upgrade head
Tests create and drop temporary databases, so the DB user in `DATABASE_URL` needs `CREATEDB`
(`ALTER USER smart_todo CREATEDB;`).

## Google sign-in
Fill GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, FERNET_KEY and SESSION_SECRET in `.env`
(see `.env.example`), run `alembic upgrade head`, start the server and open
http://localhost:8000/auth/login (use `localhost`, not `127.0.0.1`).

## Gmail sync
Sign in, then `POST /sync/run` (with the session cookie). The first run backfills 30 days;
later runs are incremental. Gmail rate limits are handled by backing off and deferring the rest.

## Dashboard (frontend)
Next.js (App Router, TypeScript, Tailwind); an installable PWA. It proxies `/api/*` to the backend, so the
session cookie stays same-origin.

    cd frontend && npm install
    npm run dev            # http://localhost:3000   (BACKEND_URL defaults to http://127.0.0.1:8000)
    npm run build && npm start

To sign in through the dashboard, add to `.env` (then restart the backend):

    POST_LOGIN_REDIRECT=http://localhost:3000/
    FRONTEND_URL=http://localhost:3000
    SCHEDULER_ENABLED=true

Tests:

    cd frontend
    npm test               # unit tests (dates, request bodies)
    npm run e2e            # real Chromium against a throwaway backend (temporary database,
                           # seeded data, fake Gmail/push); never touches your data
    npm run icons          # regenerate the PWA icons

Web Push needs HTTPS or `localhost`. `PUBLIC_BASE_URL` / `FRONTEND_URL` must become the production
HTTPS URLs in Module 14.

## Privacy

Smart To-Do is built so that you can let a classmate sign in without handing over their mailbox.

**Which mail is read.** Only mail that matches what you configured in Settings: the senders of your
*sources* (for example `nptel.ac.in`) and your *watch keywords* (for example "hackathon"), from the
last 30 days, never Spam or Trash. Everything else in the inbox is never listed, fetched or
looked at. Mail from a source is accepted only if the sender's domain is exactly the source's domain
or a true subdomain of it, and it passes SPF/DKIM/DMARC; look-alike domains (`fakenptel.ac.in`) are
treated as impersonation and held in the Suspicious list.

**What is stored.** For each task: its title (the email subject), the deadline, the link, the
sender's address, the source name, your own notes, tags and subtasks, and the status. For each
handled email: only its Gmail message id, the decision (task, dropped, ignored, suspicious) and a
short fixed reason. For emails held as suspicious: the subject, the sender and the reasons. Your
settings, your folders and pins, your push subscription, and your refresh token, which is stored
only in encrypted form (Fernet, keys from `.env`).

**What is never stored.** **Email bodies.** The body of an email is read in memory to find the
deadline and the action link, and is dropped. It is not written to the database, to logs or to
error messages. "View email" fetches the message from Gmail again each time you click it. Logs
contain message ids, counts and decisions only. `backend/tests/test_security.py` checks this: it
runs a full sync over emails whose bodies carry a marker, then searches every table and the logs.

**Retention.** Thirty days after a task is completed, the notes you wrote on it and the bodies of
the notifications about it are erased. Deleting a task removes its subtasks, reminders and pins.

**What goes where.**

| Data | Goes to | Why |
| --- | --- | --- |
| A task's title, deadline and link | your SMTP provider, as a reminder email to you | escalation emails |
| A task's title and short text | the browser vendor's push service (Google, Mozilla, Apple), encrypted for your browser | Web Push |
| A task's title, deadline and link | your own Google Calendar | the calendar event |
| Your Gmail address and a read-only view of the mail described above | Google | sign-in and reading mail |

Nothing is sent anywhere else: no analytics, no third-party scripts, no fonts or images from other
sites (the page's Content-Security-Policy forbids them).

**Permissions asked from Google.** Exactly four: `openid`, `email`, `gmail.readonly` (read mail,
cannot send, delete or change it) and `calendar.events` (only the events this app creates).

**Delete everything.** Settings, then "Delete my data", then type `DELETE`. This removes your
Calendar events, revokes the Google grant, and deletes every row about you (tasks, subtasks,
folders, pins, reminders, notifications, sources and settings, push subscriptions, handled-message
ids, suspicious emails, tokens). You can also revoke access at
<https://myaccount.google.com/permissions> at any time.

## Security notes

* Secrets (Google client secret, `FERNET_KEY`, `SESSION_SECRET`, `MARK_DONE_SECRET`, the VAPID
  private key, the SMTP password) live only in `.env`, which is git-ignored; `.env.example` has
  placeholders only. Check with `gitleaks git .` and `python backend/scripts/scan_bundle.py`
  (looks for your `.env` secrets inside the built frontend).
* **Rotating keys.** `FERNET_KEY`, `SESSION_SECRET` and `MARK_DONE_SECRET` accept several
  comma-separated values, newest first. The first one signs or encrypts; all of them are accepted
  when reading, so nobody is signed out and no stored token breaks. Remove the old value once it
  is no longer needed.
* **Production.** Set `ENV=production`. That turns off `/docs`, `/redoc` and `/openapi.json`,
  forces the dev push page off, makes cookies `Secure` and adds HSTS. Set `FRONTEND_URL` and
  `PUBLIC_BASE_URL` to the real HTTPS addresses: they are the only origins allowed to make
  state-changing requests (CSRF check) and the only CORS origin.
* **Risky actions** (`Rebuild from sources`, `Delete my data`) need the word typed and sent as a
  `confirm` field. Rate limits (HTTP 429) apply to `/auth/*`, the mark-done page, push
  subscribing, sync and rebuild.
* **Known limits.** The dashboard's Content-Security-Policy allows inline scripts: Next.js writes
  its own inline hydration scripts into every page and, with Cache Components, pages are
  prerendered, so a per-request nonce is not available. All other directives are strict. The
  rate limiter is per process and in memory. `npm audit` lists one dev-only advisory (`braces`,
  pulled in by the lint tooling, no patched release exists); `npm audit --omit=dev` is clean.
