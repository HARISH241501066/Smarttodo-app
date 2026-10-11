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
