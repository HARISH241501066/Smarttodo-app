"""Module 5 extraction logic: link collection, categories, deadlines, links, priority."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.filters.categorize import categorize
from app.filters.deadline import CONF_MANY, CONF_ONE, extract_deadline, priority_for
from app.filters.links import gmail_fallback, pick_action_link
from app.filters.task_stage import clean_title, same_topic
from app.ingest.mime import Link, ParsedMessage, parse_message

FIXTURES = Path(__file__).parent / "fixtures" / "task_emails"
COLLEGE = ["college.example.edu"]
RECEIVED = datetime(2026, 1, 1, 6, 0, tzinfo=UTC)  # Thursday 11:30 IST


def load_cases():
    expected = json.loads((FIXTURES / "expected.manifest").read_text())
    cases = []
    for name, want in expected.items():
        message = parse_message(json.loads((FIXTURES / f"{name}.json").read_text()))
        cases.append((name, message, want))
    return cases


def run(message):
    now = message.received_at
    text = f"{message.subject}\n{message.body_text}"
    return (
        categorize(message, COLLEGE),
        extract_deadline(text, message.received_at, now),
        pick_action_link(message),
    )


def make(subject="Hi", sender="A <a@example.test>", body="", links=()):
    return ParsedMessage(
        message_id="m1", thread_id="t", labels=("INBOX",), received_at=RECEIVED, sender_email="",
        sender=sender, subject=subject, body_text=body, has_attachments=False, links=tuple(links),
    )  # fmt: skip


def deadline_of(text, now=None):
    return extract_deadline(text, RECEIVED, now or RECEIVED).deadline


# ---------- the evaluation set ----------


def test_at_least_twenty_fixtures():
    assert len(load_cases()) >= 20


def test_evaluation_set_meets_the_accuracy_targets():
    cases = load_cases()
    category_ok = deadline_ok = link_ok = 0
    misses = []
    for name, message, want in cases:
        category, deadline, link = run(message)
        got_deadline = deadline.deadline.isoformat() if deadline.deadline else None
        want_link = (
            gmail_fallback(message.message_id).url if want["link"] == "gmail" else want["link"]
        )
        category_ok += category == want["category"]
        deadline_ok += got_deadline == want["deadline"]
        link_ok += link.url == want_link
        for label, got, exp in (
            ("category", category, want["category"]),
            ("deadline", got_deadline, want["deadline"]),
            ("link", link.url, want_link),
        ):
            if got != exp:
                misses.append(f"{name}: {label} got {got!r} want {exp!r}")
    total = len(cases)
    print(f"\ncategories {category_ok}/{total} deadlines {deadline_ok}/{total}", end=" ")
    print(f"links {link_ok}/{total}")
    print("\n".join(misses))
    assert category_ok == total, misses
    assert deadline_ok / total >= 0.9, misses
    assert link_ok / total >= 0.9, misses


# ---------- link collection (mime) ----------


def test_html_anchors_are_collected_before_stripping_and_bare_urls_added():
    raw = json.loads((FIXTURES / "01_nptel_quiz_with_noise_links.json").read_text())
    message = parse_message(raw)
    by_url = {link.url: link.text for link in message.links}
    assert by_url["https://onlinecourses.nptel.ac.in/noc26_cs10/unit?unit=5&assessment=20"] == (
        "Attempt quiz"
    )
    assert by_url["https://twitter.com/nptelindia"] == "Follow us"
    assert "https://nptel.ac.in/unsubscribe?u=1" in by_url


def test_plain_text_urls_are_collected_without_trailing_punctuation():
    raw = json.loads((FIXTURES / "04_gform_plain_link.json").read_text())
    urls = [link.url for link in parse_message(raw).links]
    assert urls == ["https://forms.gle/AbC123xyz"]


def test_links_are_kept_out_of_repr_and_deduplicated():
    raw = json.loads((FIXTURES / "02_nptel_plain_numeric_date.json").read_text())
    message = parse_message(raw)
    assert "onlinecourses" not in repr(message)
    urls = [link.url for link in message.links]
    assert len(urls) == len(set(urls))


# ---------- action links ----------


def test_nptel_email_with_unsubscribe_social_and_quiz_links_picks_the_quiz():
    message = parse_message(
        json.loads((FIXTURES / "01_nptel_quiz_with_noise_links.json").read_text())
    )
    choice = pick_action_link(message)
    assert choice.url.endswith("unit?unit=5&assessment=20")
    assert choice.link_type == "action"
    assert 0.5 <= choice.confidence <= 1.0


def test_google_form_email_stores_the_forms_link():
    message = parse_message(json.loads((FIXTURES / "05_gform_html_weekday.json").read_text()))
    assert categorize(message, COLLEGE) == "gform"
    assert pick_action_link(message).url == (
        "https://docs.google.com/forms/d/e/1FAIpQLSdSynthetic/viewform"
    )


def test_email_without_a_useful_link_stores_the_gmail_fallback():
    message = make(
        body="Nothing to click.",
        links=[
            Link("mailto:a@example.test", "write to us"),
            Link("https://example.test/unsubscribe", "Unsubscribe"),
            Link("https://facebook.com/page", "Facebook"),
        ],
    )
    choice = pick_action_link(message)
    assert choice.link_type == "gmail"
    assert choice.url == "https://mail.google.com/mail/u/0/#all/m1"
    assert choice.confidence == 0.0


@pytest.mark.parametrize(
    "link",
    [
        Link("mailto:x@example.test", "submit"),
        Link("https://example.test/a/unsubscribe?x=1", "submit"),
        Link("https://example.test/quiz", "Manage preferences"),
        Link("https://example.test/privacy-policy", ""),
        Link("https://example.test/terms-of-service", ""),
        Link("https://twitter.com/nptel/quiz", "quiz"),
        Link("https://www.linkedin.com/company/x/apply", "apply"),
        Link("https://example.test/assets/quiz.png", "quiz"),
        Link("https://example.test/t/pixel.gif?id=1", ""),
    ],
)
def test_noise_links_are_dropped(link):
    assert pick_action_link(make(links=[link])).link_type == "gmail"


def test_a_page_called_midterms_is_not_mistaken_for_terms():
    link = Link("https://example.test/midterms/quiz", "Start quiz")
    assert pick_action_link(make(links=[link])).link_type == "action"


def test_brand_domain_and_subdomains_score_up():
    message = make(
        sender="NPTEL <noreply@mail.nptel.ac.in>",
        links=[
            Link("https://other.example/course", "link"),
            Link("https://onlinecourses.nptel.ac.in/x/course", "link"),
        ],
    )
    assert pick_action_link(message).url == "https://onlinecourses.nptel.ac.in/x/course"


def test_a_bare_homepage_is_not_good_enough():
    message = make(sender="NPTEL <a@nptel.ac.in>", links=[Link("https://nptel.ac.in/", "NPTEL")])
    assert pick_action_link(message).link_type == "gmail"


# ---------- categories ----------


def test_category_rule_order_nptel_before_gform_before_internship():
    form = Link("https://forms.gle/x")
    assert categorize(make(subject="NPTEL internship", links=[form]), COLLEGE) == "nptel"
    assert categorize(make(subject="Internship interview", links=[form]), COLLEGE) == "gform"
    assert categorize(make(subject="Internship interview"), COLLEGE) == "internship"


def test_college_requires_an_allowlisted_domain():
    message = make(sender="Office <o@cse.college.example.edu>", subject="Timetable")
    assert categorize(message, COLLEGE) == "college"
    assert categorize(message, []) == "unknown"


def test_nptel_by_sender_domain_without_the_word_in_the_subject():
    assert categorize(make(sender="X <x@nptel.ac.in>", subject="Your certificate"), []) == "nptel"


# ---------- deadlines ----------


def test_day_first_dates_05_10_2026_is_5_october_not_10_may():
    got = deadline_of("Last date: 05/10/2026")
    assert (got.astimezone().year, got.month) == (2026, 10) or True  # tz-independent check below
    ist = got.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Kolkata"))
    assert (ist.month, ist.day) == (10, 5)


def test_date_only_becomes_2359_ist():
    got = deadline_of("Last date: 20 Jan 2026").astimezone(
        __import__("zoneinfo").ZoneInfo("Asia/Kolkata")
    )
    assert (got.hour, got.minute) == (23, 59)


def test_explicit_times_are_respected():
    ist = __import__("zoneinfo").ZoneInfo("Asia/Kolkata")
    assert deadline_of("Deadline: 20 Jan 2026 11:59 PM").astimezone(ist).hour == 23
    assert deadline_of("Deadline: 20 Jan 2026, 9 AM").astimezone(ist).hour == 9
    assert deadline_of("Deadline: 20 Jan 2026 18:30").astimezone(ist).minute == 30


def test_send_date_and_footer_year_are_not_deadlines():
    text = (
        "Sent: 01/01/2026\nPosted on 2 January 2026\nPlease read the notice.\n"
        "(c) 2026 College. All rights reserved. Terms of use 2026."
    )
    result = extract_deadline(text, RECEIVED, RECEIVED - timedelta(days=1))
    assert result.deadline is None and result.candidates == 0


def test_a_date_after_the_cue_is_picked_not_the_one_before_it():
    result = extract_deadline("Posted 2 Jan 2026. Last date: 18 Jan 2026.", RECEIVED, RECEIVED)
    ist = result.deadline.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Kolkata"))
    assert (ist.month, ist.day) == (1, 18)


def test_confidence_is_09_for_one_candidate_and_06_for_several():
    one = extract_deadline("Deadline: 20 Jan 2026. Last date: 20 Jan 2026.", RECEIVED, RECEIVED)
    many = extract_deadline(
        "Last date: 12 Jan 2026. Registration ends on 20 Jan 2026.", RECEIVED, RECEIVED
    )
    assert (one.confidence, one.candidates) == (CONF_ONE, 1)
    assert (many.confidence, many.candidates) == (CONF_MANY, 2)
    assert many.deadline < datetime(2026, 1, 13, tzinfo=UTC)  # earliest wins


def test_past_dates_are_dropped():
    result = extract_deadline("Last date: 20 Jan 2026", RECEIVED, datetime(2026, 2, 1, tzinfo=UTC))
    assert result.deadline is None


def test_only_the_120_characters_after_a_cue_are_searched():
    far = "Last date to apply: " + "x " * 70 + "20 Jan 2026"
    assert deadline_of(far) is None
    assert deadline_of("Last date to apply: " + "x " * 20 + "20 Jan 2026") is not None


def test_snippet_stops_at_the_next_line():
    assert deadline_of("Deadline: soon\n20 Jan 2026") is None


# ---------- priority ----------


def test_priority_rules():
    now = RECEIVED
    assert priority_for("college", now + timedelta(hours=47), now) == "urgent"
    assert priority_for("nptel", now + timedelta(hours=49), now) == "important"
    assert priority_for("internship", None, now) == "important"
    assert priority_for("gform", now + timedelta(days=9), now) == "important"
    assert priority_for("college", now + timedelta(days=9), now) == "regular"
    assert priority_for("unknown", None, now) == "regular"


# ---------- extension matching ----------


def test_same_topic_matches_reminders_and_extensions():
    assert same_topic("NPTEL: Assignment 3 reminder", "Assignment 3 deadline extended")
    assert same_topic("Re: Hackathon registration form", "Hackathon registration form")


def test_same_topic_does_not_merge_different_numbered_items():
    assert not same_topic("NPTEL: Assignment 3 reminder", "NPTEL: Assignment 4 reminder")


def test_same_topic_ignores_the_extended_date_in_the_subject():
    assert same_topic("Assignment 3 due", "Assignment 3 deadline extended to 25 Jan 2026")


def test_same_topic_rejects_unrelated_subjects():
    assert not same_topic("Semester 5 timetable", "Hackathon registration form")


def test_clean_title_strips_reply_prefixes():
    assert clean_title("Re: Fwd: RE: Assignment 3") == "Assignment 3"
    assert clean_title("   ") == "(no subject)"
