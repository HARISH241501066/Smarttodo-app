"""Turns a Gmail API `format=full` message into plain text.

Preference order: text/plain, then HTML converted with BeautifulSoup. Bytes are
decoded with the part's declared charset and errors="replace", so broken
encodings never raise. Only structurally impossible input (e.g. invalid base64
length) raises MessageParseError.
"""

import base64
import binascii
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.header import decode_header, make_header
from email.utils import parseaddr
from typing import Any

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

MAX_DEPTH = 20
MAX_BODY_CHARS = 100_000
MAX_LINKS = 300
MAX_URL_CHARS = 2000
_URL_RE = re.compile(r"https?://[^\s<>\"'\]\[]+", re.IGNORECASE)
MAX_FETCHED_BYTES = 200 * 1024  # cap for text parts fetched via users.messages.attachments.get
_BLOCK_TAGS = ("p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol")
_CHARSET_RE = re.compile(r"""charset\s*=\s*["']?([\w.:-]+)""", re.IGNORECASE)


class MessageParseError(Exception):
    """The message structure is corrupt and cannot be parsed."""


@dataclass(frozen=True)
class Link:
    url: str
    text: str = ""  # anchor text; empty for URLs found in plain text


@dataclass(frozen=True)
class ParsedMessage:
    # subject/sender/body are excluded from repr so they can't leak into logs by accident.
    message_id: str
    thread_id: str
    labels: tuple[str, ...]
    received_at: datetime | None
    sender_email: str = field(repr=False)
    sender: str = field(repr=False)
    subject: str = field(repr=False)
    body_text: str = field(repr=False)
    has_attachments: bool
    # Topmost Authentication-Results header (the receiving server's own verdict); "" if absent.
    authentication_results: str = field(default="", repr=False)
    internal_date_ms: int | None = None
    # Every http(s)/mailto link in the message (HTML anchors first, then bare URLs). In memory only.
    links: tuple[Link, ...] = field(default=(), repr=False)


# (message_id, attachment_id) -> base64url data, as returned by users.messages.attachments.get
AttachmentFetcher = Callable[[str, str], str]


def _b64decode(data: str) -> bytes:
    data = "".join(data.split())
    if len(data) % 4 == 1:
        raise MessageParseError("invalid base64 length")
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (binascii.Error, ValueError) as exc:
        raise MessageParseError("invalid base64 data") from exc


def _headers(part: dict[str, Any]) -> dict[str, str]:
    return {
        h.get("name", "").lower(): h.get("value", "")
        for h in part.get("headers", [])
        if isinstance(h, dict)
    }


def _decode_header_value(value: str) -> str:
    try:
        value = str(make_header(decode_header(value)))
    except Exception:  # unknown charset, malformed encoded-word, ...
        pass
    return " ".join(value.split())


def _decode_bytes(raw: bytes, content_type: str) -> str:
    match = _CHARSET_RE.search(content_type or "")
    charset = match.group(1) if match else "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _plain_urls(text: str) -> list[str]:
    return [m.group(0).rstrip(".,;:!?)>") for m in _URL_RE.finditer(text)]


def _html_to_text(html: str, links: list[Link] | None = None) -> str:
    soup = BeautifulSoup(html, "html.parser")
    if links is not None:  # collect anchors BEFORE anything is stripped or rewritten
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"].strip()
            if href.lower().startswith(("http://", "https://", "mailto:")):
                links.append(Link(href[:MAX_URL_CHARS], " ".join(anchor.get_text().split())[:200]))
    for tag in soup(["script", "style", "head", "noscript", "template"]):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if href.lower().startswith(("http://", "https://")) and href not in anchor.get_text():
            anchor.append(f" ({href})")
    for tag in soup.find_all(["td", "th"]):
        tag.insert_after(" ")
    for tag in soup.find_all(_BLOCK_TAGS):
        tag.insert_after("\n")
    lines = [" ".join(line.split()) for line in soup.get_text().splitlines()]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return text.strip()


def _is_attachment(part: dict[str, Any], mime: str) -> bool:
    if part.get("filename"):
        return True
    if "attachment" in _headers(part).get("content-disposition", "").lower():
        return True
    # Text parts too large to inline arrive as attachmentId only; non-text with one is a file.
    return bool(part.get("body", {}).get("attachmentId")) and not mime.startswith("text/")


def _fetch_text_part(part: dict[str, Any], ctx: dict[str, Any]) -> str | None:
    """Base64 data for a text part delivered via attachmentId; None when it can't be fetched."""
    attachment_id = part.get("body", {}).get("attachmentId")
    fetch = ctx["fetch"]
    if not attachment_id or fetch is None:
        return None
    data = fetch(ctx["message_id"], attachment_id) or ""
    data = "".join(data.split())
    # 4 base64 chars = 3 bytes; cut on a 4-char boundary so the truncated prefix still decodes.
    max_chars = (MAX_FETCHED_BYTES // 3 + 1) * 4
    if len(data) > max_chars:
        logger.warning("message %s: fetched text body truncated", ctx["message_id"])
        data = data[:max_chars]
    return data


def _extract(part: dict[str, Any], depth: int, ctx: dict[str, Any]) -> str:
    if depth > MAX_DEPTH:
        raise MessageParseError("MIME nesting too deep")
    mime = (part.get("mimeType") or "").lower()
    children = part.get("parts") or []

    if children:
        results = [(c.get("mimeType", "").lower(), _extract(c, depth + 1, ctx)) for c in children]
        if mime == "multipart/alternative":
            for child_mime, text in results:
                if child_mime == "text/plain" and text:
                    return text
            return next((text for _, text in results if text), "")
        return "\n\n".join(text for _, text in results if text)

    if _is_attachment(part, mime):
        ctx["attachments"] = True
        return ""
    if mime not in ("text/plain", "text/html"):
        return ""
    data = part.get("body", {}).get("data")
    if data:
        raw = _b64decode(data)
    else:
        fetched = _fetch_text_part(part, ctx)
        if not fetched:
            return ""
        raw = _b64decode(fetched)[:MAX_FETCHED_BYTES]
    text = _decode_bytes(raw, _headers(part).get("content-type", ""))
    return _html_to_text(text, ctx["links"]) if mime == "text/html" else text.strip()


def _first_header(part: dict[str, Any], name: str) -> str:
    for h in part.get("headers", []):
        if isinstance(h, dict) and h.get("name", "").lower() == name:
            return h.get("value", "")
    return ""


def parse_message(
    raw: dict[str, Any], fetch_attachment: AttachmentFetcher | None = None
) -> ParsedMessage:
    """`fetch_attachment` is only called for filename-less text parts that carry an attachmentId."""
    payload = raw.get("payload") or {}
    headers = _headers(payload)
    sender = _decode_header_value(headers.get("from", ""))
    internal = raw.get("internalDate")
    received = (
        datetime.fromtimestamp(int(internal) / 1000, tz=UTC)
        if str(internal or "").isdigit()
        else None
    )
    ctx: dict[str, Any] = {
        "attachments": False,
        "fetch": fetch_attachment,
        "message_id": raw["id"],
        "links": [],
    }
    body = _extract(payload, 0, ctx)
    links: dict[str, Link] = {}
    for link in [*ctx["links"], *(Link(u[:MAX_URL_CHARS]) for u in _plain_urls(body))]:
        links.setdefault(link.url, link)
    return ParsedMessage(
        message_id=raw["id"],
        thread_id=raw.get("threadId", ""),
        labels=tuple(raw.get("labelIds") or ()),
        received_at=received,
        sender_email=parseaddr(sender)[1].lower(),
        sender=sender[:500],
        subject=_decode_header_value(headers.get("subject", ""))[:500],
        body_text=body[:MAX_BODY_CHARS],
        has_attachments=ctx["attachments"],
        authentication_results=" ".join(_first_header(payload, "authentication-results").split()),
        internal_date_ms=int(internal) if str(internal or "").isdigit() else None,
        links=tuple(links.values())[:MAX_LINKS],
    )
