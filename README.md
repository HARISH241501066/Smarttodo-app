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
Next.js (App Router, TypeScript, Tailwind); an installable PWA. `npm run build` exports it as static
files (`frontend/out`) which FastAPI serves from the same address as the API (see Deployment), so the
session cookie is same-origin. During development `npm run dev` proxies `/api/*` to the backend.

    cd frontend && npm install
    npm run dev            # http://localhost:3000   (BACKEND_URL defaults to http://127.0.0.1:8000)
    npm run build          # static export into frontend/out

To sign in through the dashboard, add to `.env` (then restart the backend):

    POST_LOGIN_REDIRECT=http://localhost:3000/
    FRONTEND_URL=http://localhost:3000
    SCHEDULER_ENABLED=true

Tests:

    cd frontend
    npm test               # unit tests (dates, request bodies)
    npm run e2e            # real Chromium against a throwaway backend serving the exported dashboard (temporary database,
                           # seeded data, fake Gmail/push); never touches your data
    npm run icons          # regenerate the PWA icons

Web Push needs HTTPS or `localhost`. `PUBLIC_BASE_URL` / `FRONTEND_URL` must be the production HTTPS
address (see Deployment).

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

## Deployment (Render + Neon, free)

One Render web service runs everything: the FastAPI API, the background scheduler and the dashboard
(exported as static files and served by FastAPI from the same address, so there is one origin and
one cookie). The database is a free Neon Postgres. UptimeRobot pings `/health` so the free service
does not sleep and you get an email if it goes down.

```
browser / phone ──https──> Render web service (Docker, ONE uvicorn worker)
                              ├─ /            dashboard pages (static files)
                              ├─ /api/...     the API
                              ├─ /auth/...    Google sign-in (callback address)
                              ├─ /action/...  "Mark as done" page from emails
                              ├─ /health      200 only if the DB answers and the scheduler beats
                              └─ scheduler    reminders, 15-minute Gmail sync, digest
                                   │
                                   └──────────> Neon Postgres (everything is stored here)
```

### How to deploy

1. Create the Google project and OAuth client, the Neon database, and the Render service by
   following the steps your assistant walks you through (they are the manual steps of the
   deployment module; the names of the variables are in the table below).
2. Render builds the `Dockerfile` (stage 1 exports the dashboard with Node, stage 2 is the slim
   Python image). On every start it runs `alembic upgrade head` and then
   `uvicorn app.main:app --workers 1`.
3. Check `https://<app-address>/health` returns `{"status":"ok","db":"ok","scheduler":"ok"}`.
4. Add the UptimeRobot monitor (HTTP(s), `/health`, every 5 minutes, email alerts to you).

Build and run the image locally (needs Docker and a Postgres you can reach):

    docker build -t smart-todo .
    docker run --rm -p 10000:10000 --env-file .env -e ENV=production -e PORT=10000 smart-todo

Run it without Docker (the dashboard is built once, then FastAPI serves it):

    cd frontend && npm install && npm run build           # writes frontend/out
    cd ../backend && FRONTEND_DIST=../frontend/out .venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
    # then open http://localhost:8000 (set FRONTEND_URL and PUBLIC_BASE_URL to that address)

`npm run dev` still works for working on the dashboard (it proxies `/api/*` to the backend).

### Environment variables

Set these in the Render dashboard (**Environment**). `render.yaml` lists them; values for the ones
marked secret are never stored in the repository.

| Variable | Secret | What it is |
| --- | --- | --- |
| `ENV` | no | `production` (already in `render.yaml`): no `/docs`, secure cookies, HSTS, dev page off |
| `DATABASE_URL` | yes | Neon **direct** connection string (`postgresql://…?sslmode=require`; not the `-pooler` one, because the scheduler lock needs a real connection) |
| `PUBLIC_BASE_URL` | no | `https://<app-address>`: used in the emails' "Mark as done" links |
| `FRONTEND_URL` | no | the same `https://<app-address>`: the only origin allowed to make changes (CSRF/CORS) |
| `GOOGLE_CLIENT_ID` | no | OAuth client id |
| `GOOGLE_CLIENT_SECRET` | yes | OAuth client secret |
| `GOOGLE_REDIRECT_URI` | no | `https://<app-address>/auth/callback` (must match the OAuth client exactly) |
| `POST_LOGIN_REDIRECT` | no | `/` (already in `render.yaml`) |
| `FERNET_KEY` | yes | encrypts stored Google refresh tokens; comma-separated list for rotation, newest first |
| `SESSION_SECRET` | yes | signs the session cookie; comma-separated for rotation |
| `MARK_DONE_SECRET` | yes | signs the email "Mark as done" links (optional; empty = `SESSION_SECRET`) |
| `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_FROM` | password: yes | escalation emails; `SMTP_PORT` (587) and `SMTP_SECURITY` (starttls) are preset |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | private: yes | Web Push (`python backend/scripts/generate_vapid_keys.py`; subject like `mailto:you@example.com`) |
| `SCHEDULER_ENABLED` | no | `true` (preset): this process runs reminders and the sync |
| `PUBSUB_MODE` | no | `off` (preset and forced off in production): the 15-minute poll is the only mode |
| `CALENDAR_ENABLED`, `SMS_ENABLED`, `DISPLAY_TIMEZONE` | no | `true`, `false`, `Asia/Kolkata` (preset) |
| `PORT` | no | set by Render |
| `FRONTEND_DIST` | no | set in the Docker image (`/app/frontend_dist`) |

Generate keys with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # FERNET_KEY
    python -c "import secrets; print(secrets.token_urlsafe(48))"                                  # SESSION_SECRET, MARK_DONE_SECRET

Changing `FERNET_KEY` or `SESSION_SECRET`: put the new value first and keep the old one after a
comma (`new,old`); nobody is signed out and no stored token breaks. Remove the old one later.

### Things to know about the free plan

* Render's free web service sleeps after 15 minutes without a request. The UptimeRobot ping every
  5 minutes keeps it awake. It may also restart now and then; the scheduler lock is released and taken
  again, and the jobs live in the database, so nothing is lost.
* Only **one** uvicorn worker runs (more would each start a scheduler). A Postgres advisory lock
  also makes sure that if two instances ever overlap (for example during a deploy) only one of
  them runs the scheduler and sends reminders.
* Neon scales to zero when idle; the first query after a pause can take a second or two. The app
  recycles its connections, so this is invisible apart from that one slow request.
* The filesystem is temporary. The app writes nothing to disk; everything is in the database.

### Roll back

* **Code:** Render dashboard, **Events** (or **Deploys**), pick the last good deploy, **Rollback**.
  Migrations only add things, so older code keeps working against the newer database.
* **A bad migration:** run `alembic downgrade -1` from a shell with `DATABASE_URL` set to the Neon
  string (most migrations have a downgrade; check the file first), then roll the code back.
* **A bad environment variable:** change it back in **Environment**; Render restarts the service.

### Runbook

| Symptom | What to check | Fix |
| --- | --- | --- |
| "Please sign in again" banner, or no new tasks | The Google login expired or was revoked (`invalid_grant`). Testing-mode OAuth apps expire every 7 days. | Sign in again. Publish the OAuth app **In production** so it stops expiring. The app marks the user and stops syncing for them until they sign in. |
| No reminder emails, or `SMTP` errors in the logs | Render logs show `smtp` failures; wrong `SMTP_HOST/USERNAME/PASSWORD`, or the provider blocked the login | Fix the SMTP variables (use an app password). Reminders for push and in-app still arrive; failed sends are retried a few times (up to 5 attempts) before the reminder is given up on. |
| The first page load is slow, or UptimeRobot reports "down" for a minute | The free service was asleep or restarting | Normal after a deploy or a long gap. Check the UptimeRobot interval is 5 minutes. |
| `/health` returns 503 with `"scheduler":"stale"` | The scheduler has not beaten for 5 minutes: it stopped, or another instance holds the lock | Render dashboard, **Manual Deploy**, **Restart service**. If it persists, check there is exactly one instance and the logs for `another instance already runs the scheduler`. |
| `/health` returns 503 with `"db":"error"`, or logs show `OperationalError` / `connection` errors | Neon is paused, the string is wrong, or you used the `-pooler` URL | Open the Neon console (it wakes the database), confirm `DATABASE_URL` is the **direct** string with `?sslmode=require`, and restart. |
| Sign-in fails with `redirect_uri_mismatch` | The OAuth client's redirect URI differs from `GOOGLE_REDIRECT_URI` | They must match exactly, including `https` and no trailing slash. |
| Changes are refused with 403 "Cross-site request refused" | `FRONTEND_URL` / `PUBLIC_BASE_URL` do not equal the address in the browser | Set both to the exact `https://<app-address>` (or custom domain). |
