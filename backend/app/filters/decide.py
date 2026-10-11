"""The one decision per message: ignore / drop / suspicious / task / review.

Pure function of the parsed message and the user's settings (no database, no network, links are
never fetched), so every rule can be tested with a fixture. The patterns live in
triage_rules.py. Priority: ignore > done > suspicious > task (see that file's docstring).

Only labels and short reasons come out of here, never quoted text from a message.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parseaddr

from app.filters.auth_check import DEFAULT_BRANDS, check_authenticity
from app.filters.deadline import DeadlineResult, extract_deadline
from app.filters.links import LinkChoice, pick_action_link
from app.filters.scam import QUARANTINE_THRESHOLD, ScamVerdict, score_message
from app.filters.triage_rules import (
    BODY_CHARS,
    BODY_HEAD_CHARS,
    DEADLINE_CONFLICT_GAP,
    DONE_RULES,
    IGNORE_BODY_HEAD,
    IGNORE_SUBJECT,
    SOURCE_ACTION_CUES,
)
from app.ingest.mime import ParsedMessage
from app.ingest.query import sender_matches
from app.sources import Source, find_source, matching_keyword

# Decision kinds
IGNORE, DROP, SUSPICIOUS, TASK, REVIEW = "ignore", "drop", "suspicious", "task", "review"

MIN_BRAND_CHARS = 3  # a source called "IT" must not match every display name containing "it"


@dataclass(frozen=True)
class Decision:
    kind: str
    reason: str
    source: Source | None = None  # the source the sender belongs to (None: keyword match)
    keyword: str | None = None
    deadline: DeadlineResult | None = None
    link: LinkChoice | None = None
    scam: ScamVerdict | None = None  # set for suspicious keyword mail
    reasons: list[str] = field(default_factory=list)  # human labels shown for suspicious mail

    @property
    def source_type(self) -> str:
        return self.source.type if self.source else "keyword"

    @property
    def source_name(self) -> str:
        if self.source:
            return self.source.name
        return (self.keyword or "keyword").title()


def _first(rules, text: str) -> str | None:
    return next((label for label, pattern in rules if pattern.search(text)), None)


def _claimed_source(sender: str | None, sources: Iterable[Source]) -> Source | None:
    """A source whose name appears in the display name although the address is not theirs.
    A source with no senders yet has nothing to impersonate, so it is skipped."""
    display = parseaddr(sender or "")[0].lower()
    for source in sources:
        name = source.name.lower()
        if source.senders and len(name) >= MIN_BRAND_CHARS and name in display:
            return source
    return None


def decide(
    message: ParsedMessage,
    *,
    sources: list[Source],
    keywords: list[str],
    block_senders: list[str],
    require_deadline: bool,
    received_or_now: datetime,
    now: datetime,
    brands: dict[str, tuple[str, ...]] = DEFAULT_BRANDS,
) -> Decision:
    subject = message.subject or ""
    body = message.body_text or ""
    head = f"{subject}\n{body[:BODY_HEAD_CHARS]}"

    # (a) ignore: wins over everything, including a trusted source.
    label = _first(IGNORE_SUBJECT, subject) or _first(IGNORE_BODY_HEAD, body[:BODY_HEAD_CHARS])
    if label:
        return Decision(IGNORE, f"ignored: {label}")
    # (b) done / confirmation: nothing left to do.
    label = _first(DONE_RULES, head)
    if label:
        return Decision(DROP, f"dropped: {label}")

    source = find_source(message.sender, sources)
    auth = check_authenticity(message.sender, message.authentication_results, brands)

    if source is not None:
        # (c) a source sender
        if not auth.trusted:
            reason = f"claims to be {source.name} but failed authentication"
            return Decision(SUSPICIOUS, reason, source, reasons=[reason])
        text = f"{subject}\n{body[:BODY_CHARS]}"
        cue = _first(SOURCE_ACTION_CUES, text)
        if cue is None:
            return Decision(DROP, "dropped: source mail with no action cue", source)
        return _task_decision(message, source, None, f"task: {cue}", received_or_now, now,
                              require_deadline, need_deadline=False)  # fmt: skip

    # Not a source. Someone using a source's name in the display name is impersonating it.
    claimed = _claimed_source(message.sender, sources)
    if claimed is not None:
        why = "failed authentication" if not auth.trusted else "is not sent from its domain"
        reason = f"claims to be {claimed.name} but {why}"
        return Decision(SUSPICIOUS, reason, claimed, reasons=[reason])

    # (d) watch keyword from a sender who is not a source
    keyword = matching_keyword(f"{subject}\n{body[:BODY_CHARS]}", keywords)
    if keyword is None:
        return Decision(DROP, "dropped: not from a source and no watch keyword")
    if sender_matches(message.sender, block_senders):
        return Decision(DROP, "dropped: blocked sender", keyword=keyword)
    scam = score_message(message, brands)
    if scam.score >= QUARANTINE_THRESHOLD:
        return Decision(
            SUSPICIOUS, f"keyword mail scored {scam.score} as a scam", keyword=keyword,
            scam=scam, reasons=list(scam.reasons),
        )  # fmt: skip
    if not auth.trusted:
        return Decision(
            DROP, "dropped: keyword mail from an unauthenticated sender", keyword=keyword
        )
    return _task_decision(message, None, keyword, f"task: keyword {keyword}", received_or_now, now,
                          require_deadline, need_deadline=True)  # fmt: skip


def _task_decision(
    message: ParsedMessage,
    source: Source | None,
    keyword: str | None,
    reason: str,
    received: datetime,
    now: datetime,
    require_deadline: bool,
    *,
    need_deadline: bool,
) -> Decision:
    deadline = extract_deadline(f"{message.subject}\n{message.body_text}", received, now)
    if deadline.deadline is None and (need_deadline or require_deadline):
        why = "no deadline found" if need_deadline else "no deadline and a deadline is required"
        return Decision(DROP, f"dropped: {why}", source, keyword, deadline)
    link = pick_action_link(message)
    # (f) review only when several different deadlines look equally likely
    conflict = (
        deadline.candidates > 1
        and deadline.latest is not None
        and deadline.deadline is not None
        and deadline.latest - deadline.deadline > DEADLINE_CONFLICT_GAP
    )
    if conflict:
        return Decision(
            REVIEW, "review: several different deadlines", source, keyword, deadline, link
        )
    return Decision(TASK, reason, source, keyword, deadline, link)
