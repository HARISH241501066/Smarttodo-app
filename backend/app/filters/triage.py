"""Sort a passed email into ignore / update / task / review using filters/triage_rules.py.

Pure function of the message text and whether the sender is a trusted always-track sender. It
runs before deadline extraction, never fetches anything, and returns only labels (never text).
"""

from dataclasses import dataclass, field
from typing import Literal

from app.filters import triage_rules as rules
from app.ingest.mime import ParsedMessage

Kind = Literal["ignore", "update", "task", "review"]

STRONG = 3
WEAK = 1


@dataclass(frozen=True)
class Triage:
    kind: Kind
    reason: str  # short human-readable why, e.g. "ignore: one-time code"
    task_cues: tuple[str, ...] = field(default=())
    update_cues: tuple[str, ...] = field(default=())


def _hits(patterns, text: str) -> list[str]:
    return [label for label, pattern in patterns if pattern.search(text)]


def classify(message: ParsedMessage, tracked: bool = False) -> Triage:
    """`tracked`: the sender is on the always-track list AND passed authentication."""
    subject = message.subject or ""
    body = (message.body_text or "")[: rules.BODY_CHARS]

    # 1. Things that are never a to-do, whoever sent them.
    ignored = _hits(rules.IGNORE_SUBJECT, subject) or _hits(
        rules.IGNORE_BODY_HEAD, body[: rules.BODY_HEAD_CHARS]
    )
    if ignored:
        return Triage("ignore", f"ignore: {ignored[0]}")

    # 2. A sender the user asked for always becomes a task.
    text = f"{subject}\n{body}"
    strong_task = _hits(rules.TASK_STRONG, text)
    weak_task = _hits(rules.TASK_WEAK, text)
    strong_update = _hits(rules.UPDATE_STRONG, text)
    weak_update = _hits(rules.UPDATE_WEAK, text)
    task_cues = tuple(strong_task + weak_task)
    update_cues = tuple(strong_update + weak_update)

    def result(kind: Kind, reason: str) -> Triage:
        return Triage(kind, reason, task_cues, update_cues)

    if tracked:
        return result("task", "task: always-track sender")

    # 3. Strong cues on both sides cannot be settled automatically.
    if strong_task and strong_update:
        return result("review", f"review: {strong_task[0]} vs {strong_update[0]}")
    if strong_task:
        return result("task", f"task: {strong_task[0]}")
    if strong_update:
        return result("update", f"update: {strong_update[0]}")

    # 4. Only weak cues, or none. More "do this" than "FYI" is a task; otherwise it is news.
    if len(weak_task) > len(weak_update):
        return result("task", f"task: {weak_task[0]}")
    if weak_update:
        return result("update", f"update: {weak_update[0]}")
    return result("update", "update: no action cues")
