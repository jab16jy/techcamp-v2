"""Draining the outbox (docs/06-diseno-detallado.md §4; ADR-0016; D7).

The whole of docs/06 §4's `loop`, minus the provider: claim the due rows, hand
each to the sender registered for its channel, and write down what happened. A
row is only ever one of four things — `sent`, backed off, given up, or left
alone — and this module is where those four are decided, so the rules are pure
bookkeeping over two ports and the SQL is the adapter's problem.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from techcamp.notifications.application.ports import (
    NotificationSender,
    OutboxRepository,
)
from techcamp.notifications.domain.models import (
    CLAIM_LIMIT,
    MAX_ATTEMPTS,
    Channel,
    retry_delay,
)

_MAX_ERROR_CHARS = 500
"""`notification.last_error` is a `text` column, but a provider's traceback is
not worth storing whole: the row's job is to say it failed, not to keep the
evidence."""


@dataclass(frozen=True, slots=True)
class DispatchReport:
    """What one pass over the outbox did. Counted, not logged per row."""

    claimed: int
    sent: int
    retried: int
    failed: int
    deferred: int


async def dispatch_due_notifications(
    *,
    outbox: OutboxRepository,
    senders: Mapping[Channel, NotificationSender],
    now: datetime,
    limit: int = CLAIM_LIMIT,
) -> DispatchReport:
    """Send every due row whose channel has a sender (docs/06 §4).

    A row whose channel has no sender is counted as deferred and left exactly as
    it is — same status, same attempts, same due time. It has not been tried,
    so it must not spend an attempt: a channel whose adapter is not written yet
    (`push` until T7b) would otherwise burn all five retries and end `failed`
    before any provider existed.
    """
    claimed = await outbox.claim_due(now=now, limit=limit)
    sent = retried = failed = deferred = 0
    for notification in claimed:
        sender = senders.get(notification.channel)
        if sender is None:
            deferred += 1
            continue
        try:
            await sender.send(notification)
        except Exception as exc:  # noqa: BLE001 — every provider failure is the same to us
            attempts = notification.attempts + 1
            error = str(exc)[:_MAX_ERROR_CHARS] or type(exc).__name__
            if attempts >= MAX_ATTEMPTS:
                await outbox.mark_failed(notification.id, attempts=attempts, error=error)
                failed += 1
            else:
                await outbox.mark_retry(
                    notification.id,
                    attempts=attempts,
                    next_attempt_at=now + retry_delay(attempts),
                    error=error,
                )
                retried += 1
        else:
            await outbox.mark_sent(notification.id, at=now)
            sent += 1
    return DispatchReport(
        claimed=len(claimed), sent=sent, retried=retried, failed=failed, deferred=deferred
    )
