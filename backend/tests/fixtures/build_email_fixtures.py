"""Generates the synthetic Gmail API (format=full) message fixtures in ./emails.

Everything here is invented; no real mail is ever used. Re-run after editing:
    python tests/fixtures/build_email_fixtures.py
"""

import base64
import json
from pathlib import Path

OUT = Path(__file__).parent / "emails"
BASE_MS = 1_767_225_600_000  # 2026-01-01T00:00:00Z


def b64(data: str | bytes, pad: bool = False) -> str:
    raw = data.encode() if isinstance(data, str) else data
    out = base64.urlsafe_b64encode(raw).decode()
    return out if pad else out.rstrip("=")


def hdrs(subject="Hello", sender="Sender <sender@example.test>", **extra) -> list[dict]:
    headers = []
    if subject is not None:
        headers.append({"name": "Subject", "value": subject})
    if sender is not None:
        headers.append({"name": "From", "value": sender})
    headers += [{"name": k.replace("_", "-"), "value": v} for k, v in extra.items()]
    return headers


def leaf(mime, content, charset="utf-8", part_id="0", filename="", **extra_headers):
    raw = content if isinstance(content, bytes) else content.encode(charset)
    return {
        "partId": part_id,
        "mimeType": mime,
        "filename": filename,
        "headers": [{"name": "Content-Type", "value": f'{mime}; charset="{charset}"'}]
        + [{"name": k.replace("_", "-"), "value": v} for k, v in extra_headers.items()],
        "body": {"size": len(raw), "data": b64(raw)},
    }


def attachment(name, mime="application/pdf", part_id="1"):
    return {
        "partId": part_id,
        "mimeType": mime,
        "filename": name,
        "headers": [{"name": "Content-Disposition", "value": f'attachment; filename="{name}"'}],
        "body": {"size": 48213, "attachmentId": f"ANGjdJ_synthetic_{part_id}"},
    }


def multipart(mime, *parts, headers=None):
    return {"mimeType": mime, "headers": headers or [], "body": {"size": 0}, "parts": list(parts)}


def message(n, payload, headers, labels=("INBOX", "UNREAD"), snippet="synthetic"):
    payload = {**payload, "headers": headers + payload.get("headers", [])}
    return {
        "id": f"msg{n:03d}",
        "threadId": f"thr{n:03d}",
        "labelIds": list(labels),
        "snippet": snippet,
        "internalDate": str(BASE_MS + n * 3_600_000),
        "sizeEstimate": 2048,
        "payload": payload,
    }


HTML_NPTEL = """<html><head><title>ignored title</title>
<style>.x{color:red}</style><script>alert('tracking')</script></head>
<body><div>Dear learner,</div><p>Assignment 4 closes on <b>Friday</b>.</p>
<table><tr><td>Course</td><td>Data Structures</td></tr></table>
<p>Open the <a href="https://nptel.example.test/assignment/4">portal</a> now.</p>
<!-- hidden comment --></body></html>"""

FIXTURES: dict[str, dict] = {}


def add(name, n, payload, headers, **kw):
    FIXTURES[name] = message(n, payload, headers, **kw)


add(
    "01_plain_nptel",
    1,
    leaf(
        "text/plain", "NPTEL Assignment 3 is due on 12 Jan 2026 11:59 PM IST. Submit on the portal."
    ),
    hdrs("NPTEL: Assignment 3 reminder", "NPTEL <noreply@nptel.example.test>"),
)

add(
    "02_multipart_alternative_internship",
    2,
    multipart(
        "multipart/alternative",
        leaf(
            "text/plain", "PLAIN VERSION: Apply for the summer internship by 20 Jan.", part_id="0.0"
        ),
        leaf("text/html", "<p>HTML VERSION: apply for the summer internship</p>", part_id="0.1"),
    ),
    hdrs("Summer internship applications open", "Careers <careers@acme.example.test>"),
)

add(
    "03_html_only_gform",
    3,
    leaf(
        "text/html",
        '<div>Please fill the <a href="https://docs.google.com/forms/d/e/SYNTH123/viewform">'
        "registration form</a> before Monday.</div><br>Thanks,<br>Dept Office",
    ),
    hdrs("Registration form", "Dept Office <office@college.example.test>"),
)

add(
    "04_html_complex_college",
    4,
    leaf("text/html", HTML_NPTEL),
    hdrs("Circular: lab record submission", "Registrar <registrar@college.example.test>"),
)

add(
    "05_mixed_with_pdf_attachment",
    5,
    multipart(
        "multipart/mixed",
        leaf("text/plain", "Hall ticket attached. Exam begins 3 Feb.", part_id="0"),
        attachment("hall_ticket.pdf"),
    ),
    hdrs("Hall ticket", "Exams <exams@college.example.test>"),
)

add(
    "06_attachment_only",
    6,
    multipart("multipart/mixed", attachment("syllabus.pdf", part_id="0")),
    hdrs("Syllabus", "Dept <dept@college.example.test>"),
)

add(
    "07_nested_mixed_related_alternative",
    7,
    multipart(
        "multipart/mixed",
        multipart(
            "multipart/related",
            multipart(
                "multipart/alternative",
                leaf("text/plain", "Nested plain body: workshop on 8 Feb.", part_id="0.0.0.0"),
                leaf("text/html", "<p>Nested html body</p>", part_id="0.0.0.1"),
            ),
            {
                "mimeType": "image/png",
                "filename": "logo.png",
                "headers": [],
                "body": {"size": 900, "attachmentId": "ANGjdJ_logo"},
            },
        ),
        attachment("agenda.pdf", part_id="0.1"),
    ),
    hdrs("Workshop invite", "Club <club@college.example.test>"),
)

add(
    "08_broken_utf8",
    8,
    leaf("text/plain", b"Assignment due \xff\xfe tomorrow \xc3 end", charset="utf-8"),
    hdrs("Broken bytes", "Someone <someone@example.test>"),
)

add(
    "09_latin1_charset",
    9,
    leaf("text/plain", "Café meeting at noon - bring résumé", charset="iso-8859-1"),
    hdrs("Latin one", "Pat <pat@example.test>"),
)

add(
    "10_base64_no_padding",
    10,
    {"mimeType": "text/plain", "headers": [], "body": {"size": 5, "data": b64("abcde")}},
    hdrs("No padding", "Pat <pat@example.test>"),
)

corrupt = leaf("text/plain", "x")
corrupt["body"]["data"] = "QUJDR"  # 5 chars: length % 4 == 1 is impossible base64
add("11_corrupt_base64", 11, corrupt, hdrs("Corrupt body", "Pat <pat@example.test>"))

add("12_missing_headers", 12, leaf("text/plain", "No subject, no sender."), [])

add(
    "13_encoded_word_subject",
    13,
    leaf("text/plain", "Encoded subject test."),
    hdrs(
        "=?UTF-8?B?QXNzaWdubWVudCDigJQgZHVlIHRvbW9ycm93?=",
        "=?utf-8?q?Prof=2E_Rao?= <rao@college.example.test>",
    ),
)

add(
    "14_sender_display_name_with_via",
    14,
    leaf("text/plain", "Forwarded notice."),
    hdrs("Notice", '"Prof. Rao via NPTEL" <noreply@mail.nptel.example.test>'),
)

add(
    "15_promotions_label",
    15,
    leaf("text/plain", "50% off everything!"),
    hdrs("Big sale", "Shop <deals@shop.example.test>"),
    labels=("INBOX", "CATEGORY_PROMOTIONS"),
)

add(
    "16_spam_label",
    16,
    leaf("text/plain", "You won a prize, send your bank details."),
    hdrs("Winner!!!", "Prize <win@lottery.example.test>"),
    labels=("SPAM",),
)

add(
    "17_linkedin_sender",
    17,
    leaf("text/plain", "You appeared in 4 searches this week."),
    hdrs("Your weekly stats", "LinkedIn <notifications@linkedin.com>"),
)

add(
    "18_empty_plain_body",
    18,
    {"mimeType": "text/plain", "headers": [], "body": {"size": 0}},
    hdrs("Empty body", "Pat <pat@example.test>"),
)

add(
    "19_unicode_emoji_html",
    19,
    leaf("text/html", "<p>வணக்கம் नमस्ते \U0001f393 Results out!</p>"),
    hdrs("Results \U0001f393", "Office <office@college.example.test>"),
)

add(
    "20_calendar_invite_part",
    20,
    multipart(
        "multipart/alternative",
        leaf("text/plain", "Meeting invite: project review on 15 Jan.", part_id="0.0"),
        leaf(
            "text/calendar", "BEGIN:VCALENDAR\r\nSUMMARY:SECRET-ICS\r\nEND:VCALENDAR", part_id="0.1"
        ),
        leaf("text/html", "<p>Meeting invite html</p>", part_id="0.2"),
    ),
    hdrs("Invitation: project review", "Guide <guide@college.example.test>"),
)

add(
    "21_naukri_sender",
    21,
    leaf("text/plain", "10 new jobs match your profile."),
    hdrs("Jobs for you", "Naukri <info@naukri.com>"),
)

add(
    "22_unknown_charset",
    22,
    {
        "mimeType": "text/plain",
        "headers": [{"name": "Content-Type", "value": 'text/plain; charset="x-bogus-charset"'}],
        "body": {"size": 21, "data": b64("Unknown charset body")},
    },
    hdrs("Odd charset", "Pat <pat@example.test>"),
)

add(
    "23_plain_empty_html_has_content",
    23,
    multipart(
        "multipart/alternative",
        leaf("text/plain", "   ", part_id="0.0"),
        leaf("text/html", "<p>Only the HTML has the deadline: 30 Jan.</p>", part_id="0.1"),
    ),
    hdrs("Deadline notice", "Dept <dept@college.example.test>"),
)

add(
    "24_subdomain_of_denylisted",
    24,
    leaf("text/plain", "Digest."),
    hdrs("Digest", "LinkedIn Jobs <jobs-noreply@mail.linkedin.com>"),
)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    for name, data in FIXTURES.items():
        (OUT / f"{name}.json").write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n")
    print(f"wrote {len(FIXTURES)} fixtures to {OUT}")


if __name__ == "__main__":
    main()
