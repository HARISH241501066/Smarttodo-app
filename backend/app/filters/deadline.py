"""Deadline extraction from cue phrases.

Only the text right after a cue ("last date", "submit by", ...) is looked at, so send dates,
footer years and unrelated dates are never picked up. dateparser runs on at most 120
characters after each cue, with day-first dates, future preference and IST.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import dateparser

IST = ZoneInfo("Asia/Kolkata")
SNIPPET_CHARS = 120
CONF_ONE = 0.9
CONF_MANY = 0.6
CONF_NONE = 0.3  # no deadline found: the task goes to review
URGENT_WINDOW = timedelta(hours=48)

_CUE_RE = re.compile(
    r"last\s+(?:date|day)|deadline|due\s+(?:on|by|date)|clos(?:es|ing)\s+(?:on|date|at|by)|"
    r"(?:submi\w*|fill\w*)\b[^\n]{0,60}?\b(?:by|before)\b|"
    r"(?:enrol\w*|register|complete|reply|respond|confirm)\s+(?:by|before)\b|"
    r"extended\s+(?:till|until|to|up\s*to)|registration\s+(?:ends?|closes?)|"
    r"apply\s+(?:by|before)",
    re.IGNORECASE,
)

_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_WEEKDAY = r"(?:mon|tues?|wed(?:nes)?|thu(?:rs?)?|fri|sat(?:ur)?|sun)(?:day)?"
DATE_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b"
    rf"|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}\b\.?(?:,?\s+\d{{4}})?"
    rf"|\b{_MONTH}\b\.?\s+\d{{1,2}}(?:st|nd|rd|th)?\b(?:,?\s+\d{{4}})?"
    rf"|\b(?:today|tonight|tomorrow)\b|\b(?:(?:next|this)\s+)?{_WEEKDAY}\b",
    re.IGNORECASE,
)
_TIME_RES = (
    re.compile(r"\b(\d{1,2})[:.](\d{2})\s*(am|pm)\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,2})\s*(am|pm)\b", re.IGNORECASE),
    re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b"),
)
# "18 Oct 2026 | 11:59 PM": tables and templates separate the date from the time with a pipe.
_PIPE_BEFORE_TIME = re.compile(
    r"(?<=[0-9A-Za-z])\s*\|\s*(?=\d{1,2}\s*[:.]\d{2}|\d{1,2}\s*[ap]\.?m\b)", re.IGNORECASE
)
_LEADING_JUNK = re.compile(
    r"^[\s:,\-–—]*(?:(?:is|was|will\s+be|on|by|to|till|until)\b)?[\s:,\-–—]*", re.I
)


@dataclass(frozen=True)
class DeadlineResult:
    deadline: datetime | None  # the earliest future deadline, UTC
    confidence: float
    candidates: int = 0  # how many different deadlines were found
    latest: datetime | None = None  # the latest of them (== deadline when there is one)
    expired: datetime | None = None  # latest deadline already past (only set when none is upcoming)


def _time_of_day(snippet: str, date_end: int) -> tuple[int, int] | None:
    """Explicit time of day mentioned up to ~25 characters after the date, or None."""
    window = snippet[: date_end + 25]
    for index, pattern in enumerate(_TIME_RES):
        m = pattern.search(window)
        if not m:
            continue
        if index == 0:
            hour, minute, ampm = int(m.group(1)), int(m.group(2)), m.group(3).lower()
        elif index == 1:
            hour, minute, ampm = int(m.group(1)), 0, m.group(2).lower()
        else:
            return int(m.group(1)), int(m.group(2))
        if not 1 <= hour <= 12:
            continue
        return (hour % 12) + (12 if ampm == "pm" else 0), minute
    return None


def _snippets(text: str) -> list[str]:
    """Up to 120 characters after each cue, cut at a line break or at the next cue."""
    cues = list(_CUE_RE.finditer(text))
    out = []
    for i, cue in enumerate(cues):
        end = cue.end() + SNIPPET_CHARS
        if i + 1 < len(cues):
            end = min(end, cues[i + 1].start())
        lines = text[cue.end() : end].split("\n")
        snippet = lines[0]
        if not re.search(r"\w", snippet):  # label and value on separate lines (table layout)
            snippet = next((ln for ln in lines[1:] if re.search(r"\w", ln)), "")
        out.append(_LEADING_JUNK.sub("", snippet, count=1))
    return out


def extract_deadline(
    text: str, received_at: datetime, now: datetime | None = None
) -> DeadlineResult:
    """Earliest future deadline near a cue phrase. `now` defaults to the current time."""
    now = now or datetime.now(UTC)
    text = _PIPE_BEFORE_TIME.sub(" ", text)
    received = received_at.astimezone(IST).replace(tzinfo=None)
    settings = {
        "DATE_ORDER": "DMY",
        "PREFER_DATES_FROM": "future",
        "TIMEZONE": "Asia/Kolkata",
        "RETURN_AS_TIMEZONE_AWARE": True,
        "RELATIVE_BASE": received,
    }
    found: set[datetime] = set()
    past: set[datetime] = set()
    for snippet in _snippets(text):
        m = DATE_RE.search(snippet)  # only the date expression right after the cue
        if not m:
            continue
        parsed = dateparser.parse(m.group(0), settings=settings)
        if parsed is None:
            continue
        parsed = parsed.astimezone(IST)
        tod = _time_of_day(snippet, m.end())
        if tod is not None:
            parsed = parsed.replace(hour=tod[0], minute=tod[1], second=0, microsecond=0)
        else:  # date-only (including "tomorrow"): end of that day, IST
            parsed = parsed.replace(hour=23, minute=59, second=0, microsecond=0)
        deadline = parsed.astimezone(UTC)
        if deadline > now:
            found.add(deadline)
        else:
            past.add(deadline)
    if not found:
        return DeadlineResult(None, CONF_NONE, 0, None, max(past) if past else None)
    return DeadlineResult(
        min(found), CONF_ONE if len(found) == 1 else CONF_MANY, len(found), max(found)
    )


# Mail from a source the user chose, or one of their watch keywords, matters more than the rest.
IMPORTANT_SOURCES = frozenset(
    {"nptel", "digii", "internship", "custom", "keyword", "gform", "tracked"}
)


def priority_for(category: str, deadline: datetime | None, now: datetime) -> str:
    if deadline is not None and deadline - now <= URGENT_WINDOW:
        return "urgent"
    return "important" if category in IMPORTANT_SOURCES else "regular"
