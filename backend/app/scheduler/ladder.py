"""The reminder ladder as a pure function: which (time, channel, kind) rows a task needs.

  on create : inapp + push, immediately
  T-48h     : push
  T-24h     : email (+ sms if enabled)
  T-10h     : email (+ sms if enabled)
  T-1h      : push

Offsets that are already past are skipped. A task found with less than 24h left gets its
email immediately (once, unless an email has already been sent for it).
Calendar events are Module 9's business and are not part of this ladder.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

# (kind, time before the deadline, channels)
OFFSETS: tuple[tuple[str, timedelta, tuple[str, ...]], ...] = (
    ("t48", timedelta(hours=48), ("push",)),
    ("t24", timedelta(hours=24), ("email",)),
    ("t10", timedelta(hours=10), ("email",)),
    ("t1", timedelta(hours=1), ("push",)),
)
IMMEDIATE_EMAIL_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class Step:
    fire_at: datetime
    channel: str
    kind: str


def _with_sms(channels: tuple[str, ...], sms_enabled: bool) -> tuple[str, ...]:
    return (*channels, "sms") if sms_enabled and "email" in channels else channels


def build_ladder(
    deadline: datetime | None,
    now: datetime,
    *,
    created: bool = False,
    sms_enabled: bool = False,
    email_already_sent: bool = False,
) -> list[Step]:
    steps: list[Step] = []
    if created:
        steps += [Step(now, "inapp", "created"), Step(now, "push", "created")]
    if deadline is None:
        return steps

    for kind, offset, channels in OFFSETS:
        fire_at = deadline - offset
        if fire_at < now:  # already past (an offset that is exactly now still fires)
            continue
        steps += [Step(fire_at, c, kind) for c in _with_sms(channels, sms_enabled)]

    remaining = deadline - now
    email_due_now = any(s.channel == "email" and s.fire_at == now for s in steps)
    if (
        timedelta(0) < remaining < IMMEDIATE_EMAIL_WINDOW
        and not email_already_sent
        and not email_due_now  # e.g. exactly 10h left: the T-10h email is already due
    ):
        steps += [Step(now, c, "immediate") for c in _with_sms(("email",), sms_enabled)]
    return steps
