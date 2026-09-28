"""Ports the notification use cases depend on (ADR-0002; docs/05).

A subscription is external I/O (the browser's endpoint), so it is behind a
port; the SQLAlchemy adapter is its only implementation. The outbox is behind a
port for the other half of docs/06 §4: `NotificationSender` is a provider
(push, SMS, WhatsApp — real in one profile, simulated in the seminar), which is
external I/O, so tests may replace it with a double and T7c may put a circuit
breaker in front of it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from techcamp.notifications.domain.models import CLAIM_LIMIT, Channel, PendingNotification


class PushSubscriptionRepository(Protocol):
    async def upsert(self, *, user_id: UUID, endpoint: str, keys: dict[str, str]) -> UUID:
        """Bind `endpoint` to `user_id` and return the row's id.

        `push_subscription.endpoint` is UNIQUE (docs/03:307-312), so a browser
        that registers again after a new key pair rebinds and replaces the keys
        of the row it already owns instead of failing on the constraint.
        """
        ...

    async def delete_owned(self, subscription_id: UUID, user_id: UUID) -> bool:
        """Delete the row only when `user_id` owns it; `False` when it does not
        exist or belongs to someone else (both are 404 for the caller)."""
        ...


class NotificationSender(Protocol):
    """One delivery channel's provider (docs/06 §4; ADR-0016).

    Returning is success. Raising is a failure the dispatcher has to answer for:
    it costs one attempt and the next backoff delay, and the fifth one gives the
    row up. The channel itself is the key this sender is registered under, so a
    sender never has to check which one it is.
    """

    async def send(self, notification: PendingNotification) -> None:
        """Hand one due row to the provider. Raise on any failure.

        The row stays locked by the dispatcher's claim while this runs, which is
        what keeps a second worker from sending it too.
        """
        ...


class OutboxRepository(Protocol):
    """The `notification` queue the dispatcher drains (docs/06 §4; ADR-0016).

    The one read that is not org-scoped, and it reads rows of every organization
    on purpose: the dispatcher is the worker's own sweep, the same reason the
    alerts fan-out reads org ids only (D21). No caller of this port ever serves
    one organization's rows to another (docs/09).
    """

    async def claim_due(
        self, *, now: datetime, channels: Sequence[Channel], limit: int = CLAIM_LIMIT
    ) -> Sequence[PendingNotification]:
        """Take up to `limit` due `pending` rows of those `channels`, oldest first.

        "Hold" is the point: the claim is `FOR UPDATE SKIP LOCKED`, so the rows
        stay claimed until the caller's transaction ends and a second worker
        passes over them instead of sending them twice. `channels` keeps a row
        nobody can deliver yet out of the batch.
        """
        ...

    async def hold(self, notification_id: UUID, *, now: datetime) -> bool:
        """Take one row's own lock before it is sent; `False` when another worker
        already holds it or has already closed it, which is the row's answer: it
        is not ours to send."""
        ...

    async def mark_sent(self, notification_id: UUID, *, at: datetime) -> None:
        """Close a delivered row, and release the claim's lock."""
        ...

    async def mark_retry(
        self, notification_id: UUID, *, attempts: int, next_attempt_at: datetime, error: str
    ) -> None:
        """Record a failed attempt and the instant the row becomes due again."""
        ...

    async def mark_failed(self, notification_id: UUID, *, attempts: int, error: str) -> None:
        """Give a row up after `MAX_ATTEMPTS` (docs/06 §4)."""
        ...
