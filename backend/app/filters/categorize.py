"""Rule-based category: nptel -> gform -> internship -> college -> unknown (first match wins)."""

import re
from collections.abc import Iterable
from urllib.parse import urlparse

from app.filters.auth_check import sender_domain
from app.filters.urls import unwrap_tracking_url
from app.ingest.mime import ParsedMessage

CATEGORIES = ("nptel", "gform", "internship", "college", "tracked", "unknown")

_NPTEL_RE = re.compile(r"\bnptel\b", re.IGNORECASE)
# Strong internship words are checked in subject and body; the weaker ones ("interview",
# "assessment") only in the subject, since college mail mentions them in passing.
_INTERNSHIP_ANY = re.compile(r"\b(internships?|offer\s+letter|shortlisted)\b", re.IGNORECASE)
_INTERNSHIP_SUBJECT = re.compile(r"\b(interviews?|assessments?)\b", re.IGNORECASE)


def _domain_in(domain: str, allowed: Iterable[str]) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in allowed if d)


def is_gform_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return host == "forms.gle" or (host == "docs.google.com" and parsed.path.startswith("/forms"))


def categorize(
    message: ParsedMessage, college_domains: Iterable[str] = (), tracked: bool = False
) -> str:
    """`tracked`: the sender is on the always-track list (and authenticated). Anything the rules
    below do not recognise is then "tracked" rather than "unknown"."""
    domain = sender_domain(message.sender)
    if _domain_in(domain, ["nptel.ac.in"]) or _NPTEL_RE.search(message.subject):
        return "nptel"
    if any(is_gform_url(unwrap_tracking_url(link.url)) for link in message.links):
        return "gform"
    if _INTERNSHIP_SUBJECT.search(message.subject) or _INTERNSHIP_ANY.search(
        f"{message.subject}\n{message.body_text}"
    ):
        return "internship"
    if _domain_in(domain, [d.strip().lower().removeprefix("@") for d in college_domains]):
        return "college"
    return "tracked" if tracked else "unknown"


def source_for(category: str) -> str:
    """tasks.source value for a category."""
    return "other" if category == "unknown" else category
