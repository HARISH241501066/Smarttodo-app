"""Filter layers 1-3: authenticity check and scam score, on pure functions and fixtures."""

import json
from pathlib import Path

import pytest

from app.filters.auth_check import check_authenticity, parse_authentication_results
from app.filters.scam import QUARANTINE_THRESHOLD, score_message
from app.ingest.mime import ParsedMessage, parse_message

FIXTURES = Path(__file__).parent / "fixtures" / "filter_emails"


def load(kind: str) -> list[tuple[str, ParsedMessage]]:
    paths = sorted((FIXTURES / kind).glob("*.json"))
    return [(p.stem, parse_message(json.loads(p.read_text()))) for p in paths]


def make(sender="Someone <someone@example.test>", body="Hello.", subject="Hi", auth=""):
    return ParsedMessage(
        message_id="m1",
        thread_id="t1",
        labels=("INBOX",),
        received_at=None,
        sender_email="",
        sender=sender,
        subject=subject,
        body_text=body,
        has_attachments=False,
        authentication_results=auth,
    )


DMARC_PASS = "mx.google.com; dmarc=pass (p=NONE) header.from=example.test"


# ---------- fixtures ----------


def test_fixture_counts():
    assert len(load("scam")) == 10
    assert len(load("legit")) == 15


@pytest.mark.parametrize(
    "name,message", load("scam"), ids=lambda v: v if isinstance(v, str) else ""
)
def test_every_scam_fixture_is_quarantined_with_a_reason(name, message):
    verdict = score_message(message)
    assert verdict.quarantine, f"{name} scored {verdict.score}: {verdict.reasons}"
    assert len(verdict.reasons) >= 1


@pytest.mark.parametrize(
    "name,message", load("legit"), ids=lambda v: v if isinstance(v, str) else ""
)
def test_no_legitimate_fixture_is_quarantined(name, message):
    verdict = score_message(message)
    assert not verdict.quarantine, f"{name} scored {verdict.score}: {verdict.reasons}"


# ---------- authentication ----------


def test_parse_authentication_results_reads_all_three():
    r = parse_authentication_results(
        "mx.google.com; dkim=pass header.i=@a.com; spf=fail smtp.mailfrom=a.com; dmarc=fail"
    )
    assert r == {"spf": "fail", "dkim": "pass", "dmarc": "fail"}


def test_any_passing_dkim_signature_counts():
    r = parse_authentication_results("x; dkim=fail header.i=@a; dkim=pass header.i=@b")
    assert r["dkim"] == "pass"


def test_trusted_with_dmarc_pass_alone():
    assert check_authenticity("A <a@x.test>", "x; dmarc=pass").trusted


def test_trusted_with_spf_and_dkim_pass_without_dmarc():
    assert check_authenticity("A <a@x.test>", "x; spf=pass; dkim=pass").trusted


def test_spf_pass_alone_is_not_trusted():
    assert not check_authenticity("A <a@x.test>", "x; spf=pass; dkim=none; dmarc=fail").trusted


def test_missing_header_counts_as_untrusted_without_crashing():
    for header in (None, "", "   ", "garbage ;;; ===="):
        verdict = check_authenticity("A <a@x.test>", header)
        assert verdict.trusted is False
        assert verdict.dmarc is None
    assert score_message(make(auth="")).score == 2  # untrusted bonus only


def test_nptel_team_from_gmail_is_flagged_as_impersonation():
    verdict = check_authenticity("NPTEL Team <nptel.help@gmail.com>", "x; dmarc=pass")
    assert verdict.impersonated_brand == "nptel"
    scored = score_message(make(sender="NPTEL Team <nptel.help@gmail.com>", auth="x; dmarc=pass"))
    assert any("nptel" in r for r in scored.reasons)
    assert scored.quarantine


def test_real_brand_domain_and_subdomains_are_not_impersonation():
    assert check_authenticity("NPTEL <a@nptel.ac.in>", "").impersonated_brand is None
    assert check_authenticity("NPTEL <a@mail.nptel.ac.in>", "").impersonated_brand is None
    assert check_authenticity("NPTEL <a@evilnptel.ac.in>", "").impersonated_brand == "nptel"


def test_brand_list_is_configurable():
    brands = {"acme": ("acme.test",)}
    assert check_authenticity("Acme HR <a@other.test>", "", brands).impersonated_brand == "acme"
    assert check_authenticity("NPTEL <a@gmail.com>", "", brands).impersonated_brand is None


# ---------- scoring ----------


def test_dmarc_pass_with_no_scam_phrases_scores_below_threshold():
    verdict = score_message(make(body="The class moves to room 204 on Monday.", auth=DMARC_PASS))
    assert verdict.score < QUARANTINE_THRESHOLD
    assert verdict.score == 0
    assert verdict.reasons == []


def test_reasons_are_labels_and_never_quote_the_body():
    body = "Pay a registration fee of Rs. 500 to SECRETPERSON via UPI immediately."
    verdict = score_message(make(body=body, auth=""))
    assert verdict.reasons
    assert not any("SECRETPERSON" in r or "500" in r for r in verdict.reasons)


@pytest.mark.parametrize(
    "body",
    [
        "Pay the processing fee of Rs. 100 now.",
        "You get guaranteed placement.",
        "Message us on WhatsApp +91 90000 00009 today.",
        "You are selected without interview.",
    ],
)
def test_one_phrase_alone_on_a_trusted_sender_is_not_enough(body):
    verdict = score_message(make(body=body, auth=DMARC_PASS))
    assert verdict.reasons
    assert verdict.score < QUARANTINE_THRESHOLD


def test_institutional_fees_are_not_scam_signals():
    body = "Pay the exam registration fee and the semester fee on the portal."
    assert score_message(make(body=body, auth=DMARC_PASS)).score == 0


def test_free_mail_company_claim_adds_three():
    verdict = score_message(
        make(sender="Infotech Solutions HR <jobs@gmail.com>", auth="x; dmarc=pass")
    )
    assert verdict.score == 3
    assert any("free-mail" in r for r in verdict.reasons)


def test_free_mail_person_without_company_claim_is_clean():
    verdict = score_message(make(sender="Ravi <ravi@gmail.com>", auth="x; dmarc=pass"))
    assert verdict.score == 0


def test_untrusted_adds_two():
    assert score_message(make(auth="x; spf=fail; dkim=fail; dmarc=fail")).score == 2
