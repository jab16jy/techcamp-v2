"""Push subscription use cases (docs/04-api.md §Alertas y notificaciones; D15).

The web client registers its service worker's subscription so the browser can
receive pushes (docs/06 §4). Registration is an upsert on the UNIQUE `endpoint`
and re-binds the row to whoever registers it, and a delete only ever serves the
caller's own row.
"""

from __future__ import annotations

from uuid import UUID

from techcamp.notifications.application.ports import PushSubscriptionRepository
from techcamp.notifications.domain.errors import PushSubscriptionNotFoundError


async def register_push_subscription(
    *,
    user_id: UUID,
    endpoint: str,
    keys: dict[str, str],
    subscriptions: PushSubscriptionRepository,
) -> UUID:
    """Bind the caller's subscription and return its id (D15)."""
    return await subscriptions.upsert(user_id=user_id, endpoint=endpoint, keys=keys)


async def delete_push_subscription(
    *, user_id: UUID, subscription_id: UUID, subscriptions: PushSubscriptionRepository
) -> None:
    """Unsubscribe the caller; anyone else's row is
    `PushSubscriptionNotFoundError` (404)."""
    if not await subscriptions.delete_owned(subscription_id, user_id):
        raise PushSubscriptionNotFoundError(subscription_id)
