"""Smart folders: which tasks a folder holds.

A folder is never a copy. Its contents are worked out each time they are read: the tasks that
match its rules, plus the tasks pinned to it by hand. So a new task shows up in the right folders
by itself and editing the rules takes effect at once.

Rules: OR inside one list, AND across lists, an empty list is ignored. A folder with no rules
holds only its pinned tasks. Snoozed tasks are hidden; completed ones only if the folder asks.
"""

from datetime import UTC, datetime

from sqlalchemy import ColumnElement, and_, false, func, or_, select
from sqlalchemy.orm import Session

from app.ingest.sync import get_or_create_settings
from app.models import FolderPin, Task
from app.schemas.folders import FolderRules
from app.sources import load_sources

HACKATHON = "hackathon"
MANUAL = "manual"
DAY = 86_400
IST_OFFSET = 19_800

GROUPS = (
    ("overdue", "Overdue"),
    ("today", "Today"),
    ("tomorrow", "Tomorrow"),
    ("week", "This week"),
    ("later", "Later"),
    ("none", "No deadline"),
    ("completed", "Completed"),
)


def allowed_sources(session: Session, user_id: int) -> list[str]:
    settings = get_or_create_settings(session, user_id)  # a new user has the default sources
    names = [s.name for s in load_sources(settings.sources)]
    return [*names, HACKATHON, MANUAL]


def canonical_rules(session: Session, user_id: int, rules: FolderRules) -> FolderRules:
    """Source names are matched ignoring case and stored as the user's own spelling. Unknown
    names are rejected (ValueError) so a typo cannot make a folder silently empty."""
    allowed = {name.lower(): name for name in allowed_sources(session, user_id)}
    sources: list[str] = []
    for value in rules.sources:
        canonical = allowed.get(value.lower())
        if canonical is None:
            raise ValueError(f"'{value}' is not one of your sources")
        if canonical not in sources:
            sources.append(canonical)
    return rules.model_copy(update={"sources": sources})


def rules_clause(rules: FolderRules) -> ColumnElement[bool] | None:
    parts: list[ColumnElement[bool]] = []
    if rules.sources:
        options: list[ColumnElement[bool]] = []
        for name in rules.sources:
            if name.lower() == MANUAL:
                options.append(Task.source == "manual")
            elif name.lower() == HACKATHON:
                options.append(Task.source == "keyword")
            else:
                options.append(func.lower(Task.source_name) == name.lower())
        parts.append(or_(*options))
    if rules.tags:
        parts.append(func.lower(Task.tag).in_([t.lower() for t in rules.tags]))
    if rules.priorities:
        parts.append(Task.priority.in_(rules.priorities))
    if rules.title_contains:
        needle = rules.title_contains.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        parts.append(Task.title.ilike(f"%{needle}%", escape="\\"))
    return and_(*parts) if parts else None


def not_snoozed(now: datetime) -> ColumnElement[bool]:
    return or_(Task.snoozed_until.is_(None), Task.snoozed_until <= now)


def contents(
    user_id: int,
    rules: FolderRules,
    *,
    folder_id: int | None,
    show_completed: bool,
    now: datetime,
    only_open: bool = False,
):
    """SELECT of the tasks a folder holds: rule matches OR pinned, visible ones only."""
    matches = rules_clause(rules)
    pinned = (
        Task.id.in_(select(FolderPin.task_id).where(FolderPin.folder_id == folder_id))
        if folder_id is not None
        else None
    )
    options = [c for c in (matches, pinned) if c is not None]
    if not options:
        return select(Task).where(false())
    statuses = ("open", "done") if show_completed and not only_open else ("open",)
    return select(Task).where(
        Task.user_id == user_id, Task.status.in_(statuses), or_(*options), not_snoozed(now)
    )


def counts(session: Session, user_id: int, folder, now: datetime) -> tuple[int, bool]:
    """(open tasks, any of them overdue) for the sidebar."""
    rules = FolderRules.model_validate(folder.rules)
    base = contents(
        user_id, rules, folder_id=folder.id, show_completed=False, now=now, only_open=True
    ).subquery()
    total = session.scalar(select(func.count()).select_from(base)) or 0
    overdue = session.scalar(select(func.count()).select_from(base).where(base.c.deadline < now))
    return total, bool(overdue)


def group_key(task: Task, now: datetime) -> str:
    """Deadline group, judged on India days (the same rule the Deadlines page uses)."""
    if task.status == "done":
        return "completed"
    if task.deadline is None:
        return "none"
    if task.deadline < now:
        return "overdue"
    ist_day = lambda dt: int((dt.timestamp() + IST_OFFSET) // DAY)  # noqa: E731
    days = ist_day(task.deadline) - ist_day(now)
    if days <= 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return "week" if days <= 6 else "later"


def grouped(tasks: list[Task], now: datetime | None = None) -> list[tuple[str, str, list[Task]]]:
    now = now or datetime.now(UTC)
    out = []
    for key, label in GROUPS:
        items = [t for t in tasks if group_key(t, now) == key]
        if key == "completed":
            items.sort(key=lambda t: t.updated_at, reverse=True)
        else:
            items.sort(key=lambda t: (t.deadline is None, t.deadline or now, t.id))
        if items:
            out.append((key, label, items))
    return out
