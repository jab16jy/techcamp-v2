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
