"""Web Push channel (VAPID, via pywebpush).

A 404 or 410 from the push service means the subscription is gone for good, so it is deleted.
A user with no subscriptions has nothing to push to; that is not an error (the in-app
notification still exists). The click handler opens the payload's "url", the action link.
"""

import json
import logging
from collections.abc import Callable

from pywebpush import WebPushException, webpush
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db import get_engine
from app.models import PushSubscription
from app.notify.channels import Outgoing
from app.notify.email import _safe_http_url

logger = logging.getLogger(__name__)

GONE_STATUSES = (404, 410)
PUSH_TIMEOUT = 10
PUSH_TTL = 3600


def payload_for(message: Outgoing, fallback_url: str) -> str:
    return json.dumps(
        {
            "title": message.title[:200],
            "body": message.body[:300],
            "url": _safe_http_url(message.link) or fallback_url,
            "tag": f"task-{message.task_id}",
            "task_id": message.task_id,
        }
    )


class PushSender:
    def __init__(
        self,
        settings: Settings | None = None,
        session_factory: Callable[[], Session] | None = None,
        webpush_fn=webpush,
    ):
        self.settings = settings or get_settings()
        self._session_factory = session_factory or (
            lambda: sessionmaker(get_engine(), expire_on_commit=False)()
        )
        self._webpush = webpush_fn

    def send(self, message: Outgoing) -> None:
        with self._session_factory() as session:
            subs = list(
                session.scalars(
                    select(PushSubscription)
                    .where(PushSubscription.user_id == message.user_id)
                    .order_by(PushSubscription.id)
                )
            )
        if not subs:
            logger.info("push: user %s has no subscriptions", message.user_id)
            return

        data = payload_for(message, self.settings.public_base_url)
        delivered, failed, gone = 0, 0, []
        for sub in subs:
            try:
                self._webpush(
                    subscription_info={
                        "endpoint": sub.endpoint,
                        "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                    },
                    data=data,
                    vapid_private_key=self.settings.vapid_private_key.get_secret_value(),
                    vapid_claims={"sub": self.settings.vapid_subject},  # fresh dict: it is mutated
                    ttl=PUSH_TTL,
                    timeout=PUSH_TIMEOUT,
                )
                delivered += 1
            except WebPushException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in GONE_STATUSES:
                    gone.append(sub.id)
                else:
                    failed += 1
                    logger.warning("push to subscription %s failed: status=%s", sub.id, status)
            except Exception as exc:
                failed += 1
                logger.warning("push to subscription %s failed: %s", sub.id, type(exc).__name__)

        if gone:
            with self._session_factory() as session:
                session.execute(delete(PushSubscription).where(PushSubscription.id.in_(gone)))
                session.commit()
            logger.info("push: removed %d expired subscription(s)", len(gone))
        if delivered == 0 and failed > 0:
            raise RuntimeError(f"push failed for {failed} subscription(s)")
        logger.info(
            "push sent: reminder=%s task=%s delivered=%d failed=%d gone=%d",
            message.reminder_id, message.task_id, delivered, failed, len(gone),
        )  # fmt: skip
