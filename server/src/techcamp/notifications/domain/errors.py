"""Domain-level notification failures. Pure, no I/O.

Kept as `notifications`' own types: the module never imports `alerts`
(docs/05, D14).
"""

from __future__ import annotations

from uuid import UUID


class PushSubscriptionNotFoundError(Exception):
    """Raised when a push subscription is not the caller's own.

    docs/04: 404, never 403 — a subscription of another user is not revealed.
    """

    def __init__(self, subscription_id: UUID) -> None:
        self.subscription_id = subscription_id
        super().__init__(f"Push subscription {subscription_id} not found")


class PushSubscriptionGoneError(Exception):
    """The push service no longer knows this subscription (404 or 410).

    docs/06 §4's `410 Gone (suscripción vencida)` branch: the row is deleted and
    the next subscription is tried. Distinct from a provider outage on purpose —
    a browser that will never accept another push is not a delivery that failed,
    so it must not cost the outbox row one of its five attempts.
    """

    def __init__(self, subscription_id: UUID) -> None:
        self.subscription_id = subscription_id
        super().__init__(f"Push subscription {subscription_id} is gone")


class NoPushSubscriptionError(Exception):
    """A `push` row whose user has no browser left to receive it on.

    The honest outcome of `plan_notifications` writing one `push` row per
    recipient whoever they are (D5), for a user who never enabled notifications
    or whose every subscription has since gone. Not D31's "no adapter registered"
    case, which costs nothing: here an adapter exists and the delivery genuinely
    could not happen, so the row costs an attempt and the documented backoff
    decides its future, and `failed` after five is the truth.
    """

    def __init__(self, user_id: UUID) -> None:
        self.user_id = user_id
        super().__init__(f"no push subscription could be delivered to user {user_id}")
