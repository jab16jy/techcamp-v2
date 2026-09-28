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
    """What one run over the outbox did. Counted, not logged per row."""

    claimed: int
    sent: int
    retried: int
    failed: int
    skipped: int

    def __add__(self, other: DispatchReport) -> DispatchReport:
        return DispatchReport(
            claimed=self.claimed + other.claimed,
            sent=self.sent + other.sent,
            retried=self.retried + other.retried,
            failed=self.failed + other.failed,
            skipped=self.skipped + other.skipped,
        )


_EMPTY_REPORT = DispatchReport(0, 0, 0, 0, 0)


async def dispatch_due_notifications(
    *,
    outbox: OutboxRepository,
    senders: Mapping[Channel, NotificationSender],
    now: datetime,
    limit: int = CLAIM_LIMIT,
) -> DispatchReport:
    """Send every due row whose channel has a sender (docs/06 §4).

    Runs pass after pass while a pass comes back FULL, so a backlog is drained in
    one run instead of at 50 rows a minute (RNF-05, p95 < 2 min). The loop ends
    because every pass either moves its rows out of the due set (sent, given up,
    or backed off to a later `next_attempt_at`) or comes back short: a row
    another worker holds is passed over by the claim and is not counted.

    Delivery is AT LEAST ONCE, and deliberately so: a worker that dies after the
    provider accepted a message but before `sent` is committed leaves the row
    `pending`, and the next sweep sends it again. A possible duplicate is
    preferred over a lost critical alert (docs/06 §4; RF-08, RNF-05).
    """
    if not senders:
        return _EMPTY_REPORT
    report = _EMPTY_REPORT
    while True:
        passed = await _one_pass(outbox=outbox, senders=senders, now=now, limit=limit)
        report += passed
        if passed.claimed < limit:
            return report


async def _one_pass(
    *,
    outbox: OutboxRepository,
    senders: Mapping[Channel, NotificationSender],
    now: datetime,
    limit: int,
) -> DispatchReport:
    """One `SELECT … FOR UPDATE SKIP LOCKED LIMIT …` and the rows it holds.

    A row is locked again right before it is sent, and that second lock is what
    makes the batch safe: the claim's locks all end with the first row's commit,
    so without it a second worker could pick up an unprocessed row of this batch
    and the row would be sent twice.
    """
    claimed = await outbox.claim_due(now=now, channels=list(senders), limit=limit)
    sent = retried = failed = skipped = 0
    for notification in claimed:
        if not await outbox.hold(notification.id, now=now):
            skipped += 1
            continue
        try:
            await senders[notification.channel].send(notification)
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
        claimed=len(claimed), sent=sent, retried=retried, failed=failed, skipped=skipped
    )
