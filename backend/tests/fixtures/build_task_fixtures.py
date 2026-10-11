"""Synthetic emails with hand-written expectations for category, deadline and action link.

Everything is invented. The expectations are written from the email text, not from what
the extractor happens to output. Re-run after editing:
    python tests/fixtures/build_task_fixtures.py
Received time is 2026-01-01T00:00Z + N hours (a Thursday, ~05:30-IST onwards).
Expected deadlines are IST wall-clock; "gmail" as a link means the Gmail fallback.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from build_email_fixtures import hdrs, leaf, message, multipart

OUT = Path(__file__).parent / "task_emails"
IST = ZoneInfo("Asia/Kolkata")
CASES: list[dict] = []


def html(*body: str) -> str:
    return "<html><body>" + "".join(body) + "</body></html>"


def case(name, subject, sender, body, category, deadline, link, plain=False):
    CASES.append(locals() | {"n": len(CASES) + 1})


NPTEL = "NPTEL <noreply@nptel.ac.in>"
INTERNSHALA = "Internshala <no-reply@internshala.com>"
ACME = "Acme Labs Recruiting <recruiting@acme-labs.example>"
COLLEGE = "Dept Office <office@college.example.edu>"
FRIEND = "Ravi <ravi.k@gmail.com>"
QUIZ = "https://onlinecourses.nptel.ac.in/noc26_cs10/unit?unit=5&assessment=20"
FOOTER = (
    '<p><a href="https://nptel.ac.in/unsubscribe?u=1">Unsubscribe</a> | '
    '<a href="https://nptel.ac.in/privacy">Privacy policy</a> | '
    '<a href="https://nptel.ac.in/terms">Terms</a></p><p>&copy; 2025 NPTEL</p>'
)

case(
    "nptel_quiz_with_noise_links", "NPTEL: Week 5 quiz is live", NPTEL,
    html(
        "<p>Dear learner, the Week 5 quiz is open.</p>",
        '<p><a href="https://twitter.com/nptelindia">Follow us</a> ',
        '<a href="https://www.facebook.com/nptel">Facebook</a></p>',
        f'<p>Last date to submit: 22 January 2026.</p><p><a href="{QUIZ}">Attempt quiz</a></p>',
        FOOTER,
    ),
    "nptel", "2026-01-22 23:59", QUIZ,
)  # fmt: skip
case(
    "nptel_plain_numeric_date", "Assignment 4 closes soon", NPTEL,
    "NPTEL assignment 4 closes on 05/02/2026 11:59 PM.\n"
    "Course page: https://onlinecourses.nptel.ac.in/noc26_cs10/course\n"
    "Unsubscribe: https://nptel.ac.in/unsubscribe?u=2",
    "nptel", "2026-02-05 23:59", "https://onlinecourses.nptel.ac.in/noc26_cs10/course", True,
)  # fmt: skip
case(
    "nptel_exam_registration", "NPTEL exam registration open", NPTEL,
    html(
        "<p>Registration ends on 25th Feb 2026.</p>",
        '<p><a href="https://examform.nptel.ac.in/registration/apply">Register now</a></p>',
        '<p><a href="https://nptel.ac.in/">NPTEL home</a></p>', FOOTER,
    ),
    "nptel", "2026-02-25 23:59", "https://examform.nptel.ac.in/registration/apply",
)  # fmt: skip
case(
    "gform_plain_link", "Hackathon registration", "Prof. Rao <rao@college.example.edu>",
    "Please fill the form https://forms.gle/AbC123xyz before 15 Jan 2026 5 PM.",
    "gform", "2026-01-15 17:00", "https://forms.gle/AbC123xyz", True,
)  # fmt: skip
GFORM = "https://docs.google.com/forms/d/e/1FAIpQLSdSynthetic/viewform"
case(
    "gform_html_weekday", "Feedback form for the workshop", "Dr. Iyer <iyer@college.example.edu>",
    html(
        f'<p>Please <a href="{GFORM}">open the form</a> and submit by Friday.</p>',
        '<p><a href="https://college.example.edu/privacy">Privacy</a></p>',
    ),
    "gform", "2026-01-02 23:59", GFORM,
)  # fmt: skip
case(
    "internshala_interview", "Interview invitation: Data Analytics internship", INTERNSHALA,
    html(
        "<p>You are invited to an interview. Last date to confirm: 28 Jan 2026 4 PM.</p>",
        '<p><a href="https://internshala.com/student/application/123/confirm">Confirm slot</a></p>',
        '<p><a href="https://www.instagram.com/internshala">Instagram</a></p>',
    ),
    "internship", "2026-01-28 16:00", "https://internshala.com/student/application/123/confirm",
)  # fmt: skip
ASSESS = "https://assess.acme-labs.example/start?token=abc"
case(
    "acme_offer_assessment", "Offer letter and assessment: Software Intern", ACME,
    html(
        "<p>Congratulations! Assessment deadline: 3 Feb 2026 18:30 IST.</p>",
        f'<p><a href="{ASSESS}">Start assessment</a></p>',
        '<p><a href="mailto:recruiting@acme-labs.example">Email us</a></p>',
    ),
    "internship", "2026-02-03 18:30", ASSESS,
)  # fmt: skip
case(
    "shortlisted_no_link", "You are shortlisted", ACME,
    "You have been shortlisted for the next round. Last date to reply 10 Feb 2026.",
    "internship", "2026-02-10 23:59", "gmail", True,
)  # fmt: skip
PAY = "https://erp.college.example.edu/fees/pay?id=1"
case(
    "college_fee_notice", "Exam fee notice", COLLEGE,
    html(
        "<p>Last date for fee payment is 15 Feb 2026.</p>",
        f'<p><a href="{PAY}">Pay now</a></p>',
        '<p><a href="https://college.example.edu/terms">Terms</a></p>',
    ),
    "college", "2026-02-15 23:59", PAY,
)  # fmt: skip
case(
    "college_timetable_no_deadline", "Semester 5 timetable", COLLEGE,
    "The timetable is on the notice board. Classes begin on 2 March 2026.",
    "college", None, "gmail", True,
)  # fmt: skip
case(
    "friend_submit_before", "Project report", FRIEND,
    "Hey, please submit before 20/01/2026 so we can print it.",
    "unknown", "2026-01-20 23:59", "gmail", True,
)  # fmt: skip
case(
    "newsletter_footer_year_only", "Weekly digest", "Acme News <news@acme-news.example>",
    html("<p>This week in tech.</p>",
         '<p><a href="https://acme-news.example/unsubscribe">Unsubscribe</a> '
         '<a href="https://acme-news.example/privacy">Privacy</a></p><p>&copy; 2026 Acme News</p>'),
    "unknown", None, "gmail",
)  # fmt: skip
case(
    "dmy_not_mdy", "Registration", COLLEGE,
    "Last date: 05/10/2026.", "college", "2026-10-05 23:59", "gmail", True,
)  # fmt: skip
case(
    "send_date_and_posted_date_ignored", "Notice", COLLEGE,
    "Sent on 01/01/2026. Posted: 31 Dec 2025.\nDeadline: 18 January 2026\n(c) 2026 College",
    "college", "2026-01-18 23:59", "gmail", True,
)  # fmt: skip
case(
    "two_candidates_earliest_wins", "Workshop signup", COLLEGE,
    "Last date: 12 Jan 2026. Late registration ends on 20 Jan 2026 with a fine.",
    "college", "2026-01-12 23:59", "gmail", True,
)  # fmt: skip
case(
    "relative_tomorrow_with_time", "Quick one", FRIEND,
    "Submit by tomorrow 11 AM please.", "unknown", "2026-01-02 11:00", "gmail", True,
)  # fmt: skip
case(
    "nptel_extension", "Assignment 3 deadline extended", NPTEL,
    f'<p>The deadline is extended till 25 Jan 2026.</p><p><a href="{QUIZ}">Go to assignment</a></p>'
    + FOOTER,
    "nptel", "2026-01-25 23:59", QUIZ,
)  # fmt: skip
case(
    "only_a_past_deadline", "Old notice", COLLEGE,
    "The last date was 5 Dec 2025.", "college", None, "gmail", True,
)  # fmt: skip
case(
    "deadline_24h_clock", "Submission window", COLLEGE,
    "Deadline: 12 Jan 2026 18:30 IST.", "college", "2026-01-12 18:30", "gmail", True,
)  # fmt: skip
case(
    "weekday_monday", "Lab record", COLLEGE,
    "Submit by Monday.", "college", "2026-01-05 23:59", "gmail", True,
)  # fmt: skip
case(
    "internship_mailto_only", "Internship opening at Zenith", "Zenith HR <hr@zenith-sys.example>",
    html("<p>Apply by 30 Jan 2026.</p>",
         '<p><a href="mailto:hr@zenith-sys.example">hr@zenith-sys.example</a></p>',
         '<p><a href="https://zenith-sys.example/legal/terms">Terms</a></p>'),
    "internship", "2026-01-30 23:59", "gmail",
)  # fmt: skip
ENROLL = "https://lc.college.example.edu/nptel/enroll"
case(
    "nptel_local_chapter_subject_only", "NPTEL local chapter: enrol now", COLLEGE,
    html("<p>Enrol by 12 Jan 2026.</p>", f'<p><a href="{ENROLL}">Apply</a></p>'),
    "nptel", "2026-01-12 23:59", ENROLL,
)  # fmt: skip
ASSIGN = "https://onlinecourses.nptel.ac.in/noc26_cs10/assignment/4"
case(
    "nptel_two_good_links", "Assignment 4 is open", NPTEL,
    html("<p>Last date to submit: 30 Jan 2026.</p>",
         '<p><a href="https://onlinecourses.nptel.ac.in/noc26_cs10/course">View course</a></p>',
         f'<p><a href="{ASSIGN}">Submit assignment</a></p>', FOOTER),
    "nptel", "2026-01-30 23:59", ASSIGN,
)  # fmt: skip
case(
    "tracking_pixel_and_image_links", "Hostel notice", COLLEGE,
    html("<p>Hostel allotment closes on 14 Jan 2026.</p>",
         '<a href="https://track.college.example.edu/pixel/open.gif">x</a>',
         '<a href="https://college.example.edu/img/banner.png">banner</a>'),
    "college", "2026-01-14 23:59", "gmail",
)  # fmt: skip
case(
    "gform_among_other_links", "Volunteer sign-up", COLLEGE,
    html("<p>Sign up via the form, fill before 9 Jan 2026.</p>",
         '<a href="https://college.example.edu/">Home</a> ',
         '<a href="https://forms.gle/VolSignUp1">Sign up</a>'),
    "gform", "2026-01-09 23:59", "https://forms.gle/VolSignUp1",
)  # fmt: skip


def build(c: dict) -> dict:
    n = c["n"]
    body = (
        leaf("text/plain", c["body"])
        if c["plain"] is True
        else multipart("multipart/alternative", leaf("text/html", c["body"], part_id="0.1"))
    )
    raw = message(n, body, hdrs(c["subject"], c["sender"]))
    raw["id"] = f"task{n:02d}"
    raw["threadId"] = f"thr-task{n:02d}"
    return raw


def expected(c: dict) -> dict:
    deadline = None
    if c["deadline"]:
        local = datetime.strptime(c["deadline"], "%Y-%m-%d %H:%M").replace(tzinfo=IST)
        deadline = local.astimezone(UTC).isoformat()
    return {"category": c["category"], "deadline": deadline, "link": c["link"]}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    manifest = {}
    for c in CASES:
        raw = build(c)
        name = f"{c['n']:02d}_{c['name']}"
        (OUT / f"{name}.json").write_text(json.dumps(raw, indent=2) + "\n")
        manifest[name] = expected(c)
    (OUT / "expected.manifest").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {len(CASES)} task fixtures")


if __name__ == "__main__":
    main()
