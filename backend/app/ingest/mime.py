"""Turns a Gmail API `format=full` message into plain text.

Preference order: text/plain, then HTML converted with BeautifulSoup. Bytes are
decoded with the part's declared charset and errors="replace", so broken
encodings never raise. Only structurally impossible input (e.g. invalid base64
length) raises MessageParseError.
"""

import base64
import binascii
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.header import decode_header, make_header
from email.utils import parseaddr
from typing import Any

from bs4 import BeautifulSoup

MAX_DEPTH = 20
MAX_BODY_CHARS = 100_000
_BLOCK_TAGS = ("p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol")
_CHARSET_RE = re.compile(r"""charset\s*=\s*["']?([\w.:-]+)""", re.IGNORECASE)


class MessageParseError(Exception):
    """The message structure is corrupt and cannot be parsed."""


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


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
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


def _extract(part: dict[str, Any], depth: int, found: dict[str, bool]) -> str:
    if depth > MAX_DEPTH:
        raise MessageParseError("MIME nesting too deep")
    mime = (part.get("mimeType") or "").lower()
    children = part.get("parts") or []

    if children:
        results = [(c.get("mimeType", "").lower(), _extract(c, depth + 1, found)) for c in children]
        if mime == "multipart/alternative":
            for child_mime, text in results:
                if child_mime == "text/plain" and text:
                    return text
            return next((text for _, text in results if text), "")
        return "\n\n".join(text for _, text in results if text)

    if _is_attachment(part, mime):
        found["attachments"] = True
        return ""
    if mime not in ("text/plain", "text/html"):
        return ""
    data = part.get("body", {}).get("data")
    if not data:
        return ""
    text = _decode_bytes(_b64decode(data), _headers(part).get("content-type", ""))
    return _html_to_text(text) if mime == "text/html" else text.strip()


def parse_message(raw: dict[str, Any]) -> ParsedMessage:
    payload = raw.get("payload") or {}
    headers = _headers(payload)
    sender = _decode_header_value(headers.get("from", ""))
    internal = raw.get("internalDate")
    received = (
        datetime.fromtimestamp(int(internal) / 1000, tz=UTC)
        if str(internal or "").isdigit()
        else None
    )
    found = {"attachments": False}
    body = _extract(payload, 0, found)
    return ParsedMessage(
        message_id=raw["id"],
        thread_id=raw.get("threadId", ""),
        labels=tuple(raw.get("labelIds") or ()),
        received_at=received,
        sender_email=parseaddr(sender)[1].lower(),
        sender=sender[:500],
        subject=_decode_header_value(headers.get("subject", ""))[:500],
        body_text=body[:MAX_BODY_CHARS],
        has_attachments=found["attachments"],
    )
