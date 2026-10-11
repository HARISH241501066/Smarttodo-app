"""Layer 3: weighted scam score.

Phrase signals look at subject + body; authenticity signals come from auth_check. Reasons
are fixed human-readable labels, never quoted text, so nothing from a body is ever stored.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.filters.auth_check import DEFAULT_BRANDS, AuthVerdict, check_authenticity
from app.ingest.mime import ParsedMessage
from app.ingest.query import sender_matches

QUARANTINE_THRESHOLD = 4

W_UNTRUSTED = 2
W_IMPERSONATION = 4
W_FREE_MAIL_COMPANY = 3

_I = re.IGNORECASE | re.DOTALL
_PAY_WORD = r"(?:pay(?:ment)?|transfer|deposit|send|fee|fees)"
_PAY_CHANNEL = r"(?:upi|gpay|google\s*pay|phonepe|paytm|bank\s+account|account\s+number|ifsc)"
_URGENT = (
    r"(?:immediately|urgent(?:ly)?|within\s+\d+\s+(?:hours?|minutes?)|today\s+only|"
    r"last\s+chance|act\s+now|hurry)"
)
_PHONE = r"\+?\d[\d\s-]{8,13}\d"

# Fees a real institution charges are not scam signals on their own ("exam registration fee").
_LEGIT_FEE_PREFIX = re.compile(
    r"(?:exam|examination|semester|tuition|course|hostel|library)\W*$", _I
)
_FEE_RE = re.compile(
    r"\b(?:registration|processing|security|refundable|application|training|verification|"
    r"admission|joining|onboarding|kit|document|courier)\s+(?:fee|fees|deposit|charges?)\b",
    _I,
)
FEE_LABEL = "Asks for a registration, processing or similar fee"
FEE_WEIGHT = 2

# (label, weight, pattern) for the remaining phrase signals
PHRASE_SIGNALS: list[tuple[str, int, re.Pattern[str]]] = [
    (
        "Asks you to pay via UPI, bank transfer or a personal account",
        3,
        re.compile(
            rf"\b{_PAY_CHANNEL}\b.{{0,80}}?\b{_PAY_WORD}\b|\b{_PAY_WORD}\b.{{0,80}}?\b{_PAY_CHANNEL}\b",
            _I,
        ),
    ),
    (
        "Promises guaranteed placement, job or returns",
        3,
        re.compile(
            r"\bguarantee[d]?\s+(?:placement|job|internship|selection|returns|income)\b|"
            r"\b100\s*%\s+(?:placement|job|selection)\b|\b(?:placement|job)\s+guarantee[d]?\b",
            _I,
        ),
    ),
    (
        "Asks you to contact a phone number on WhatsApp or Telegram",
        3,
        re.compile(
            rf"(?:whats\s?app|telegram|t\.me/).{{0,80}}?{_PHONE}|"
            rf"{_PHONE}.{{0,80}}?(?:whats\s?app|telegram)",
            _I,
        ),
    ),
    (
        "Says you were selected without an interview or test",
        3,
        re.compile(
            r"\b(?:selected|shortlisted|hired|offer(?:ed)?)\b.{0,80}?\b(?:without|no)\s+"
            r"(?:any\s+)?(?:interview|test|exam)|\bno\s+interview\s+(?:required|needed|necessary)\b",
            _I,
        ),
    ),
    (
        "Combines urgency with a payment demand",
        2,
        re.compile(
            rf"\b{_URGENT}\b.{{0,60}}?\b(?:pay(?:ment)?|transfer|deposit)\b|"
            rf"\b(?:pay(?:ment)?|transfer|deposit)\b.{{0,60}}?\b{_URGENT}\b",
            _I,
        ),
    ),
]

_COMPANY_CLAIM_RE = re.compile(
    r"\b(?:pvt\.?\s*ltd|private\s+limited|technologies|solutions|inc\.|llc|corporation|"
    r"hr\s+(?:team|department)|recruitment\s+team|careers?\s+team|talent\s+acquisition)\b",
    _I,
)


SPOOFED_TRACKED_REASON = "Claims to be an always-track sender but failed authentication"


@dataclass
class ScamVerdict:
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    auth: AuthVerdict | None = None
    tracked: bool = False  # the sender is on the user's "always turn into tasks" list
    trusted_tracked: bool = (
        False  # ...and passed authentication, so the scam heuristics are skipped
    )

    @property
    def quarantine(self) -> bool:
        return self.score >= QUARANTINE_THRESHOLD and not self.trusted_tracked

    def add(self, weight: int, reason: str) -> None:
        self.score += weight
        self.reasons.append(reason)


def _has_fee_request(text: str) -> bool:
    return any(
        not _LEGIT_FEE_PREFIX.search(text[: m.start()][-20:]) for m in _FEE_RE.finditer(text)
    )


def _claims_company(message: ParsedMessage, brand_hit: bool) -> bool:
    if brand_hit:
        return True
    display = message.sender.split("<")[0]
    return bool(_COMPANY_CLAIM_RE.search(f"{display}\n{message.body_text}"))


def is_trusted_tracked(
    message: ParsedMessage,
    always_track: Iterable[str],
    brands: dict[str, tuple[str, ...]] = DEFAULT_BRANDS,
) -> bool:
    """On the user's always-track list AND authenticated (dmarc pass, or spf + dkim pass)."""
    if not sender_matches(message.sender, always_track):
        return False
    return check_authenticity(message.sender, message.authentication_results, brands).trusted


def score_message(
    message: ParsedMessage,
    brands: dict[str, tuple[str, ...]] = DEFAULT_BRANDS,
    always_track: Iterable[str] = (),
) -> ScamVerdict:
    auth = check_authenticity(message.sender, message.authentication_results, brands)
    tracked = sender_matches(message.sender, always_track)
    if tracked and auth.trusted:
        # Legitimate portals mention fees and deadlines; the user asked for this sender, and
        # authentication shows it really is them. Skip the scam-phrase heuristics.
        return ScamVerdict(auth=auth, tracked=True, trusted_tracked=True)
    verdict = ScamVerdict(auth=auth, tracked=tracked)
    text = f"{message.subject}\n{message.body_text}"

    if _has_fee_request(text):
        verdict.add(FEE_WEIGHT, FEE_LABEL)
    for label, weight, pattern in PHRASE_SIGNALS:
        if pattern.search(text):
            verdict.add(weight, label)

    if not auth.trusted:
        verdict.add(W_UNTRUSTED, "Sender failed or lacks SPF/DKIM/DMARC authentication")
    if auth.impersonated_brand:
        verdict.add(
            W_IMPERSONATION,
            f"Display name claims to be {auth.impersonated_brand} but the sender domain differs",
        )
    if auth.free_mail and _claims_company(message, bool(auth.impersonated_brand)):
        verdict.add(
            W_FREE_MAIL_COMPANY, "Claims to be a company but sends from a free-mail address"
        )
    if tracked:  # on the list but failed authentication: possibly someone pretending to be them
        verdict.reasons.append(SPOOFED_TRACKED_REASON)
    return verdict
