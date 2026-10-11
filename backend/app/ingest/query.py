"""Gmail search query builder and the post-filter that mirrors it.

`history.list` cannot take a search query, so incremental sync applies the same
exclusions locally (labels + denylist) to messages it discovers.
"""

import logging
import re
from collections.abc import Iterable
from email.utils import parseaddr

logger = logging.getLogger(__name__)

DEFAULT_DENYLIST = ("linkedin.com", "naukri.com")
BACKFILL_DAYS = 30

BASE_QUERY = (
    f"newer_than:{BACKFILL_DAYS}d -category:promotions -category:social "
    "-category:forums -in:spam -in:trash"
)

# Mail from "always track" senders is also fetched from Promotions/Social/Forums; spam and trash
# stay excluded for everyone.
TRACKED_BASE_QUERY = f"newer_than:{BACKFILL_DAYS}d -in:spam -in:trash"

# Gmail system labels that the query above excludes.
HARD_EXCLUDED_LABELS = frozenset({"SPAM", "TRASH"})
EXCLUDED_LABELS = HARD_EXCLUDED_LABELS | {
    "CATEGORY_PROMOTIONS",
    "CATEGORY_SOCIAL",
    "CATEGORY_FORUMS",
}

# Only plain domains / addresses: anything else could inject extra Gmail search operators.
_ENTRY_RE = re.compile(r"^[a-z0-9][a-z0-9._+@-]*$")


def normalize_denylist(entries: Iterable[object] | None) -> list[str]:
    """Lower-case, de-duplicate and drop entries that aren't a safe domain/address."""
    result: list[str] = []
    for entry in entries or []:
        value = entry.strip().lower().removeprefix("@") if isinstance(entry, str) else ""
        if _ENTRY_RE.fullmatch(value):
            if value not in result:
                result.append(value)
        else:
            logger.warning("ignoring invalid denylist entry (%d chars)", len(value))
    return result


def build_query(
    denylist: Iterable[object] | None, always_track: Iterable[object] | None = None
) -> str:
    """The normal query, OR-ed with a second clause for "always track" senders when there are any.

    With no tracked senders the result is exactly the normal query."""
    normal = " ".join([BASE_QUERY, *(f"-from:{d}" for d in normalize_denylist(denylist))])
    tracked = normalize_denylist(always_track)
    if not tracked:
        return normal
    return f"({normal}) OR ({TRACKED_BASE_QUERY} from:({' OR '.join(tracked)}))"


def sender_matches(sender: str | None, entries: Iterable[str]) -> bool:
    """A domain entry matches that domain and its subdomains (nptel.ac.in matches
    onlinecourses.nptel.ac.in but not fakenptel.ac.in); an address entry matches only itself."""
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


sender_matches_denylist = sender_matches


def labels_allowed(labels: Iterable[str] | None, tracked: bool = False) -> bool:
    """Whether a message's Gmail labels let it through. Tracked senders may come from the
    Promotions/Social/Forums tabs; nothing in Spam or Trash ever does."""
    excluded = HARD_EXCLUDED_LABELS if tracked else EXCLUDED_LABELS
    return not (set(labels or ()) & excluded)
