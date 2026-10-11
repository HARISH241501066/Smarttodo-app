"""Daily digest: one in-app notification per user listing everything due in the next 72 hours."""

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Notification, Task, User, UserSettings
from app.notify.render import display_ist

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
DIGEST_WINDOW = timedelta(hours=72)
DEFAULT_DIGEST_HOUR = 8
MAX_LISTED = 20
MAX_NO_DEADLINE_LISTED = 10


def run_digest(session: Session, *, now: datetime | None = None) -> int:
    """Create today's digest for every user whose digest hour (IST) has arrived and who has
    none yet today. Running hourly therefore also catches up after downtime. Users with
    nothing due soon and no open task without a deadline get no digest.
    Returns how many were created."""
    now = now or datetime.now(UTC)
    local = now.astimezone(IST)
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)

    hour = func.coalesce(UserSettings.digest_hour, DEFAULT_DIGEST_HOUR)
    user_ids = session.scalars(
        select(User.id)
        .outerjoin(UserSettings, UserSettings.user_id == User.id)
        .where(hour <= local.hour)
        .order_by(User.id)
    ).all()

    created = 0
    for user_id in user_ids:
        already = session.scalar(
            select(Notification.id).where(
                Notification.user_id == user_id,
                Notification.kind == "digest",
                Notification.created_at >= day_start,
            )
        )
        if already:
            continue
        tasks = session.scalars(
            select(Task)
            .where(
                Task.user_id == user_id,
                Task.status == "open",
                Task.deadline > now,
                Task.deadline <= now + DIGEST_WINDOW,
            )
            .order_by(Task.deadline, Task.id)
        ).all()
        # Tasks without a deadline get no deadline reminders, so the digest is where they surface.
        undated = session.scalars(
            select(Task)
            .where(Task.user_id == user_id, Task.status == "open", Task.deadline.is_(None))
            .order_by(Task.created_at.desc(), Task.id.desc())
        ).all()
        if not tasks and not undated:
            continue
        lines = [f"- {t.title} (due {display_ist(t.deadline)})" for t in tasks[:MAX_LISTED]]
        if len(tasks) > MAX_LISTED:
            lines.append(f"...and {len(tasks) - MAX_LISTED} more")
        title = f"Daily digest: {len(tasks)} task(s) due in the next 72 hours"
        if undated:
            if lines:
                lines.append("")
            lines.append("No deadline:")
            lines += [f"- {t.title}" for t in undated[:MAX_NO_DEADLINE_LISTED]]
            if len(undated) > MAX_NO_DEADLINE_LISTED:
                lines.append(f"...and {len(undated) - MAX_NO_DEADLINE_LISTED} more")
            title += f", {len(undated)} with no deadline"
        session.add(
            Notification(
                user_id=user_id, kind="digest", title=title, body="\n".join(lines), created_at=now
            )
        )
        session.commit()
        created += 1
    logger.info("digest: created=%d", created)
    return created
