"""Pick the link that most directly lets the user complete the task.

Pure string analysis: links found in an email are never fetched or resolved.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.filters.auth_check import sender_domain
from app.filters.categorize import is_gform_url
from app.ingest.mime import Link, ParsedMessage

ACTION_THRESHOLD = 3
MAX_SCORE = 6  # score at which link_confidence reaches 1.0

_SOCIAL_HOSTS = (
    "facebook.com", "fb.com", "twitter.com", "x.com", "instagram.com", "linkedin.com",
    "youtube.com", "youtu.be", "t.me", "telegram.me", "wa.me", "whatsapp.com", "pinterest.com",
    "tiktok.com", "snapchat.com", "reddit.com", "threads.net",
)  # fmt: skip
_DROP_TEXT_OR_URL = re.compile(
    r"(?<![a-z])(?:unsubscribe|opt[-_ ]?out|preferences?|privacy|terms|"
    r"manage[-_ ]?(?:subscription|email))",
    re.IGNORECASE,
)
_IMAGE_RE = re.compile(r"\.(?:png|jpe?g|gif|svg|webp|ico|bmp)(?:$|[?#])", re.IGNORECASE)
_PIXEL_RE = re.compile(r"pixel|beacon|1x1|/open\.|/track/open|[?&]open=1", re.IGNORECASE)
_PATH_KEYWORDS = re.compile(
    r"assignment|quiz|exam|form|apply|register|submit|course", re.IGNORECASE
)
_TEXT_KEYWORDS = re.compile(
    r"submit|apply|start|attempt|quiz|assignment|click\s+here", re.IGNORECASE
)
_SECOND_LEVEL = {"ac", "co", "edu", "gov", "org", "com", "net"}

W_BRAND_DOMAIN = 3
W_GFORM = 3
W_PATH = 2
W_TEXT = 2
W_HOMEPAGE = -1


@dataclass(frozen=True)
class LinkChoice:
    url: str
    link_type: str  # "action" | "gmail"
    confidence: float


def gmail_fallback(message_id: str) -> LinkChoice:
    return LinkChoice(f"https://mail.google.com/mail/u/0/#all/{message_id}", "gmail", 0.0)


def base_domain(domain: str) -> str:
    """mail.nptel.ac.in -> nptel.ac.in; mail.example.com -> example.com"""
    labels = domain.lower().split(".")
    keep = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL else 2
    return ".".join(labels[-keep:])


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def is_droppable(link: Link) -> bool:
    url = link.url
    if not url.lower().startswith(("http://", "https://")):  # mailto: and anything else
        return True
    host = _host(url)
    if not host or any(host == h or host.endswith("." + h) for h in _SOCIAL_HOSTS):
        return True
    if _DROP_TEXT_OR_URL.search(url) or _DROP_TEXT_OR_URL.search(link.text):
        return True
    return bool(_IMAGE_RE.search(url) or _PIXEL_RE.search(url))


def score_link(link: Link, sender_base: str) -> int:
    parsed = urlparse(link.url)
    host = _host(link.url)
    score = 0
    if is_gform_url(link.url):
        score += W_GFORM
    elif sender_base and (host == sender_base or host.endswith("." + sender_base)):
        score += W_BRAND_DOMAIN
    if _PATH_KEYWORDS.search(parsed.path):
        score += W_PATH
    if _TEXT_KEYWORDS.search(link.text):
        score += W_TEXT
    if parsed.path in ("", "/") and not parsed.query:
        score += W_HOMEPAGE
    return score


def pick_action_link(message: ParsedMessage) -> LinkChoice:
    sender = sender_domain(message.sender)
    sender_base = base_domain(sender) if sender else ""
    best: tuple[int, Link] | None = None
    for link in message.links:
        if is_droppable(link):
            continue
        score = score_link(link, sender_base)
        if best is None or score > best[0]:  # earliest link wins ties
            best = (score, link)
    if best is None or best[0] < ACTION_THRESHOLD:
        return gmail_fallback(message.message_id)
    return LinkChoice(best[1].url, "action", round(min(best[0], MAX_SCORE) / MAX_SCORE, 2))
