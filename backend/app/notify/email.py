"""Email channel: SMTP with tenacity retries, and the escalation message itself.

The email carries the task title, the deadline in IST, a big "Open & Complete" button to the
action link (its domain shown in plain text) and a "Mark as done" link. That link opens a
confirmation page; only the button on that page changes anything (see app/api/actions.py).
"""

import logging
import re
import smtplib
import ssl
from collections.abc import Callable
from datetime import UTC, datetime
from email.message import EmailMessage
from html import escape
from urllib.parse import quote, urlparse

from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from app.config import Settings, get_settings
from app.notify.channels import Outgoing
from app.notify.render import display_ist
from app.notify.tokens import make_done_token

logger = logging.getLogger(__name__)

SMTP_TIMEOUT = 15
SEND_ATTEMPTS = 3
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")


def _transient(exc: BaseException) -> bool:
    """Network trouble and 4xx replies are worth retrying; auth failures and 5xx are not."""
    if isinstance(exc, smtplib.SMTPConnectError | smtplib.SMTPServerDisconnected):
        return True
    if isinstance(exc, smtplib.SMTPResponseException):
        return 400 <= exc.smtp_code < 500
    if isinstance(exc, smtplib.SMTPException):
        return False  # refused recipients/sender, etc.
    return isinstance(exc, OSError)  # includes timeouts and connection resets


def link_domain(url: str | None) -> str:
    host = (urlparse(url).hostname or "") if url else ""
    return host.lower().removeprefix("www.")


def _safe_http_url(url: str | None) -> str | None:
    return url if url and urlparse(url).scheme.lower() in ("http", "https") else None


def build_email(
    message: Outgoing,
    *,
    sender: str,
    base_url: str,
    now: datetime | None = None,
) -> EmailMessage:
    now = now or datetime.now(UTC)
    link = _safe_http_url(message.link)
    done_url = (
        f"{base_url.rstrip('/')}/action/done?t="
        f"{quote(make_done_token(message.task_id, message.deadline, now))}"
    )
    due = display_ist(message.deadline) if message.deadline else None
    subject = f"⏰ {message.title}" + (f" — due {due}" if due else "")
    domain = link_domain(link)

    email = EmailMessage()
    email["Subject"] = _CONTROL_CHARS.sub(" ", subject).strip()[:200]
    email["From"] = sender
    email["To"] = message.user_email
    email["Auto-Submitted"] = "auto-generated"

    text = [message.title, f"Due: {due}" if due else "No deadline set", ""]
    if link:
        text += [f"Open & Complete: {link}", f"(opens {domain})", ""]
    text += [f"Mark as done: {done_url}"]
    email.set_content("\n".join(text))

    button = ""
    if link:
        button = (
            f'<p style="margin:24px 0 4px"><a href="{escape(link, quote=True)}" '
            'style="display:inline-block;background:#1a73e8;color:#ffffff;font-size:20px;'
            'font-weight:bold;text-decoration:none;padding:16px 36px;border-radius:8px">'
            "Open &amp; Complete</a></p>"
            f'<p style="margin:0 0 24px;color:#5f6368;font-size:13px">Opens {escape(domain)}</p>'
        )
    html = (
        '<div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:auto">'
        f'<h2 style="margin-bottom:4px">{escape(message.title)}</h2>'
        f'<p style="margin:0;color:#444">{escape("Due " + due) if due else "No deadline set"}</p>'
        f"{button}"
        f'<p style="font-size:14px"><a href="{escape(done_url, quote=True)}">Mark as done</a></p>'
        "</div>"
    )
    email.add_alternative(html, subtype="html")
    return email


class EmailSender:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        smtp_factory: Callable[[], smtplib.SMTP] | None = None,
        wait=None,
        attempts: int = SEND_ATTEMPTS,
    ):
        self.settings = settings or get_settings()
        self._smtp_factory = smtp_factory or self._default_smtp
        self._wait = wait or wait_exponential(multiplier=1, min=1, max=8)
        self._attempts = attempts

    def _default_smtp(self) -> smtplib.SMTP:
        s = self.settings
        if s.smtp_security == "ssl":
            return smtplib.SMTP_SSL(
                s.smtp_host, s.smtp_port, timeout=SMTP_TIMEOUT, context=ssl.create_default_context()
            )
        return smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=SMTP_TIMEOUT)

    def _deliver(self, email: EmailMessage) -> None:
        s = self.settings
        with self._smtp_factory() as smtp:
            if s.smtp_security != "ssl":
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            if s.smtp_username:
                smtp.login(s.smtp_username, s.smtp_password.get_secret_value())
            smtp.send_message(email)

    def send(self, message: Outgoing) -> None:
        if not message.user_email:
            raise ValueError("no recipient address")
        email = build_email(
            message,
            sender=self.settings.email_from or self.settings.smtp_username,
            base_url=self.settings.public_base_url,
        )
        retrying = Retrying(
            retry=retry_if_exception(_transient),
            stop=stop_after_attempt(self._attempts),
            wait=self._wait,
            reraise=True,
            before_sleep=lambda rs: logger.warning(
                "smtp send failed (%s); retrying, attempt %d",
                type(rs.outcome.exception()).__name__,
                rs.attempt_number,
            ),
        )
        retrying(self._deliver, email)
        logger.info("email sent: reminder=%s task=%s", message.reminder_id, message.task_id)
