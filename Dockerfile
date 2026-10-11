# Smart To-Do: one image that runs the API, the scheduler and the dashboard.
# Stage 1 exports the Next.js dashboard as static files; stage 2 is the slim Python app that serves
# them. Build from the repository root:  docker build -t smart-todo .

FROM node:24-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build        # static export -> /web/out

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ENV=production \
    FRONTEND_DIST=/app/frontend_dist
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/app ./app
COPY --from=web /web/out /app/frontend_dist
# The app never writes to disk (Render's filesystem is temporary); everything lives in the database.
RUN useradd --system --no-create-home app
USER app
EXPOSE 10000
# ONE worker on purpose: more workers would each try to run the scheduler. (A Postgres advisory lock
# also guarantees that only one process ever does, even if two instances overlap during a deploy.)
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
