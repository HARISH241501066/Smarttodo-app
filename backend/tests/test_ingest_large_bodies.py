"""Module 3 patches: large text bodies fetched via attachmentId, and the extra parsed fields."""

import base64
import logging

import pytest
from sqlalchemy import select

from app.ingest import mime
from app.ingest.mime import MAX_FETCHED_BYTES, parse_message
from app.ingest.sync import run_sync
from app.models import User
from tests.gmail_fakes import FakeGmailApi


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def raw_message(parts, headers=None, message_id="msgBIG"):
    return {
        "id": message_id,
        "threadId": "thr",
        "labelIds": ["INBOX"],
        "internalDate": "1767225600000",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": headers or [{"name": "From", "value": "A <a@example.test>"}],
            "body": {"size": 0},
            "parts": parts,
        },
    }


def text_part(mime_type="text/plain", attachment_id="ATT1", filename=""):
    return {
        "partId": "0",
        "mimeType": mime_type,
        "filename": filename,
        "headers": [{"name": "Content-Type", "value": f'{mime_type}; charset="utf-8"'}],
        "body": {"size": 999_999, "attachmentId": attachment_id},
    }


class Fetcher:
    def __init__(self, data: dict[str, str]):
        self.data = data
        self.calls: list[tuple[str, str]] = []

    def __call__(self, message_id, attachment_id):
        self.calls.append((message_id, attachment_id))
        return self.data[attachment_id]


def test_text_part_delivered_via_attachment_id_is_read():
    fetch = Fetcher({"ATT1": b64("Deadline is 30 Jan.\nSubmit on the portal.")})
    parsed = parse_message(raw_message([text_part()]), fetch)
    assert "Deadline is 30 Jan." in parsed.body_text
    assert fetch.calls == [("msgBIG", "ATT1")]


def test_html_part_delivered_via_attachment_id_is_converted_to_text():
    fetch = Fetcher({"ATT1": b64("<p>Hello <b>there</b></p>")})
    parsed = parse_message(raw_message([text_part("text/html")]), fetch)
    assert parsed.body_text == "Hello there"


def test_real_file_attachment_is_skipped_and_never_fetched():
    fetch = Fetcher({"ATT1": b64("SECRET FILE CONTENT")})
    parsed = parse_message(raw_message([text_part(filename="notes.txt")]), fetch)
    assert parsed.body_text == ""
    assert parsed.has_attachments is True
    assert fetch.calls == []


def test_without_a_fetcher_a_large_text_part_is_just_empty():
    assert parse_message(raw_message([text_part()])).body_text == ""


def test_oversized_body_is_truncated_not_dropped(monkeypatch, caplog):
    monkeypatch.setattr(mime, "MAX_BODY_CHARS", 10_000_000)  # isolate the 200 KB fetch cap
    fetch = Fetcher({"ATT1": b64("x" * (MAX_FETCHED_BYTES * 2))})
    with caplog.at_level(logging.WARNING, logger="app.ingest.mime"):
        parsed = parse_message(raw_message([text_part()]), fetch)
    assert len(parsed.body_text) == MAX_FETCHED_BYTES
    assert "msgBIG" in caplog.text
    assert "x" * 50 not in caplog.text  # ids only, never content


def test_body_under_the_cap_is_not_flagged(caplog):
    fetch = Fetcher({"ATT1": b64("small body")})
    with caplog.at_level(logging.WARNING, logger="app.ingest.mime"):
        parse_message(raw_message([text_part()]), fetch)
    assert "truncated" not in caplog.text


def test_parsed_message_carries_auth_results_date_and_sender():
    headers = [
        {"name": "From", "value": "NPTEL <noreply@nptel.ac.in>"},
        {"name": "Subject", "value": "Hello"},
        {"name": "Authentication-Results", "value": "mx.google.com; dmarc=pass header.from=x"},
        {"name": "Authentication-Results", "value": "forged.example; dmarc=pass"},
    ]
    parsed = parse_message(raw_message([text_part()], headers), Fetcher({"ATT1": b64("hi")}))
    assert parsed.message_id == "msgBIG"
    assert parsed.sender == "NPTEL <noreply@nptel.ac.in>"
    assert parsed.subject == "Hello"
    assert parsed.authentication_results == "mx.google.com; dmarc=pass header.from=x"
    assert parsed.internal_date_ms == 1767225600000
    assert parsed.body_text == "hi"


def test_missing_authentication_results_header_is_empty_string():
    parsed = parse_message(raw_message([text_part()]), Fetcher({"ATT1": b64("hi")}))
    assert parsed.authentication_results == ""


def test_body_and_headers_stay_out_of_repr():
    parsed = parse_message(raw_message([text_part()]), Fetcher({"ATT1": b64("PRIVATE BODY")}))
    assert "PRIVATE BODY" not in repr(parsed)
    assert "mx.google" not in repr(parsed)


def test_sync_fetches_large_body_through_the_gmail_api(db_session):
    user = User(email="me@example.test")
    db_session.add(user)
    db_session.commit()
    gmail = FakeGmailApi(messages={"msgBIG": raw_message([text_part()])})
    gmail.attachments[("msgBIG", "ATT1")] = b64("Big body text")
    seen = []
    run_sync(user, db_session, gmail, lambda u, m: seen.append(m.body_text))
    assert seen == ["Big body text"]
    assert ("attachment", "msgBIG", "ATT1") in gmail.calls
    assert db_session.scalar(select(User.id)) == user.id


@pytest.mark.parametrize("size", [0, 1, 2, 3])
def test_truncation_boundary_always_decodes(size):
    """A cut on the 4-char base64 boundary never produces an invalid-length string."""
    fetch = Fetcher({"ATT1": b64("y" * (MAX_FETCHED_BYTES + size))})
    parsed = parse_message(raw_message([text_part()]), fetch)
    assert parsed.body_text.startswith("yyyy")
