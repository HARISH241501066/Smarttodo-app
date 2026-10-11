"""Source-based decision rules, over synthetic fixtures (tests/fixtures/source_emails)."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.filters.decide import DROP, IGNORE, REVIEW, SUSPICIOUS, TASK, decide
from app.ingest.mime import parse_message
from app.sources import Source

DIR = Path(__file__).parent / "fixtures" / "source_emails"
MANIFEST = json.loads((DIR / "expected.manifest").read_text())
NOW = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)

SOURCES = [
    Source("NPTEL", "nptel", ("nptel.ac.in",)),
    Source("Digii", "digii", ("digii.example",)),
]
KEYWORDS = ["hackathon", "ideathon", "datathon"]
BLOCK = ["blocked.example"]


def message(name: str):
    return parse_message(json.loads((DIR / f"{name}.json").read_text()))


def run(name: str, *, require_deadline=False, sources=SOURCES, block=BLOCK, keywords=KEYWORDS):
    msg = message(name)
    return decide(
        msg,
        sources=sources,
        keywords=keywords,
        block_senders=block,
        require_deadline=require_deadline,
        received_or_now=msg.received_at or NOW,
        now=NOW,
    )


def name_of(prefix: str) -> str:
    return next(n for n in MANIFEST if n.split("_", 1)[1].startswith(prefix))


@pytest.mark.parametrize("name", sorted(MANIFEST))
def test_every_fixture_gets_its_expected_decision(name):
    expected = MANIFEST[name]
    got = run(name)
    assert got.kind == expected["kind"], (name, got.kind, got.reason)
    if expected["kind"] == TASK:
        deadline = got.deadline.deadline
        assert (deadline.astimezone(UTC).isoformat() if deadline else None) == expected["deadline"]
        assert (got.link.url if got.link.link_type == "action" else None) == expected["link"]


def test_digii_new_quiz_table_is_a_task_with_the_take_quiz_link():
    got = run(name_of("task_digii_new_quiz_table"))
    assert got.kind == TASK and got.source.name == "Digii" and got.source_type == "digii"
    # "Closes on : 18 Oct 2026 | 11:59 PM" -> 23:59 IST
    assert got.deadline.deadline.isoformat() == "2026-10-18T18:29:00+00:00"
    assert got.link.link_type == "action"
    assert got.link.url == "https://app.digii.example/student/quiz/start?quizId=9"


def test_digii_submitted_confirmation_is_dropped():
    got = run(name_of("drop_digii_you_have_submitted"))
    assert got.kind == DROP and "submitted" in got.reason


def test_nptel_week9_without_a_date_is_a_task_with_no_deadline():
    got = run(name_of("task_nptel_week9"))
    assert got.kind == TASK and got.deadline.deadline is None
    assert got.source_name == "NPTEL"


def test_require_deadline_drops_a_source_mail_without_one():
    got = run(name_of("task_nptel_week9"), require_deadline=True)
    assert got.kind == DROP and "deadline" in got.reason


def test_require_deadline_keeps_a_source_mail_that_has_one():
    assert run(name_of("task_digii_new_quiz_table"), require_deadline=True).kind == TASK


def test_hackathon_from_unknown_authenticated_sender_with_deadline_is_a_keyword_task():
    got = run(name_of("task_hackathon_unknown"))
    assert got.kind == TASK and got.source is None
    assert (got.source_type, got.source_name, got.keyword) == ("keyword", "Hackathon", "hackathon")


def test_hackathon_without_a_deadline_is_dropped_even_without_require_deadline():
    got = run(name_of("drop_hackathon_no_deadline"), require_deadline=False)
    assert got.kind == DROP and "no deadline" in got.reason


def test_hackathon_with_fee_and_payment_phrases_is_suspicious():
    got = run(name_of("suspicious_hackathon_fee"))
    assert got.kind == SUSPICIOUS and got.scam.score >= 4
    assert any("fee" in r.lower() for r in got.reasons)


def test_hackathon_from_a_sender_who_failed_nothing_but_is_unauthenticated_is_dropped():
    got = run(name_of("drop_hackathon_unauthenticated"))
    assert got.kind == DROP and "unauthenticated" in got.reason


def test_blocked_sender_never_becomes_a_keyword_task():
    assert run(name_of("drop_hackathon_blocked")).kind == DROP
    assert run(name_of("drop_hackathon_blocked"), block=[]).kind == TASK


def test_nptel_display_name_from_a_failing_domain_is_suspicious():
    got = run(name_of("suspicious_nptel_display_name"))
    assert got.kind == SUSPICIOUS
    assert got.reason == "claims to be NPTEL but failed authentication"


def test_real_source_domain_that_fails_authentication_is_suspicious():
    got = run(name_of("suspicious_source_domain"))
    assert got.kind == SUSPICIOUS
    assert got.reason == "claims to be NPTEL but failed authentication"


def test_fakenptel_is_not_the_nptel_source():
    got = run(name_of("suspicious_fakenptel"))
    assert got.kind == SUSPICIOUS and got.source.name == "NPTEL"
    assert "claims to be NPTEL" in got.reason  # judged as an impostor, not as a source sender


def test_otp_and_signin_link_from_a_source_are_ignored():
    assert run(name_of("ignore_source_otp")).kind == IGNORE
    assert run(name_of("ignore_source_signin_link")).kind == IGNORE


def test_source_mail_with_no_action_cue_is_dropped():
    assert run(name_of("drop_source_announcement")).kind == DROP


def test_conflicting_deadlines_are_the_only_route_to_review():
    got = run(name_of("review_source_two"))
    assert got.kind == REVIEW
    kinds = {run(n).kind for n in MANIFEST}
    assert REVIEW in kinds
    assert sum(1 for n in MANIFEST if run(n).kind == REVIEW) == 1


def test_scam_heuristics_are_skipped_for_an_authenticated_source_sender():
    """A real source may mention fees and payments; only the keyword path scores scams."""
    msg = message(name_of("task_nptel_week9"))
    object.__setattr__(
        msg,
        "body_text",
        msg.body_text
        + "\nExam registration fee: pay via UPI to the portal. Last date: 25 Oct 2026.",
    )
    got = decide(
        msg,
        sources=SOURCES,
        keywords=KEYWORDS,
        block_senders=[],
        require_deadline=False,
        received_or_now=NOW,
        now=NOW,
    )
    assert got.kind == TASK


def test_priority_ignore_beats_done_beats_suspicious_beats_task():
    # ignore beats a failing-authentication source
    msg = message(name_of("suspicious_source_domain"))
    object.__setattr__(msg, "subject", "Your OTP for login")
    got = decide(
        msg, sources=SOURCES, keywords=KEYWORDS, block_senders=[], require_deadline=False,
        received_or_now=NOW, now=NOW,
    )  # fmt: skip
    assert got.kind == IGNORE
    # done beats suspicious
    msg = message(name_of("suspicious_source_domain"))
    object.__setattr__(msg, "subject", "You have submitted the quiz")
    got = decide(
        msg, sources=SOURCES, keywords=KEYWORDS, block_senders=[], require_deadline=False,
        received_or_now=NOW, now=NOW,
    )  # fmt: skip
    assert got.kind == DROP


def test_nothing_matches_when_there_are_no_sources_and_no_keywords():
    got = run(name_of("task_digii_new_quiz_table"), sources=[], keywords=[])
    assert got.kind == DROP and "not from a source" in got.reason


def test_a_digii_source_with_no_senders_trusts_nothing():
    empty = [Source("Digii", "digii", ())]
    assert run(name_of("task_digii_new_quiz_table"), sources=empty, keywords=[]).kind == DROP


def test_reasons_never_quote_the_message():
    for name in MANIFEST:
        got = run(name)
        msg = message(name)
        assert msg.subject not in got.reason
        assert all(msg.body_text[:40] not in r for r in got.reasons)
