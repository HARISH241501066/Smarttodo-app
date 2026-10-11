import json
from datetime import UTC, datetime

import pytest

from app.ingest.mime import MAX_BODY_CHARS, MessageParseError, ParsedMessage, parse_message
from tests.gmail_fakes import FIXTURE_DIR, load_fixture


def parse(name: str) -> ParsedMessage:
    return parse_message(load_fixture(name))


def test_at_least_fifteen_synthetic_fixtures_exist():
    assert len(list(FIXTURE_DIR.glob("*.json"))) >= 15


def test_every_fixture_is_valid_json_with_gmail_shape():
    for path in FIXTURE_DIR.glob("*.json"):
        raw = json.loads(path.read_text())
        assert {"id", "threadId", "labelIds", "internalDate", "payload"} <= raw.keys(), path.name


def test_every_fixture_parses_except_the_deliberately_corrupt_one():
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        raw = json.loads(path.read_text())
        if path.stem == "11_corrupt_base64":
            with pytest.raises(MessageParseError):
                parse_message(raw)
        else:
            assert parse_message(raw).message_id == raw["id"], path.name


# ---------- multipart ----------


def test_plain_single_part_and_metadata():
    m = parse("01_plain_nptel")
    assert (
        m.body_text
        == "NPTEL Assignment 3 is due on 12 Jan 2026 11:59 PM IST. Submit on the portal."
    )
    assert m.subject == "NPTEL: Assignment 3 reminder"
    assert m.sender_email == "noreply@nptel.example.test"
    assert m.message_id == "msg001" and m.thread_id == "thr001"
    assert m.labels == ("INBOX", "UNREAD")
    assert m.received_at == datetime(2026, 1, 1, 1, 0, tzinfo=UTC)
    assert m.has_attachments is False


def test_multipart_alternative_prefers_plain_over_html():
    m = parse("02_multipart_alternative_internship")
    assert m.body_text.startswith("PLAIN VERSION")
    assert "HTML VERSION" not in m.body_text


def test_alternative_falls_back_to_html_when_plain_is_blank():
    m = parse("23_plain_empty_html_has_content")
    assert m.body_text == "Only the HTML has the deadline: 30 Jan."


def test_multipart_mixed_keeps_text_and_flags_attachment():
    m = parse("05_mixed_with_pdf_attachment")
    assert m.body_text == "Hall ticket attached. Exam begins 3 Feb."
    assert m.has_attachments is True


def test_nested_mixed_related_alternative_is_walked():
    m = parse("07_nested_mixed_related_alternative")
    assert m.body_text == "Nested plain body: workshop on 8 Feb."
    assert "Nested html body" not in m.body_text
    assert m.has_attachments is True


def test_calendar_part_is_ignored():
    m = parse("20_calendar_invite_part")
    assert m.body_text == "Meeting invite: project review on 15 Jan."
    assert "SECRET-ICS" not in m.body_text


# ---------- HTML ----------


def test_html_only_is_converted_and_keeps_link_targets():
    m = parse("03_html_only_gform")
    assert "Please fill the registration form" in m.body_text
    assert "https://docs.google.com/forms/d/e/SYNTH123/viewform" in m.body_text
    assert "<" not in m.body_text and ">" not in m.body_text
    assert "Thanks,\nDept Office" in m.body_text


def test_html_strips_script_style_head_and_comments():
    body = parse("04_html_complex_college").body_text
    for junk in ("alert", "tracking", "color:red", "ignored title", "hidden comment"):
        assert junk not in body
    assert "Assignment 4 closes on Friday." in body
    assert "Course Data Structures" in body
    assert "https://nptel.example.test/assignment/4" in body
    assert "&nbsp;" not in body and "<" not in body


def test_html_unicode_and_emoji_survive():
    m = parse("19_unicode_emoji_html")
    assert "Results out!" in m.body_text
    assert "வணக்கம்" in m.body_text
    assert "\U0001f393" in m.body_text
    assert "\U0001f393" in m.subject


# ---------- broken encoding ----------


def test_invalid_utf8_bytes_are_replaced_not_raised():
    m = parse("08_broken_utf8")
    assert "Assignment due" in m.body_text and "tomorrow" in m.body_text
    assert "�" in m.body_text


def test_declared_latin1_charset_is_honoured():
    assert parse("09_latin1_charset").body_text == "Café meeting at noon - bring résumé"


def test_unknown_charset_falls_back_to_utf8():
    assert parse("22_unknown_charset").body_text == "Unknown charset body"


def test_base64_without_padding_decodes():
    assert parse("10_base64_no_padding").body_text == "abcde"


def test_corrupt_base64_raises_parse_error():
    with pytest.raises(MessageParseError):
        parse("11_corrupt_base64")


# ---------- headers, attachments, edge cases ----------


def test_attachment_only_message_has_empty_body():
    m = parse("06_attachment_only")
    assert m.body_text == ""
    assert m.has_attachments is True


def test_empty_plain_body():
    assert parse("18_empty_plain_body").body_text == ""


def test_missing_headers_do_not_raise():
    m = parse("12_missing_headers")
    assert (m.subject, m.sender, m.sender_email) == ("", "", "")
    assert m.body_text == "No subject, no sender."


def test_rfc2047_encoded_words_are_decoded():
    m = parse("13_encoded_word_subject")
    assert m.subject == "Assignment — due tomorrow"
    assert m.sender == "Prof. Rao <rao@college.example.test>"
    assert m.sender_email == "rao@college.example.test"


def test_display_name_with_via_does_not_confuse_address_extraction():
    m = parse("14_sender_display_name_with_via")
    assert m.sender_email == "noreply@mail.nptel.example.test"
    assert "Prof. Rao via NPTEL" in m.sender


def test_message_without_payload_or_internal_date():
    m = parse_message({"id": "x1"})
    assert m.message_id == "x1" and m.body_text == "" and m.received_at is None


def test_pathologically_deep_nesting_is_rejected():
    part = {"mimeType": "text/plain", "body": {}}
    for _ in range(50):
        part = {"mimeType": "multipart/mixed", "parts": [part]}
    with pytest.raises(MessageParseError):
        parse_message({"id": "deep", "payload": part})


def test_huge_body_is_truncated():
    raw = load_fixture("01_plain_nptel")
    import base64

    big = base64.urlsafe_b64encode(b"a" * (MAX_BODY_CHARS * 2)).decode()
    raw["payload"]["body"]["data"] = big
    assert len(parse_message(raw).body_text) == MAX_BODY_CHARS


def test_repr_never_contains_message_content():
    m = parse("01_plain_nptel")
    text = repr(m)
    assert "msg001" in text
    for secret in ("Assignment 3", "NPTEL", "noreply@", "portal"):
        assert secret not in text
