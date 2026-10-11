"""Web Push subscriptions: one row per browser, scoped to the session user."""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.config import get_settings
from app.db import get_session
from app.models import PushSubscription, User
from app.schemas.channels import PushSubscribe, PushUnsubscribe

router = APIRouter(tags=["push"])


@router.get("/push/public-key")
def public_key() -> dict[str, str]:
    """The VAPID public key the browser needs to subscribe. Public by design."""
    key = get_settings().vapid_public_key
    if not key:
        raise HTTPException(503, "Web Push is not configured")
    return {"public_key": key}


@router.post("/push/subscribe", status_code=201)
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
