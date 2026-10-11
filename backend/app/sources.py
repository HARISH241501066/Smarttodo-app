"""The user's sources and watch keywords, as read from settings."""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.ingest.query import normalize_entries, normalize_keywords, sender_matches

SOURCE_TYPES = ("nptel", "digii", "internship", "custom")
MAX_NAME_CHARS = 40

DEFAULT_KEYWORDS = ["hackathon", "ideathon", "datathon"]
# NPTEL is a known domain. Digii differs per college and internship alerts per person, so those
# start empty rather than guessing a domain that would then be trusted.
DEFAULT_SOURCES: list[dict] = [
    {"name": "NPTEL", "type": "nptel", "senders": ["nptel.ac.in"]},
    {"name": "Digii", "type": "digii", "senders": []},
    {"name": "Internship", "type": "internship", "senders": []},
]


@dataclass(frozen=True)
class Source:
    name: str
    type: str
    senders: tuple[str, ...]


def load_sources(raw: Iterable[object] | None) -> list[Source]:
    """Tolerant reader for the stored JSON: malformed entries are skipped, never crash a sync."""
    sources: list[Source] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        name = " ".join(str(item.get("name", "")).split())[:MAX_NAME_CHARS]
        kind = item.get("type")
        if not name or kind not in SOURCE_TYPES:
            continue
        senders = tuple(normalize_entries(item.get("senders") or []))
        sources.append(Source(name, kind, senders))
    return sources


def find_source(sender: str | None, sources: Iterable[Source]) -> Source | None:
    """The first source whose senders match this From header."""
    return next((s for s in sources if sender_matches(sender, s.senders)), None)


def matching_keyword(text: str, keywords: Iterable[str]) -> str | None:
    """The first watch keyword found as a whole word (or phrase) in `text`."""
    for keyword in normalize_keywords(keywords):
        if re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", text, re.IGNORECASE):
            return keyword
    return None
