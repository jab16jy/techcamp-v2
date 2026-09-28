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

from techcamp.notifications.domain.models import (
    CLAIM_LIMIT,
    Channel,
    PendingNotification,
    PushSubscription,
)


class PushSubscriptionRepository(Protocol):
    async def upsert(self, *, user_id: UUID, endpoint: str, keys: dict[str, str]) -> UUID:
        """Bind `endpoint` to `user_id` and return the row's id.

        `push_subscription.endpoint` is UNIQUE (docs/03:307-312), so a browser
        that registers again after a new key pair rebinds and replaces the keys
        of the row it already owns instead of failing on the constraint.
        """
        ...

    async def list_for_user(self, user_id: UUID) -> Sequence[PushSubscription]:
        """Every browser `user_id` registered, oldest first.

        What the Web Push sender fans out to: a user with a phone and a laptop has
        two rows and has to hear about the alert on both. Scoped by `user_id` and
        not by `org_id` because that is how this table is already isolated — a
        subscription belongs to a user, so the caller's own rows are the only ones
        reachable (docs/04 §Alertas y notificaciones, same as `upsert`).
        """
        ...

    async def delete_owned(self, subscription_id: UUID, user_id: UUID) -> bool:
        """Delete the row only when `user_id` owns it; `False` when it does not
        exist or belongs to someone else (both are 404 for the caller)."""
        ...


class PushTransport(Protocol):
    """The browser push service itself, reached over HTTP (docs/06 §4; D34).

    External I/O no test may reach, which is the one thing a port in this project
    is for, so the sender is testable without a live service. `topic` and `ttl`
    are the Web Push request's own `Topic` and `TTL`, and they exist for D30:
    delivery is at least once, and both let a duplicate be a replacement instead
    of a second copy of the same news.
    """

    async def deliver(
        self, subscription: PushSubscription, *, payload: str, topic: str, ttl: int
    ) -> None:
        """Hand one encrypted push to one browser's push service.

        Raise `PushSubscriptionGoneError` for a `404`/`410` — the subscription is
        gone for good, which is not a failure. Raise anything else for every
        other problem, including a `429` or a `5xx`: those say nothing about the
        subscription, so the outbox row has to hear about them.
        """
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


class ProviderCircuits(Protocol):
    """The circuit breaker of each channel's provider (docs/06 §4; ADR-0016).

    One per provider, which is one per channel: docs/06 §4's "Por proveedor" and
    its "mientras tanto las críticas pasan al canal alterno" only mean something
    if the push service being down says nothing about the SMS provider, and if the
    dispatcher can ask about one channel and about the alternative separately.

    The dispatcher owns every decision that follows from the answer — hold the row
    without spending an attempt, send the critical somewhere else, let the next
    delivery through after the cooldown. This port only answers, and
    `cooldown_remaining` is how it turns "not now" into the instant the row
    becomes due again.
    """

    def allows(self, channel: Channel) -> bool:
        """Whether a delivery on this channel may be attempted right now.

        `True` in CLOSED and for the one trial HALF_OPEN allows; `False` while
        OPEN, which is a statement about the provider and not about the row.
        """
        ...

    def record_success(self, channel: Channel) -> None:
        """One delivery landed: the provider is answering, so the consecutive
        count restarts (docs/06 §4 counts "5 fallos seguidos")."""
        ...

    def record_failure(self, channel: Channel) -> None:
        """One delivery failed for a reason that is the provider's.

        The caller decides what counts: a row that cannot be delivered at all
        (D34's gone subscription, D36's `NoPushSubscriptionError`) is not
        evidence about the provider and must not be recorded here.
        """
        ...

    def cooldown_remaining(self, channel: Channel) -> float:
        """Seconds until this channel is allowed again; `0.0` when it is."""
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

    async def mark_deferred(
        self, notification_id: UUID, *, next_attempt_at: datetime, reason: str
    ) -> None:
        """Hold a row that is due but must not be sent yet, WITHOUT an attempt.

        The third outcome, next to `mark_sent` and `mark_retry`, and it exists
        because two documented situations are not the provider failing: the
        circuit for this channel is open (docs/06 §4) and the row is non-critical
        inside 20:00–05:00 Bogotá. Charging either one an attempt would spend a
        row's five attempts on something no delivery could have fixed — a whole
        evening of alerts given up at 23:00 with `failed` on all of them, which is
        the opposite of the silence the quiet hours are for. `reason` goes to
        `last_error` because that is the one column that says why a row did not
        move, and `next_attempt_at` is the instant it will be tried again.
        """
        ...
