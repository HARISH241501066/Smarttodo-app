"""Web Push subscriptions: one row per browser, scoped to the session user."""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.config import get_settings
from app.db import get_session
from app.hardening import rate_limit
from app.models import Notification, PushSubscription, User
from app.notify.channels import Outgoing, Sender
from app.notify.push import PushSender
from app.schemas.channels import PushSubscribe, PushUnsubscribe

router = APIRouter(tags=["push"])


@router.get("/push/public-key")
def public_key() -> dict[str, str]:
    """The VAPID public key the browser needs to subscribe. Public by design."""
    key = get_settings().vapid_public_key
    if not key:
        raise HTTPException(503, "Web Push is not configured")
    return {"public_key": key}


@router.post(
    "/push/subscribe", status_code=201, dependencies=[Depends(rate_limit("push-subscribe"))]
)
def subscribe(
    body: PushSubscribe,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> dict[str, int]:
    """Stores (or refreshes) this browser's subscription. If the same browser was subscribed
    under another account, it now belongs to the current one."""
    stmt = insert(PushSubscription).values(
        user_id=user.id, endpoint=body.endpoint, p256dh=body.keys.p256dh, auth=body.keys.auth
    )
    sub_id = session.execute(
        stmt.on_conflict_do_update(
            index_elements=["endpoint"],
            set_={"user_id": user.id, "p256dh": body.keys.p256dh, "auth": body.keys.auth},
        ).returning(PushSubscription.id)
    ).scalar_one()
    session.commit()
    return {"id": sub_id}


@router.delete("/push/subscribe", status_code=204)
def unsubscribe(
    body: PushUnsubscribe,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Response:
    result = session.execute(
        delete(PushSubscription).where(
            PushSubscription.user_id == user.id, PushSubscription.endpoint == body.endpoint
        )
    )
    session.commit()
    if result.rowcount == 0:
        raise HTTPException(404, "Subscription not found")
    return Response(status_code=204)


def get_push_sender() -> Sender | None:
    settings = get_settings()
    if not (settings.vapid_public_key and settings.vapid_private_key.get_secret_value()):
        return None
    return PushSender(settings)


@router.post("/push/test")
def send_test_push(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
    sender: Sender | None = Depends(get_push_sender),
) -> dict[str, int]:
    """Send a test notification to every browser this user has subscribed (and add one to the
    bell). Clicking the push opens the dashboard."""
    if sender is None:
        raise HTTPException(503, "Web Push is not configured")
    subscriptions = session.scalar(
        select(func.count())
        .select_from(PushSubscription)
        .where(PushSubscription.user_id == user.id)
    )
    if not subscriptions:
        raise HTTPException(409, "No browser is subscribed to push notifications yet")
    url = get_settings().frontend_url
    title, body = "Test notification", "Push notifications are working."
    try:
        sender.send(
            Outgoing(
                user_id=user.id,
                task_id=0,
                reminder_id=0,
                channel="push",
                kind="test",
                title=title,
                body=body,
                link=url,
            )  # fmt: skip
        )
    except Exception as exc:
        raise HTTPException(502, "The push service did not accept the notification") from exc
    session.add(Notification(user_id=user.id, kind="reminder", title=title, body=body, link=url))
    session.commit()
    return {"subscriptions": subscriptions}
