"""Channel interface and the stub senders. Real senders arrive in Module 9.

A sender raises on failure; the dispatcher retries. Stubs log ids only, never titles or links.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)

CHANNELS = ("inapp", "push", "email", "sms", "calendar")


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


class Sender(Protocol):
    def send(self, message: Outgoing) -> None: ...


class StubSender:
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


def default_senders() -> dict[str, Sender]:
    senders: dict[str, Sender] = {c: StubSender(c) for c in CHANNELS}
    senders["inapp"] = InAppSender()
    return senders
