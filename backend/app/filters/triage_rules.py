# ruff: noqa: E501  (regular expressions read best on one line)
"""Decision rules: the one file to edit when an email is sorted wrongly.

Mail only gets here if it came from one of the user's sources or mentions a watch keyword. Each
message is then given ONE outcome, in this priority order (see filters/decide.py):

    1. ignore      OTPs, sign-in links, password resets, security alerts, receipts, order
                   confirmations. Never a task, even from a source.
    2. done        "You have submitted ...", "check your result", quiz summaries, scores,
                   certificates. Confirmations of something already done: dropped.
    3. suspicious  Claims to be a source but fails authentication, or a keyword mail that
                   scores as a scam. Held in the Suspicious tab.
    4. task        From a source: authenticated + an action cue. From a keyword match: needs a
                   deadline, authentication and a low scam score.
    (anything else is dropped; "needs review" is only for several conflicting deadlines.)

How to read the rules
---------------------
* Each rule is (label, regular expression). Matching is case-insensitive. The label is shown in
  logs and in the "reason", so keep it short and human.
* Prefer short, specific phrases over single common words (a bare "form" matches far too much).
* To stop something being ignored/dropped/tasked, delete or tighten its line. To catch more,
  add a line. `python -m pytest tests/test_decide.py` shows whether anything else changed.
"""

import re
from datetime import timedelta

_I = re.IGNORECASE


def rules(*pairs: tuple[str, str]) -> list[tuple[str, re.Pattern[str]]]:
    return [(label, re.compile(pattern, _I)) for label, pattern in pairs]


# ---------------------------------------------------------------------------------------------
# IGNORE: never a task. Checked first, and it wins over everything else, even a source.
# ---------------------------------------------------------------------------------------------

# Matched against the SUBJECT only (a body that merely mentions "receipt" must not be dropped).
IGNORE_SUBJECT = rules(
    (
        "one-time code",
        r"\b(otp|one[- ]time (password|passcode|pin|code)|verification code|security code|"
        r"authentication code|login code|sign[- ]?in code|passcode|2fa|two[- ]factor)\b",
    ),
    (
        "sign-in link",
        r"\b(secure |magic )?(sign[- ]?in|log[- ]?in) (link|request|attempt|to your|code)\b|"
        r"\bmagic link\b|\bclick (the )?(link )?(below )?to (sign|log) ?in\b",
    ),
    (
        "password reset",
        r"\b(password (reset|change|changed|recovery)|reset your password|forgot (your )?password|"
        r"change your password)\b",
    ),
    (
        "security alert",
        r"\b(security (alert|notice|warning|code)|critical security|new (sign[- ]?in|login|device)|"
        r"(suspicious|unusual) (sign[- ]?in|login|activity)|was (just )?signed in|"
        r"sign[- ]?in (was )?(blocked|detected)|your account (was|has been) (accessed|locked))\b",
    ),
    (
        "payment receipt",
        r"\b((payment|transaction|booking) (receipt|confirmation|successful|received|failed)|"
        r"receipt (for|of|from)|your (payment )?receipt|payment of .{1,30}(received|successful|failed)|"
        r"thank you for your payment|tax invoice|e-?receipt)\b",
    ),
    (
        "submission receipt",
        r"\b(submission|attempt|response|application) (receipt|confirmation|received|recorded|"
        r"successful|submitted)\b|\bthank you for (submitting|your submission|registering)\b",
    ),
    (
        "order confirmation",
        r"\b(order (confirmation|confirmed|placed|shipped|dispatched|delivered|status|#\s?\w+)|"
        r"your order|shipping (confirmation|update)|(has|have) shipped|out for delivery|"
        r"delivery (update|confirmation)|your package)\b",
    ),
    (
        "account notification",
        r"\b(verify your (email|account|identity)|confirm your (email|account)|"
        r"email (verification|verified|confirmed)|account (created|activated|verified|statement|"
        r"summary|notification|update)|your account (has been|was) (created|activated|verified)|"
        r"new account|(statement|bill) (is )?(ready|available|generated)|subscription (confirmed|renewed|renewal))\b",
    ),
)

# Matched against the first BODY_HEAD_CHARS of the body: OTPs often have a bland subject
# ("Your login") and the code only in the first line.
BODY_HEAD_CHARS = 400
IGNORE_BODY_HEAD = rules(
    (
        "one-time code in body",
        r"\b(otp|one[- ]time (password|passcode|code)|verification code|security code|login code|"
        r"passcode)\b[^.\n]{0,60}\b\d{4,8}\b|\b\d{4,8}\b[^.\n]{0,30}\b(is|as) your\b[^.\n]{0,30}"
        r"\b(otp|code|passcode|pin)\b",
    ),
    (
        "sign-in link in body",
        r"\bclick (the )?(link |button )?(below )?to (securely )?(sign|log) ?in\b|"
        r"\byour (secure )?(sign[- ]?in|log[- ]?in) link\b|\bthis link (will )?expires? in \d+ ?(min|hour)",
    ),
)


# ---------------------------------------------------------------------------------------------
# DONE / CONFIRMATION: the user already did the thing, or it is a result. Dropped by default.
# Matched against subject + the first BODY_HEAD_CHARS of the body.
# ---------------------------------------------------------------------------------------------

DONE_RULES = rules(
    (
        "you have submitted",
        r"\byou (have|'ve) (successfully )?(submitted|completed|attempted|registered|applied)\b",
    ),
    ("submission received", r"\bsubmission (has been )?(received|recorded|successful)\b"),
    ("successfully submitted", r"\bsuccessfully (submitted|registered|applied|completed)\b"),
    ("check your result", r"\bcheck (out )?your (results?|scores?|marks|grades?)\b"),
    ("quiz summary", r"\b(quiz|assignment|test|exam) (summary|results?|feedback)\b"),
    ("your score", r"\byour (scores?|marks|grades?|scorecard|results?)\b"),
    ("attempt recorded", r"\battempt (has been |was |is )?(recorded|saved|submitted|received)\b"),
    (
        "certificate issued",
        r"\bcertificate\b[^.\n]{0,30}\b(issued|generated|ready|available|released)\b|\byour certificate\b",
    ),
)

# ---------------------------------------------------------------------------------------------
# SOURCE ACTION CUES: mail from an authenticated source becomes a task only if it says there is
# something to do. Matched against subject + body (first BODY_CHARS characters). A source mail
# with no cue is dropped (it is an announcement, not a to-do).
# ---------------------------------------------------------------------------------------------

BODY_CHARS = 4000

SOURCE_ACTION_CUES = rules(
    (
        "assignment is live",
        r"\bassignments?\b[^.\n]{0,30}\b(is |are |has been |have been |now )?(live|released|published|open(ed)?)\b",
    ),
    (
        "quiz published/open",
        r"\bquiz(zes)?\b[^.\n]{0,30}\b(is |are |has been |now )?(published|open(ed)?|live|released)\b|\bnew quiz\b",
    ),
    (
        "attempt the quiz",
        r"\b(attempt|take|start|begin) (the |this |your |a )?(quiz|assignment|test|exam)\b",
    ),
    ("submit", r"\bsubmit\b"),
    ("register", r"\bregist(er|ration)\b"),
    ("apply", r"\bapply\b"),
    ("fill the form", r"\bfill( in| out|-in)?\b[^.\n]{0,20}\b(form|survey|questionnaire)\b"),
    ("complete by", r"\bcomplete\b[^.\n]{0,30}\b(by|before|within)\b"),
    ("closes on", r"\b(closes?|closing)\s+(on|at|by)\b|\bclosing date\b"),
    ("last date", r"\blast (date|day)\b"),
    ("deadline / due", r"\bdeadline\b|\bdue\b"),
)

# ---------------------------------------------------------------------------------------------
# Several different deadlines in one email ("Last date 12 Jan ... late fee until 20 Jan") are
# ambiguous, so it goes to review. Dates closer together than this count as the same deadline.
# ---------------------------------------------------------------------------------------------

DEADLINE_CONFLICT_GAP = timedelta(hours=12)
