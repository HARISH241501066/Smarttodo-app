# ruff: noqa: E501  (an embedded HTML email)
"""Synthetic emails for the decision rules (source-based scanning), each with its expected outcome.

Everything here is invented. Re-run after editing:
    python tests/fixtures/build_source_fixtures.py
Expected `kind` is what filters.decide must answer: task, drop, ignore, suspicious or review.
Deadlines are IST wall-clock. The test settings are: sources NPTEL (nptel.ac.in) and Digii
(digii.example), keywords hackathon/ideathon/datathon, block sender blocked.example.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from build_email_fixtures import hdrs, leaf, message, multipart

OUT = Path(__file__).parent / "source_emails"
IST = ZoneInfo("Asia/Kolkata")
CASES: list[dict] = []

NPTEL = "NPTEL <noreply@nptel.ac.in>"
DIGII = "Digii <no-reply@digii.example>"
AUTH = "mx.google.com; dkim=pass; spf=pass; dmarc=pass"
FAIL = "mx.google.com; spf=fail; dkim=none; dmarc=fail"
NONE = ""


def case(name, subject, sender, body, kind, deadline=None, link=None, html=False, auth=AUTH):
    CASES.append(locals() | {"n": len(CASES) + 1})


# ------------------------------------------------------------------ the Digii-style HTML table

DIGII_HTML = """<html><body>
<table width="600" cellpadding="8"><tr><td><h2>New Quiz Published</h2></td></tr>
<tr><td><table>
<tr><td>Course</td><td>:</td><td>Data Structures</td></tr>
<tr><td>Quiz</td><td>:</td><td>Week 9 Quiz</td></tr>
<tr><td>Opens on</td><td>:</td><td>11 Oct 2026 | 9:00 AM</td></tr>
<tr><td>Closes on</td><td>:</td><td>18 Oct 2026 | 11:59 PM</td></tr>
</table></td></tr>
<tr><td><a href="https://app.digii.example/student/quiz/start?quizId=9" style="background:#06c;color:#fff;padding:12px 24px">Take Quiz</a></td></tr>
<tr><td><a href="https://app.digii.example/unsubscribe?u=1">Unsubscribe</a> |
<a href="https://app.digii.example/privacy">Privacy</a></td></tr>
</table></body></html>"""

case(
    "task_digii_new_quiz_table",
    "New Quiz Published: Week 9 Quiz",
    DIGII,
    DIGII_HTML,
    "task",
    "2026-10-18 23:59",
    "https://app.digii.example/student/quiz/start?quizId=9",
    html=True,
)
case(
    "drop_digii_you_have_submitted",
    "You have submitted the quiz: Week 9 Quiz",
    DIGII,
    "You have successfully submitted the Week 9 Quiz. Your attempt has been recorded.\n"
    "You can check your result at https://app.digii.example/student/quiz/result?quizId=9",
    "drop",
)
case(
    "task_nptel_week9_assignment_live_no_date",
    "Week 9 content & Assignment is live",
    NPTEL,
    "Dear learner, the Week 9 content and the Assignment are now live on the course page.\n"
    "Open https://onlinecourses.nptel.ac.in/noc26_cs10/assignment/9 to begin.",
    "task",
    None,
    "https://onlinecourses.nptel.ac.in/noc26_cs10/assignment/9",
)

# ------------------------------------------------------------------ watch keywords

case(
    "task_hackathon_unknown_sender_with_deadline",
    "Smart Hackathon 2026: registrations open",
    "Hack Club <events@hackclub.example>",
    "Join our hackathon this November. Register at https://hackclub.example/apply\n"
    "Last date to register: 25 Oct 2026.",
    "task",
    "2026-10-25 23:59",
    "https://hackclub.example/apply",
)
case(
    "drop_hackathon_no_deadline",
    "Hackathon coming soon",
    "Hack Club <events@hackclub.example>",
    "We are planning a hackathon this winter. More details soon, stay tuned.",
    "drop",
)
case(
    "suspicious_hackathon_fee_scam",
    "Hackathon winner selection - pay the registration fee now",
    "Hack Club <events@hackclub.example>",
    "Pay the registration fee of Rs. 500 via UPI to 9876543210@upi to confirm your hackathon seat.\n"
    "Last date: 25 Oct 2026.",
    "suspicious",
)
case(
    "drop_hackathon_unauthenticated",
    "Hackathon: register now",
    "Hack Club <events@hackclub.example>",
    "Our hackathon is open. Last date to register: 25 Oct 2026.",
    "drop",
    auth=NONE,
)
case(
    "drop_hackathon_blocked_sender",
    "Hackathon: register now",
    "Promo <promo@blocked.example>",
    "A great hackathon. Last date to register: 25 Oct 2026.",
    "drop",
)

# ------------------------------------------------------------------ authentication

case(
    "suspicious_nptel_display_name_failing_auth",
    "Assignment is live - act now",
    "NPTEL <support@nptel-help.example>",
    "The assignment is live. Log in at https://nptel-help.example/login to submit.",
    "suspicious",
    auth=FAIL,
)
case(
    "suspicious_source_domain_failing_auth",
    "Assignment is live",
    NPTEL,
    "The assignment is live. Submit before 25 Oct 2026.",
    "suspicious",
    auth=FAIL,
)
case(
    "suspicious_fakenptel_lookalike_domain",
    "Assignment is live",
    "NPTEL <noreply@fakenptel.ac.in>",
    "The assignment is live. Submit before 25 Oct 2026.",
    "suspicious",
)

# ------------------------------------------------------------------ ignore / done / no cue

case(
    "ignore_source_otp",
    "Your login",
    NPTEL,
    "Your OTP is 482913. It is valid for 10 minutes. Do not share it with anyone.",
    "ignore",
)
case(
    "ignore_source_signin_link",
    "Your secure sign-in link",
    DIGII,
    "Use the link below to sign in. It expires in 15 minutes.\nhttps://app.digii.example/magic/abc123",
    "ignore",
)
case(
    "drop_source_certificate_issued",
    "Your certificate is ready",
    NPTEL,
    "Congratulations! Your certificate has been issued and is available to download.",
    "drop",
)
case(
    "drop_source_announcement_no_cue",
    "Holiday on Friday",
    NPTEL,
    "The office will remain closed on Friday for the local festival.",
    "drop",
)
case(
    "review_source_two_conflicting_deadlines",
    "Assignment 4 reminder",
    NPTEL,
    "Submit before the last date: 12 Nov 2026.\nLast date with a fine: 28 Dec 2026.",
    "review",
)

# ------------------------------------------------------------------ output


def build(c: dict) -> dict:
    n = c["n"]
    body = leaf("text/html", c["body"]) if c["html"] else leaf("text/plain", c["body"])
    payload = multipart("multipart/alternative", body) if c["html"] else body
    extra = {"Authentication_Results": c["auth"]} if c["auth"] else {}
    raw = message(n, payload, hdrs(c["subject"], c["sender"], **extra))
    raw["id"] = f"src{n:02d}"
    raw["threadId"] = f"thr-src{n:02d}"
    return raw


def expected(c: dict) -> dict:
    deadline = None
    if c["deadline"]:
        local = datetime.strptime(c["deadline"], "%Y-%m-%d %H:%M").replace(tzinfo=IST)
        deadline = local.astimezone(UTC).isoformat()
    return {"kind": c["kind"], "deadline": deadline, "link": c["link"]}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    manifest = {}
    for c in CASES:
        raw = build(c)
        name = f"{c['n']:02d}_{c['name']}"
        (OUT / f"{name}.json").write_text(json.dumps(raw, indent=2) + "\n")
        manifest[name] = expected(c) | {"id": raw["id"]}
    (OUT / "expected.manifest").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {len(CASES)} source fixtures")


if __name__ == "__main__":
    main()
