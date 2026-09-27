"""Push subscription endpoints (docs/04-api.md §Alertas y notificaciones; D15).

The service worker registers here so the browser receives the pushes of
docs/06 §4. A subscription belongs to a user, so these routes take no `org_id`:
what is isolated is the caller's own row.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from techcamp.identity.adapters.api.deps import CurrentUserId
from techcamp.notifications.adapters.api.deps import PushSubscriptionRepoDep
from techcamp.notifications.application import (
    delete_push_subscription,
    register_push_subscription,
)
from techcamp.notifications.domain.errors import PushSubscriptionNotFoundError
from techcamp.shared.errors import ProblemError

router = APIRouter(tags=["notifications"])


class PushKeys(BaseModel):
    """The VAPID key pair of the browser's subscription (docs/06 §4)."""

    p256dh: str
    auth: str


class PushSubscriptionCreateRequest(BaseModel):
    endpoint: str = Field(min_length=1)
    keys: PushKeys


class PushSubscriptionView(BaseModel):
    id: UUID


@router.post("/push-subscriptions", response_model=PushSubscriptionView, status_code=201)
async def post_push_subscription(
    payload: PushSubscriptionCreateRequest,
    user_id: CurrentUserId,
    subscriptions: PushSubscriptionRepoDep,
) -> PushSubscriptionView:
    """D15: an upsert on the UNIQUE `endpoint` — a browser that registers
    again rebinds its row and replaces its keys."""
    subscription_id = await register_push_subscription(
        user_id=user_id,
        endpoint=payload.endpoint,
        keys=payload.keys.model_dump(),
        subscriptions=subscriptions,
    )
    return PushSubscriptionView(id=subscription_id)


@router.delete("/push-subscriptions/{subscription_id}", status_code=204)
async def delete_push_subscription_route(
    subscription_id: UUID,
    user_id: CurrentUserId,
    subscriptions: PushSubscriptionRepoDep,
) -> Response:
    try:
        await delete_push_subscription(
            user_id=user_id, subscription_id=subscription_id, subscriptions=subscriptions
        )
    except PushSubscriptionNotFoundError as exc:
        raise ProblemError(status=404, title="Push subscription not found") from exc
    return Response(status_code=204)
