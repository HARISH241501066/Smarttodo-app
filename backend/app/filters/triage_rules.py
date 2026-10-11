# ruff: noqa: E501  (regular expressions read best on one line)
"""Triage rules: the one file to edit when an email is sorted wrongly.

Every email that passes the scam filters is sorted into one of four kinds, BEFORE any deadline
is looked for:

    ignore   OTPs, sign-in links, password resets, security alerts, receipts, order and account
             notices. Nothing is stored except that the message was handled.
    update   Results, scores, certificates, "content is live", announcements: worth seeing, but
             there is nothing to do. Shown in the Updates tab.
    task     Something to do: an assignment or quiz, a form, a registration, a deadline. Also
             anything from a sender on the "Always turn into tasks" list (but not if it is an
             ignore message, so a tracked portal's OTP is still ignored).
    review   Only when the signals conflict (strong "do this" AND strong "just FYI" cues in the
             same email), or the email gives several different deadlines.

How to read the rules
---------------------
* Each rule is (label, regular expression). Matching is case-insensitive. The label is shown in
  logs and in the "reason", so keep it short and human.
* STRONG cues are decisive on their own; WEAK cues only break ties.
* Prefer short, specific phrases over single common words (a bare "form" matches far too much).
* To stop something being ignored/updated/tasked, delete or tighten its line. To catch more,
  add a line. `python -m pytest tests/test_triage.py` shows whether anything else changed.
"""

import re
from datetime import timedelta

_I = re.IGNORECASE


def rules(*pairs: tuple[str, str]) -> list[tuple[str, re.Pattern[str]]]:
    return [(label, re.compile(pattern, _I)) for label, pattern in pairs]


# ---------------------------------------------------------------------------------------------
# IGNORE: never a task, never an update. Checked first, and it wins over everything else,
# including the always-track list.
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
# TASK cues, matched against subject + body (first BODY_CHARS characters).
# ---------------------------------------------------------------------------------------------

BODY_CHARS = 4000

TASK_STRONG = rules(
    (
        "assignment is live/due",
        r"\bassignments?\b(?![^.\n]{0,30}\b(results?|scores?|summary|grades?|feedback|marks)\b)"
        r"[^.\n]{0,30}\b(is |are |has been |have been |now )?(live|released|published|"
        r"open(ed)?|due|available|posted|submission|deadline)\b",
    ),
    (
        "new assignment/quiz/test",
        r"\bnew (assignment|quiz|test|exam|task)s?\b|\b(assignment|quiz) published\b",
    ),
    (
        "quiz open/due/published",
        r"\bquiz(zes)?\b(?![^.\n]{0,30}\b(results?|scores?|summary|grades?|feedback|marks)\b)"
        r"[^.\n]{0,30}\b(is |are |now )?(due|open|live|published|released|available|"
        r"closes?|closing|starts?|starting)\b",
    ),
    (
        "attempt/take the quiz",
        r"\b(attempt|take|start|begin|complete|solve) (the |this |your |a )?(quiz|assignment|test|"
        r"exam|survey)\b|\bquiz is open\b",
    ),
    (
        "submit by",
        r"\bsubmit(ted)?\b[^.\n]{0,40}\b(by|before|on|until|till)\b|\bsubmission\b[^.\n]{0,25}\b(due|deadline|closes?|by|before)\b",
    ),
    ("closes on", r"\b(closes?|closing|ends?|ending)\s+(on|at|by)\b|\bclosing date\b"),
    (
        "last date / deadline",
        r"\blast (date|day)\b|\bdeadline\b|\bdue (on|by|date|today|tomorrow|soon)\b",
    ),
    (
        "register / apply by",
        r"\bregist(er|ration)\b[^.\n]{0,25}\b(now|by|before|open|opens|closes?|ends?|deadline|"
        r"last date|required)\b|\bapply\b[^.\n]{0,15}\b(now|by|before|today)\b",
    ),
    (
        "fill the form",
        r"\bfill( in| out|-in)?\b[^.\n]{0,20}\b(form|survey|questionnaire)\b|\bgoogle form\b",
    ),
    ("complete by", r"\bcomplete\b[^.\n]{0,30}\b(by|before|within)\b"),
    # (?<!no ): "No action required" is the opposite of a task.
    ("action required", r"(?<!no )\baction (is )?(required|needed)\b|\bresponse (is )?required\b"),
    (
        "please do something",
        r"\b(please|kindly) (submit|complete|fill|register|apply|upload|attend|confirm|respond|reply|"
        r"pay|bring|collect|report)\b",
    ),
)

TASK_WEAK = rules(
    ("assignment", r"\bassignments?\b"),
    ("register/apply", r"\b(register|registration|apply|application)\b"),
    ("form", r"\bforms?\b"),
    ("submit", r"\bsubmit\b"),
    ("mandatory", r"\b(mandatory|compulsory|must attend|required to)\b"),
    ("please", r"\b(please|kindly)\b"),
)

# ---------------------------------------------------------------------------------------------
# UPDATE cues: informational, nothing to do.
# ---------------------------------------------------------------------------------------------

UPDATE_STRONG = rules(
    (
        "results out",
        r"\bresults?\b[^.\n]{0,25}\b(declared|published|announced|out|released|available)\b|"
        r"\byour (results?|scores?|grades?|marks|scorecard)\b",
    ),
    ("score/grade", r"\b(scored|scores?|grades?|marks|scorecard|percentile|rank)\b"),
    (
        "quiz summary",
        r"\b(quiz|assignment|test|exam) (summary|result|results|feedback)\b|\bsummary\b[^.\n]{0,20}\b(quiz|assignment)\b",
    ),
    (
        "certificate issued",
        r"\bcertificate\b[^.\n]{0,30}\b(issued|generated|ready|available|released|downloaded|is out)\b|"
        r"\byour certificate\b",
    ),
    (
        "no action needed",
        r"\bno action (is )?(required|needed)\b|\bfor your (information|reference)\b|\bfyi\b|"
        r"\bfor your records\b",
    ),
)

UPDATE_WEAK = rules(
    (
        "content is live",
        r"\bcontent\b[^.\n]{0,25}\b(is |are |now )?(live|available|uploaded|released|added)\b",
    ),
    (
        "lecture uploaded",
        r"\b(lectures?|videos?|slides|notes|materials?|recordings?)\b[^.\n]{0,25}\b(uploaded|added|"
        r"available|released|posted)\b",
    ),
    ("week content", r"\bweek \d+\b[^.\n]{0,25}\b(content|lectures?|videos?|materials?)\b"),
    ("announcement", r"\b(announcement|newsletter|notice|circular|bulletin|digest|update)\b"),
)

# ---------------------------------------------------------------------------------------------
# Several different deadlines in one email ("Last date 12 Jan ... late fee until 20 Jan") are
# ambiguous, so it goes to review. Dates closer together than this count as the same deadline.
# ---------------------------------------------------------------------------------------------

DEADLINE_CONFLICT_GAP = timedelta(hours=12)
