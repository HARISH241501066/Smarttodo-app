"""Channel interface and the factory that picks real senders (or logging stubs when a channel
is not configured). A sender raises on failure and the dispatcher retries; it raises
ChannelDisabled when the channel is switched off, and the reminder is dropped instead.

Stubs and real senders log ids only, never titles, links or addresses.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

logger = logging.getLogger(__name__)

CHANNELS = ("inapp", "push", "email", "sms", "calendar")


class ChannelDisabled(Exception):
    """The channel is turned off (e.g. SMS_ENABLED=false); the reminder is dropped, not retried."""


@dataclass(frozen=True)
class Outgoing:
    user_id: int
    task_id: int
    reminder_id: int
    channel: str
    kind: str
    title: str
    body: str
    link: str | None
    user_email: str | None = None
    deadline: datetime | None = None


class Sender(Protocol):
    def send(self, message: Outgoing) -> None: ...


class StubSender:
    """Used for a channel that is not configured: logs the attempt and succeeds."""

    def __init__(self, channel: str):
        self.channel = channel

    def send(self, message: Outgoing) -> None:
        logger.info(
            "stub %s send: reminder=%s task=%s kind=%s",
            self.channel, message.reminder_id, message.task_id, message.kind,
        )  # fmt: skip


class InAppSender:
    """Delivery is the notifications row the dispatcher writes with the sent mark."""

    def send(self, message: Outgoing) -> None:
        return None


class CalendarSender:
    """Calendar events are synced when a task changes (and by a reconcile job), not by the
    reminder outbox, so a stray 'calendar' reminder is simply dropped."""

    def send(self, message: Outgoing) -> None:
        raise ChannelDisabled("calendar events are not sent through the outbox")


def default_senders(settings=None, session_factory=None) -> dict[str, Sender]:
    """Real senders where configured, logging stubs otherwise."""
    from app.config import get_settings
    from app.notify.email import EmailSender
    from app.notify.push import PushSender
    from app.notify.sms import SmsSender, StubSmsProvider

    settings = settings or get_settings()
    senders: dict[str, Sender] = {
        "inapp": InAppSender(),
        "calendar": CalendarSender(),
        "sms": SmsSender(StubSmsProvider(), enabled=settings.sms_enabled),
    }
    senders["email"] = EmailSender(settings) if settings.smtp_host else StubSender("email")
    vapid_ready = settings.vapid_public_key and settings.vapid_private_key.get_secret_value()
    senders["push"] = PushSender(settings, session_factory) if vapid_ready else StubSender("push")
    return senders
