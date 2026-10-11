"""The one Gmail search the app ever runs: mail from the user's sources, or mentioning a watch
keyword. Nothing outside this query is fetched.

    newer_than:30d -in:spam -in:trash (from:nptel.ac.in OR from:a@b.com OR hackathon OR ideathon)

There are deliberately no category exclusions: mail from a source may sit in Promotions. Sender
matching afterwards (sender_matches) is the strict rule; Gmail's own `from:` is only a filter to
keep the amount of mail fetched small.
"""

import logging
import re
from collections.abc import Iterable
from email.utils import parseaddr

logger = logging.getLogger(__name__)

BACKFILL_DAYS = 30

# Only plain domains / addresses and simple words: anything else could inject extra Gmail
# search operators ("x OR from:boss@work.test", "-in:anywhere", parentheses...).
_ENTRY_RE = re.compile(r"^[a-z0-9][a-z0-9._+@-]*$")
_KEYWORD_RE = re.compile(r"^[a-z0-9][a-z0-9 -]{0,58}[a-z0-9]$|^[a-z0-9]$")

HARD_EXCLUDED_LABELS = frozenset({"SPAM", "TRASH"})


def normalize_entries(entries: Iterable[object] | None) -> list[str]:
    """Lower-case, de-duplicate and drop anything that is not a safe domain or address."""
    result: list[str] = []
    for entry in entries or []:
        value = entry.strip().lower().removeprefix("@") if isinstance(entry, str) else ""
        if _ENTRY_RE.fullmatch(value):
            if value not in result:
                result.append(value)
        else:
            logger.warning("ignoring invalid sender entry (%d chars)", len(value))
    return result


def normalize_keywords(entries: Iterable[object] | None) -> list[str]:
    result: list[str] = []
    for entry in entries or []:
        value = " ".join(entry.lower().split()) if isinstance(entry, str) else ""
        if _KEYWORD_RE.fullmatch(value):
            if value not in result:
                result.append(value)
        else:
            logger.warning("ignoring invalid keyword (%d chars)", len(value))
    return result


def build_query(
    sources: Iterable[object], keywords: Iterable[object] | None, days: int = BACKFILL_DAYS
) -> str | None:
    """`sources` are objects with a `.senders` list. Returns None when there is nothing to look
    for, in which case no mail is fetched at all."""
    senders: list[str] = []
    for source in sources:
        for sender in normalize_entries(getattr(source, "senders", ())):
            if sender not in senders:
                senders.append(sender)
    terms = [f"from:{s}" for s in senders]
    terms += [f'"{k}"' if " " in k else k for k in normalize_keywords(keywords)]
    if not terms:
        return None
    return f"newer_than:{days}d -in:spam -in:trash ({' OR '.join(terms)})"


def sender_matches(sender: str | None, entries: Iterable[str]) -> bool:
    """A domain entry matches that domain or a true subdomain (nptel.ac.in matches
    onlinecourses.nptel.ac.in, never fakenptel.ac.in or nptel.ac.in.evil.test); an address entry
    matches only that exact address. The display name is ignored."""
    address = parseaddr(sender or "")[1].lower()
    if "@" not in address:
        return False
    domain = address.rsplit("@", 1)[1]
    for entry in entries:
        entry = entry.strip().lower()
        if "@" in entry:
            if address == entry:
                return True
        elif entry and (domain == entry or domain.endswith("." + entry)):
            return True
    return False


def labels_allowed(labels: Iterable[str] | None) -> bool:
    """Spam and trash never get through, whoever sent them."""
    return not (set(labels or ()) & HARD_EXCLUDED_LABELS)
