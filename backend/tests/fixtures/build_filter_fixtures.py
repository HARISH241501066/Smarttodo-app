"""Generates the synthetic fixtures for the filter layers: 10 scams and 15 legitimate emails.

Everything here is invented (names, numbers, links). Re-run after editing:
    python tests/fixtures/build_filter_fixtures.py
Output goes to ./filter_emails/{scam,legit}, separate from the ingestion fixtures.
"""

import json
from pathlib import Path

from build_email_fixtures import hdrs, leaf, message

OUT = Path(__file__).parent / "filter_emails"


def auth(domain, spf="pass", dkim="pass", dmarc="pass", ip="203.0.113.7"):
    """A realistic receiving-server Authentication-Results value."""
    parts = ["mx.google.com;"]
    if dkim:
        parts.append(f"dkim={dkim} header.i=@{domain} header.s=sel1 header.b=Zm9vYmFy;")
    if spf:
        parts.append(
            f"spf={spf} (google.com: domain of bounce@{domain} designates {ip} as permitted "
            f"sender) smtp.mailfrom=bounce@{domain};"
        )
    if dmarc:
        parts.append(f"dmarc={dmarc} (p=NONE sp=NONE dis=NONE) header.from={domain}")
    return " ".join(parts)


SCAMS: dict[str, tuple[str, str, str | None, str]] = {}  # name -> (subject, from, auth, body)
LEGIT: dict[str, tuple[str, str, str | None, str]] = {}


def scam(name, subject, sender, auth_value, body):
    SCAMS[name] = (subject, sender, auth_value, body)


def legit(name, subject, sender, auth_value, body):
    LEGIT[name] = (subject, sender, auth_value, body)


# ---------------------------------------------------------------- scams (10)

scam(
    "s01_fake_nptel_certificate_fee",
    "NPTEL Certificate Dispatch - Action Required",
    "NPTEL Team <nptel.certificates.help@gmail.com>",
    auth("gmail.com"),
    "Dear candidate, your NPTEL certificate is ready. To dispatch it you must pay a processing "
    "fee of Rs. 1499 using UPI to 98xxxxxx01@upi. Pay immediately to avoid cancellation.",
)
scam(
    "s02_guaranteed_placement_whatsapp",
    "100% Placement Guarantee - Join Now",
    "HR Team <hr.careers2026@yahoo.com>",
    auth("yahoo.com", spf="fail", dkim=None, dmarc="fail"),
    "Congratulations! Join our program and get guaranteed placement in a top MNC. "
    "Contact our manager on WhatsApp +91 90000 00001 to confirm your seat.",
)
scam(
    "s03_selected_without_interview",
    "You are selected - work from home",
    "Hiring Desk <hiring.desk.jobs@outlook.com>",
    auth("outlook.com", spf="softfail", dkim=None, dmarc=None),
    "You have been shortlisted and selected without any interview for a work from home role. "
    "Message us on Telegram +91 90000 00002 to receive your offer letter today.",
)
scam(
    "s04_fake_internshala_deposit",
    "Internshala Trainee Program - Confirm your seat",
    "Internshala Trainee Team <jobs@mail-internshala-trainee.xyz>",
    auth("mail-internshala-trainee.xyz", spf="fail", dkim="none", dmarc="fail"),
    "Your internship is approved. Pay a refundable security deposit of Rs. 2000 via Google Pay "
    "to confirm your seat. Last chance, pay within 24 hours.",
)
scam(
    "s05_fake_amazon_part_time_job",
    "Amazon Careers: part-time job, earn daily",
    "Amazon Careers <amazon.hiring.desk@hotmail.com>",
    auth("hotmail.com"),
    "Earn Rs. 5000 per day reviewing products. Pay a registration fee of Rs. 500 by bank "
    "transfer to start. Account number and IFSC will be shared on WhatsApp +91 90000 00003.",
)
scam(
    "s06_fake_google_scholarship",
    "Google Scholarship Award - claim now",
    "Google Scholarship Team <awards@google-scholarships.info>",
    auth("google-scholarships.info", spf="fail", dkim=None, dmarc="fail"),
    "You have won a Google scholarship. Pay the processing fee of Rs. 999 immediately through "
    "PhonePe to release the funds. Act now.",
)
scam(
    "s07_telegram_task_job",
    "Easy online tasks, instant payout",
    "Talent Acquisition <jobs@quickhire-global.example>",
    auth("quickhire-global.example", spf="softfail", dkim=None, dmarc=None),
    "Complete simple tasks and earn. No interview required. Contact our recruiter on "
    "Telegram +91 90000 00004 and pay the onboarding fee Rs. 750 to activate your account.",
)
scam(
    "s08_no_auth_header_placement",
    "Guaranteed job guarantee program for freshers",
    "Placement Cell <placements.support@rediffmail.com>",
    None,
    "Our 100% placement program guarantees a job. Send the registration fee of Rs. 3000 to "
    "our bank account number given below and your admission is confirmed.",
)
scam(
    "s09_fake_nptel_exam_cell",
    "NPTEL Exam Hall Ticket - pay fee",
    "NPTEL Exam Cell <exam-cell@nptel-exams-portal.com>",
    auth("nptel-exams-portal.com", spf="fail", dkim=None, dmarc="fail"),
    "Your hall ticket is on hold. Pay the verification fee Rs. 600 urgently within 24 hours "
    "via UPI to release it, otherwise your exam registration will be cancelled.",
)
scam(
    "s10_fake_company_offer_laptop_kit",
    "Offer Letter - Software Trainee",
    "Careers <careers@infotech-solutions-hr.example>",
    auth("infotech-solutions-hr.example", spf="fail", dkim=None, dmarc=None),
    "Infotech Solutions Pvt Ltd is pleased to offer you the post. You are selected, no interview "
    "required. Pay the laptop kit security deposit of Rs. 4500 to our account immediately.",
)

# ---------------------------------------------------------------- legitimate (15)

legit(
    "l01_nptel_assignment_reminder",
    "NPTEL: Assignment 5 due on 22 Jan 2026",
    "NPTEL <noreply@nptel.ac.in>",
    auth("nptel.ac.in"),
    "Dear learner, Assignment 5 for Data Structures closes on 22 Jan 2026, 11:59 PM IST. "
    "Submit it on the course page.",
)
legit(
    "l02_nptel_exam_registration",
    "NPTEL exam registration is open",
    "NPTEL Exams <exams@nptel.ac.in>",
    auth("nptel.ac.in"),
    "Registration for the April 2026 exams is open until 25 Feb. The exam registration fee of "
    "Rs. 1000 per course is paid on the official portal. Last date to pay is 25 Feb.",
)
legit(
    "l03_nptel_hall_ticket",
    "NPTEL hall tickets released",
    "NPTEL <noreply@nptel.ac.in>",
    auth("nptel.ac.in", dmarc=None),
    "Your hall ticket and exam centre details are now available in your dashboard.",
)
legit(
    "l04_internshala_application_update",
    "Your application status on Internshala",
    "Internshala <no-reply@internshala.com>",
    auth("internshala.com"),
    "Good news! The employer viewed your application for the Web Development internship. "
    "Complete your assessment by 30 Jan 2026.",
)
legit(
    "l05_internshala_interview_invite",
    "Interview invitation: Data Analytics internship",
    "Internshala Student <student@internshala.com>",
    auth("internshala.com"),
    "You are invited to an interview on 28 Jan 2026 at 4 PM. Please respond urgently so the "
    "team can confirm the slot.",
)
legit(
    "l06_company_internship_schedule",
    "Interview schedule - Acme Labs internship",
    "Acme Labs Recruiting <recruiting@acme-labs.example>",
    auth("acme-labs.example", dmarc=None),
    "Hello, your technical interview is scheduled for 29 Jan 2026 at 11 AM. The stipend is "
    "Rs. 15000 per month, paid on the last working day.",
)
legit(
    "l07_google_form_shared",
    "Feedback form: Workshop on Cloud Computing",
    "Dr. Meena Iyer (via Google Forms) <forms-noreply@google.com>",
    auth("google.com"),
    "Dr. Meena Iyer has invited you to fill in the form 'Workshop feedback'. "
    "Open https://docs.google.com/forms/d/e/synthetic/viewform and submit by 31 Jan 2026.",
)
legit(
    "l08_google_form_receipt",
    "Your response to Hackathon registration",
    "Google Forms <forms-receipts-noreply@google.com>",
    auth("google.com"),
    "Thanks for filling in 'Hackathon registration'. Your response has been recorded.",
)
legit(
    "l09_teacher_google_form_link",
    "Fill this form for the tech fest by Friday",
    "Prof. Rao <rao@college.example.edu>",
    auth("college.example.edu"),
    "Students, please fill the Google Form https://forms.gle/syntheticAbc for tech fest volunteers "
    "before Friday.",
)
legit(
    "l10_college_exam_timetable",
    "Semester 5 end-semester exam timetable",
    "Controller of Examinations <coe@college.example.edu>",
    auth("college.example.edu", dmarc=None),
    "The end-semester examinations begin on 2 March 2026. The timetable is attached to the "
    "department notice board.",
)
legit(
    "l11_college_semester_fee",
    "Urgent: pay semester fee by 15 Feb",
    "Accounts Office <accounts@college.example.edu>",
    auth("college.example.edu"),
    "Urgent reminder: please pay the semester fee of Rs. 45000 on the college portal before "
    "15 Feb 2026 to avoid a late fee. Tuition fee receipts are available there too.",
)
legit(
    "l12_placement_drive_announcement",
    "Campus placement drive - Zenith Systems",
    "Placement Cell <placement@college.example.edu>",
    auth("college.example.edu"),
    "Zenith Systems will visit on 10 Feb for a campus drive. Register on the placement portal by "
    "5 Feb. Eligible: CGPA 7.0 and above. Selection is through an aptitude test and interviews.",
)
legit(
    "l13_professor_gmail_submission",
    "Project report submission",
    "Prof. Kumar <prof.kumar.cse@gmail.com>",
    auth("gmail.com"),
    "Please submit the final project report by 20 Feb 2026 and bring a printed copy.",
)
legit(
    "l14_amazon_order_shipped",
    "Your Amazon.in order has shipped",
    "Amazon.in <shipment-tracking@amazon.in>",
    auth("amazon.in"),
    "Your order of 'Engineering Mathematics' will arrive by 18 Jan 2026. Track your package in "
    "Your Orders. Payment was received on 14 Jan.",
)
legit(
    "l15_google_security_alert",
    "Security alert: new sign-in on Windows",
    "Google <no-reply@accounts.google.com>",
    auth("accounts.google.com"),
    "A new sign-in to your Google Account was detected on a Windows device. If this was you, "
    "no action is needed.",
)

# ---------------------------------------------------------------- output


def build(n, name, spec):
    subject, sender, auth_value, body = spec
    extra = {"Authentication_Results": auth_value} if auth_value else {}
    msg = message(n, leaf("text/plain", body), hdrs(subject, sender, **extra))
    msg["id"] = f"{name.split('_')[0]}"
    msg["threadId"] = f"thr-{msg['id']}"
    return msg


def main() -> None:
    for kind, specs, base in (("scam", SCAMS, 100), ("legit", LEGIT, 200)):
        folder = OUT / kind
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("*.json"):
            old.unlink()
        for i, (name, spec) in enumerate(specs.items(), start=1):
            path = folder / f"{name}.json"
            path.write_text(json.dumps(build(base + i, name, spec), indent=2) + "\n")
        print(f"wrote {len(specs)} {kind} fixtures")


if __name__ == "__main__":
    main()
