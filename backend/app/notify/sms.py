"""SMS: an interface and a logging stub only. No real provider is wired up, and nothing is
sent while SMS_ENABLED=false (SMS to Indian numbers generally needs DLT registration)."""

import logging
from typing import Protocol

from app.notify.channels import ChannelDisabled, Outgoing

logger = logging.getLogger(__name__)


class SmsProvider(Protocol):
    def send_sms(self, to: str | None, text: str) -> None: ...


class StubSmsProvider:
    def send_sms(self, to: str | None, text: str) -> None:
        logger.info("stub sms send (%d chars)", len(text))


class SmsSender:
    def __init__(self, provider: SmsProvider, *, enabled: bool):
        self.provider = provider
        self.enabled = enabled

    def send(self, message: Outgoing) -> None:
        if not self.enabled:
            raise ChannelDisabled("SMS_ENABLED is false")
        # Users have no phone number on file yet; a real provider will need one.
        self.provider.send_sms(None, message.title[:160])
