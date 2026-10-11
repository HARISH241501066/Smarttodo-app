# ruff: noqa: E501  (an embedded HTML email)
"""Synthetic emails for the triage rules, each with the kind it must be sorted into.

Everything here is invented. Re-run after editing:
    python tests/fixtures/build_triage_fixtures.py
Received time is 2026-01-01T00:00Z + N hours. Expected deadlines are IST wall-clock.
`kind` is what the whole pipeline must do with the email: task, update, ignore or review.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from build_email_fixtures import hdrs, leaf, message, multipart

OUT = Path(__file__).parent / "triage_emails"
IST = ZoneInfo("Asia/Kolkata")
CASES: list[dict] = []

NPTEL = "NPTEL <noreply@nptel.ac.in>"
DIGII = "Digii <no-reply@digii.example>"
COLLEGE = "Dept Office <office@college.example.edu>"
SECURITY = "Accounts <no-reply@accounts.example.test>"
SHOP = "Shop <orders@shop.example.test>"
BANK = "Payments <payments@pay.example.test>"
AUTH = "mx.google.com; dkim=pass; spf=pass; dmarc=pass"


def case(name, subject, sender, body, kind, deadline=None, link=None, html=False):
    CASES.append(locals() | {"n": len(CASES) + 1})


# ------------------------------------------------------------------ the cases named in the brief

case(
    "task_assignment_is_live",
    "Week 9 content & Assignment is live",
    NPTEL,
    "Dear learner, the Week 9 content and the Assignment are now live on the course page.\n"
    "Open https://onlinecourses.nptel.ac.in/noc26_cs10/assignment/9 to begin.",
    "task",
    None,  # no deadline in the text: still an open task, deadline null
    "https://onlinecourses.nptel.ac.in/noc26_cs10/assignment/9",
)
case(
    "update_quiz_summary_score",
    "Quiz summary / score",
    NPTEL,
    "Your quiz summary is ready. You scored 8 out of 10 in the Week 8 quiz.",
    "update",
)
case(
    "ignore_secure_signin_link",
    "Your secure sign-in link",
    SECURITY,
    "Use the link below to sign in. It expires in 15 minutes.\n"
    "https://accounts.example.test/magic/abc123",
    "ignore",
)
case(
    "ignore_otp",
    "Your login",
    SECURITY,
    "Your OTP is 482913. It is valid for 10 minutes. Do not share it with anyone.",
    "ignore",
)
case(
    "ignore_payment_receipt",
    "Payment receipt - exam registration fee",
    BANK,
    "Thank you for your payment of Rs. 1000. Your receipt number is R-20931.",
    "ignore",
)
case(
    "update_lecture_uploaded",
    "Week 9 lecture videos uploaded",
    NPTEL,
    "The lecture videos for Week 9 have been uploaded to the course page.",
    "update",
)
case(
    "review_two_conflicting_deadlines",
    "Assignment 4 reminder",
    NPTEL,
    "Last date to submit: 12 Jan 2026.\nLate registration ends on 28 Feb 2026 with a fine.",
    "review",
)

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

# ------------------------------------------------------------------ more coverage

case(
    "ignore_security_alert",
    "Security alert: new sign-in on Windows",
    SECURITY,
    "A new sign-in to your account was detected. If this was you, no action is needed.",
    "ignore",
)
case(
    "ignore_order_shipped",
    "Your order #48213 has shipped",
    SHOP,
    "Your order of 'Engineering Mathematics' is on its way and will arrive by Friday.",
    "ignore",
)
case(
    "ignore_password_reset",
    "Reset your password",
    SECURITY,
    "We received a request to reset your password. Click the link to choose a new one.",
    "ignore",
)
case(
    "update_certificate_issued",
    "Your certificate is ready",
    NPTEL,
    "Congratulations! Your certificate has been issued and is available to download.",
    "update",
)
case(
    "update_announcement_no_action",
    "Holiday on Friday",
    COLLEGE,
    "The college will remain closed on Friday for the local festival. No action required.",
    "update",
)
case(
    "update_no_cues_at_all",
    "Hello",
    "Ravi <ravi.k@gmail.com>",
    "Just saying hi, hope your week is going well.",
    "update",
)
case(
    "task_fill_the_form",
    "Please fill the feedback form",
    COLLEGE,
    "Please fill the form https://forms.gle/Feedback26 before 25 Jan 2026.",
    "task",
    "2026-01-25 23:59",
    "https://forms.gle/Feedback26",
)
case(
    "task_registration_closes_on",
    "Exam registration",
    NPTEL,
    "Registration closes on 30 Jan 2026. Register at https://examform.nptel.ac.in/registration/apply",
    "task",
    "2026-01-30 23:59",
    "https://examform.nptel.ac.in/registration/apply",
)
case(
    "task_quiz_is_open_no_date",
    "Quiz is open",
    NPTEL,
    "The Week 3 quiz is open. Attempt the quiz at https://onlinecourses.nptel.ac.in/noc26_cs10/quiz/3",
    "task",
    None,
    "https://onlinecourses.nptel.ac.in/noc26_cs10/quiz/3",
)
case(
    "review_score_and_assignment_cues_conflict",
    "Quiz 3 score published; Assignment 5 is due",
    NPTEL,
    "Your quiz score is published. Separately, Assignment 5 is due soon.",
    "review",
)
case(
    "update_results_declared",
    "Results declared for semester 4",
    COLLEGE,
    "The results are out. You can view your scorecard on the portal.",
    "update",
)

# ------------------------------------------------------------------ output


def build(c: dict) -> dict:
    n = c["n"]
    sender = c["sender"]
    body = leaf("text/html", c["body"]) if c["html"] else leaf("text/plain", c["body"])
    payload = multipart("multipart/alternative", body) if c["html"] else body
    raw = message(n, payload, hdrs(c["subject"], sender, Authentication_Results=AUTH))
    raw["id"] = f"tri{n:02d}"
    raw["threadId"] = f"thr-tri{n:02d}"
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
    print(f"wrote {len(CASES)} triage fixtures")


if __name__ == "__main__":
    main()
