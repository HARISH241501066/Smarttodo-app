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

# Gmail system labels that the query above excludes.
EXCLUDED_LABELS = frozenset(
    {"SPAM", "TRASH", "CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"}
)

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


def build_query(denylist: Iterable[object] | None) -> str:
    parts = [BASE_QUERY, *(f"-from:{d}" for d in normalize_denylist(denylist))]
    return " ".join(parts)


def sender_matches_denylist(sender: str | None, denylist: Iterable[str]) -> bool:
    address = parseaddr(sender or "")[1].lower()
    if "@" not in address:
        return False
    domain = address.rsplit("@", 1)[1]
    for entry in denylist:
        if "@" in entry:
            if address == entry:
                return True
        elif domain == entry or domain.endswith("." + entry):
            return True
    return False


def labels_allowed(labels: Iterable[str] | None) -> bool:
    return not (set(labels or ()) & EXCLUDED_LABELS)
