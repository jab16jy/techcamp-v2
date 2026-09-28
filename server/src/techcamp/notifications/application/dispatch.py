"""Draining the outbox (docs/06-diseno-detallado.md §4; ADR-0016; D7).

The whole of docs/06 §4's `loop`, minus the provider: claim the due rows, decide
whether each channel's provider is accepting deliveries, hand the row to the
sender registered for its channel, and write down what happened. A row is only
ever one of five things — `sent`, backed off, given up, held until a circuit
closes, or left alone — and this module is where those five are decided, so the
rules are pure bookkeeping over three ports and the SQL is the adapter's problem.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from techcamp.notifications.application.ports import (
    NotificationSender,
    OutboxRepository,
    ProviderCircuits,
)
from techcamp.notifications.domain.errors import RowNotDeliverableError
from techcamp.notifications.domain.models import (
    ALTERNATE_CHANNELS,
    CLAIM_LIMIT,
    CRITICAL_SEVERITY,
    MAX_ATTEMPTS,
    Channel,
    FinalAttempt,
    PendingNotification,
    RetrySchedule,
    in_quiet_hours,
    quiet_hours_until,
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
    deferred: int = 0
    """Due rows held back without an attempt: the channel's circuit is open, or a
    non-critical row has come due inside the quiet hours. A third outcome, and the
    one that would otherwise be invisible — a run can claim fifty rows and move
    none of them without anything having failed."""

    def __add__(self, other: DispatchReport) -> DispatchReport:
        return DispatchReport(
            claimed=self.claimed + other.claimed,
            sent=self.sent + other.sent,
            retried=self.retried + other.retried,
            failed=self.failed + other.failed,
            skipped=self.skipped + other.skipped,
            deferred=self.deferred + other.deferred,
        )


_EMPTY_REPORT = DispatchReport(0, 0, 0, 0, 0)


async def dispatch_due_notifications(
    *,
    outbox: OutboxRepository,
    senders: Mapping[Channel, NotificationSender],
    circuits: ProviderCircuits,
    now: datetime,
    limit: int = CLAIM_LIMIT,
) -> DispatchReport:
    """Send every due row whose channel has a sender (docs/06 §4).

    Runs pass after pass while a pass comes back FULL, so a backlog is drained in
    one run instead of at 50 rows a minute (RNF-05, p95 < 2 min). The loop ends
    because every pass either moves its rows out of the due set (sent, given up,
    backed off to a later `next_attempt_at`, or held to the end of a circuit's
    cooldown) or comes back short: a row another worker holds is passed over by
    the claim and is not counted.

    Delivery is AT LEAST ONCE, and deliberately so: a worker that dies after the
    provider accepted a message but before `sent` is committed leaves the row
    `pending`, and the next sweep sends it again. A possible duplicate is
    preferred over a lost critical alert (docs/06 §4; RF-08, RNF-05). A held row
    is not on that trade: it is still `pending`, so it cannot be lost either, and
    the instant it is held until is the circuit's own cooldown.
    """
    if not senders:
        return _EMPTY_REPORT
    report = _EMPTY_REPORT
    while True:
        passed = await _one_pass(
            outbox=outbox, senders=senders, circuits=circuits, now=now, limit=limit
        )
        report += passed
        if passed.claimed < limit:
            return report


async def _one_pass(
    *,
    outbox: OutboxRepository,
    senders: Mapping[Channel, NotificationSender],
    circuits: ProviderCircuits,
    now: datetime,
    limit: int,
) -> DispatchReport:
    """One `SELECT … FOR UPDATE SKIP LOCKED LIMIT …` and the rows it holds.

    A row is locked again right before it is sent, and that second lock is what
    makes the batch safe: the claim's locks all end with the first row's commit,
    so without it a second worker could pick up an unprocessed row of this batch
    and the row would be sent twice.

    The batch is also where docs/06 §4's grouping happens, because the batch is
    the only place that knows what else is due right now. Rows are taken in due
    order, so a group is the run of consecutive rows of one farm that belong to
    one user, and each group leaves as one message.
    """
    claimed = await outbox.claim_due(
        now=now,
        channels=list(senders),
        unconfigured=_unconfigured(senders),
        limit=limit,
    )
    sent = retried = failed = skipped = deferred = 0
    group: list[PendingNotification] = []
    group_channel: Channel | None = None

    async def flush() -> None:
        """Send the group built so far, if it has one.

        `group_channel` is the emptiness test rather than `group` alone, because
        a group carries the channel it goes out on: a critical on an open push
        circuit leaves as SMS while its row still says `push` (D37), and the
        channel is the dispatcher's knowledge, not the row's.
        """
        nonlocal sent, retried, failed, group_channel
        if group_channel is None:
            return
        outcome = await _deliver(
            outbox=outbox,
            senders=senders,
            circuits=circuits,
            group=group,
            channel=group_channel,
            now=now,
        )
        sent += outcome.sent
        retried += outcome.retried
        failed += outcome.failed
        group.clear()
        group_channel = None

    for notification in claimed:
        if not await outbox.hold(notification.id, now=now):
            await flush()
            skipped += 1
            continue
        if _is_silenced(notification, now):
            await flush()
            # docs/06 §4's "Horas de silencio" at the point the row actually
            # leaves. D6 applies them when the row is WRITTEN, which is not the
            # same thing: `next_attempt_at` is also written by a retry backoff and
            # by the circuit's cooldown, and neither knows about 20:00, so a
            # `warning` whose two-hour retry landed at 03:00 would ring a farmer's
            # phone at three in the morning. No attempt either — the delivery was
            # never refused by anybody, it was simply not this hour (D39).
            await outbox.mark_deferred(
                notification.id,
                next_attempt_at=quiet_hours_until(now),
                reason="the farm is in its quiet hours until 05:00",
            )
            deferred += 1
            continue
        if group and not _joins(group[-1], notification):
            # Start a new message by sending the one already built, BEFORE this
            # row's channel is decided. That ordering is the whole reason a pass
            # stops calling a provider that just failed five times: the decision
            # then sees the failures this pass caused, so the circuit bites here
            # instead of on the next sweep a minute later.
            await flush()
        channel = _where_it_can_go(notification, senders=senders, circuits=circuits)
        if channel is None:
            await flush()
            # The provider is refusing, and a refused call would cost this row one
            # of its five attempts on a delivery nothing could have fixed. The row
            # is held until the circuit's own cooldown, so it becomes due exactly
            # when the provider is allowed its next trial (docs/06 §4; D30, D35).
            await outbox.mark_deferred(
                notification.id,
                next_attempt_at=now
                + timedelta(seconds=circuits.cooldown_remaining(notification.channel)),
                reason=f"the {notification.channel.value} circuit is open",
            )
            deferred += 1
            continue
        if group and group_channel != channel:
            # A row that joins the group but resolves to another channel (nothing
            # routes a non-critical to an alternate today, D37) still cannot ride
            # in a message going somewhere else.
            await flush()
        group.append(notification)
        group_channel = channel
    await flush()
    return DispatchReport(
        claimed=len(claimed),
        sent=sent,
        retried=retried,
        failed=failed,
        skipped=skipped,
        deferred=deferred,
    )


async def _deliver(
    *,
    outbox: OutboxRepository,
    senders: Mapping[Channel, NotificationSender],
    circuits: ProviderCircuits,
    group: Sequence[PendingNotification],
    channel: Channel,
    now: datetime,
) -> DispatchReport:
    """One message to one provider, and its outcome written to every row it covers.

    A group is the message, so a failed delivery is a failed attempt for each of
    the alerts in it and a retry re-sends them together: the farmer hears about
    the farm once, which is the whole point of grouping, and hearing about it late
    is the same as not hearing.

    Every row in a group belongs to the same user, so the next delay is the same
    for all of them; the group's `attempts` is read from its first row for that
    reason, and a group is never built out of rows of different ages because the
    claim takes them in due order.

    The outcome is written ONCE for the whole message, not once per row: the
    commit is what releases the claim's locks, and a commit per row would leave
    the message's remaining rows `pending` and unlocked after the message was
    already delivered, which is a duplicate waiting for a second worker (R3-001).

    A failed message writes TWICE — the rows that are given up first, the rows
    that will retry second — and the order is the safety, because the first
    commit already released the locks: a row that is still `pending` and still
    due at that moment is claimable by a second worker. The given-up row is
    `failed` before that happens, and the retrying rows carry a `next_attempt_at`
    in the future, which the claim's `next_attempt_at <= now` refuses.

    Each row's own count moves by one and its own instant is derived from that
    count: `attempts` is a column of the notification (docs/03) and "máximo 5
    intentos" is per notification (docs/06 §4), so one delivery that covered
    several alerts is one attempt for each of them and not the oldest row's count
    written over the rest (R3-002).
    """
    sent = retried = failed = 0
    rows = [notification.id for notification in group]
    try:
        await senders[channel].send(group)
    except Exception as exc:  # noqa: BLE001 — every provider failure is the same to us
        if not isinstance(exc, RowNotDeliverableError):
            # docs/06 §4 counts the failures of a PROVIDER, and a row that cannot
            # be delivered at all is not one of them (D36). The failure belongs to
            # the channel that was used, which for a critical on an open push
            # circuit is the alternate.
            circuits.record_failure(channel)
        error = str(exc)[:_MAX_ERROR_CHARS] or type(exc).__name__
        given_up, retrying = _outcomes(group, now=now)
        if given_up:
            await outbox.mark_failed(given_up, error=error)
            failed = len(given_up)
        if retrying:
            await outbox.mark_retry(retrying, error=error)
            retried = len(retrying)
    else:
        circuits.record_success(channel)
        await outbox.mark_sent(rows, at=now)
        sent = len(rows)
    return DispatchReport(0, sent, retried, failed, 0, 0)


def _unconfigured(senders: Mapping[Channel, NotificationSender]) -> list[Channel]:
    """Channels with no sender whose CRITICAL rows can still go somewhere.

    docs/06 §4's "mientras tanto las críticas pasan al canal alterno" and D40: a
    channel that was never configured behaves like a channel whose circuit is
    open — for a critical, which has somewhere else to go, and only for a critical,
    because a `warning` does not (D5) and claiming it would spend a pass on a row
    that cannot leave the batch.

    "Somewhere else to go" is checked here and not in the claim, so the claim never
    has to know what is registered: a channel with no sender AND no alternate with
    one is not in this list, and its rows are not claimed at all, exactly as D31
    says for a channel nobody can send.
    """
    return [
        channel
        for channel, alternates in ALTERNATE_CHANNELS.items()
        if channel not in senders and any(candidate in senders for candidate in alternates)
    ]


def _outcomes(
    group: Sequence[PendingNotification], *, now: datetime
) -> tuple[list[FinalAttempt], list[RetrySchedule]]:
    """A failed message's own outcome for every row it covered, split by fate.

    One attempt added to EACH row's count, and each row's next instant derived
    from that same count, so a group of rows of different ages ends the message
    with a row at 5 `failed` beside a row at 1 waiting a minute — and never the
    reverse (docs/06 §4; R3-002). The two lists are disjoint by construction: a
    row's fate is decided once, from its own count.
    """
    given_up: list[FinalAttempt] = []
    retrying: list[RetrySchedule] = []
    for notification in group:
        attempts = notification.attempts + 1
        if attempts >= MAX_ATTEMPTS:
            given_up.append(FinalAttempt(notification_id=notification.id, attempts=attempts))
        else:
            retrying.append(
                RetrySchedule(
                    notification_id=notification.id,
                    attempts=attempts,
                    next_attempt_at=now + retry_delay(attempts),
                )
            )
    return given_up, retrying


def _is_silenced(notification: PendingNotification, now: datetime) -> bool:
    """Whether this row must not leave now because the farm is asleep.

    docs/06 §4 "Horas de silencio": "20:00–05:00: solo notificaciones críticas". A
    critical is the one severity that may break it, and D6 says so on the write
    side too, so the two can never disagree about which rows are exempt.
    """
    return notification.severity != CRITICAL_SEVERITY and in_quiet_hours(now)


def _joins(previous: PendingNotification, candidate: PendingNotification) -> bool:
    """Whether `candidate` rides out in the same message as `previous`.

    docs/06 §4 "Agrupación": "Varias alertas no críticas de la misma finca en 15
    min se envían en una sola notificación", and D6 keys it on the (user, farm)
    pair — one recipient gets one message per farm, and two farms of one
    organization are two messages because the message cannot name which farm it is
    about.

    Three rows never join, each for its own reason:

    - a critical, which RNF-05 gives two minutes and which must not wait behind a
      message it has nothing to do with (D5);
    - a row whose farm could not be resolved, sent on its own rather than merged
      with another row of unknown farm — `None` is "not known", never "the same
      farm" (D38);
    - a row of a different user, because a message is delivered to one recipient's
      browsers and nobody else's.
    """
    return (
        previous.severity != CRITICAL_SEVERITY
        and candidate.severity != CRITICAL_SEVERITY
        and previous.farm_id is not None
        and previous.farm_id == candidate.farm_id
        and previous.user_id == candidate.user_id
    )


def _where_it_can_go(
    notification: PendingNotification,
    *,
    senders: Mapping[Channel, NotificationSender],
    circuits: ProviderCircuits,
) -> Channel | None:
    """The channel this row's message goes out on now, or `None` while it waits.

    Its own channel whenever there is a sender for it and that channel's circuit
    allows a call. When there is not — the circuit is open, or the channel was
    never configured (D40) — only a critical has anywhere else to go (docs/06 §4:
    "mientras tanto las críticas pasan al canal alterno"; D5, D37), and the
    alternates are the ones `ALTERNATE_CHANNELS` names that have a sender of their
    own and a circuit that is not refusing — so one provider being down never means
    the row is sent to a second one that is down too, and never means it is sent
    through a channel with no adapter (D31).

    `None` is a real answer, not a failure: the caller holds the row without
    spending an attempt, and `pending` is what keeps a critical from being lost
    while every provider is down.
    """
    if notification.channel in senders and circuits.allows(notification.channel):
        return notification.channel
    if notification.severity != CRITICAL_SEVERITY:
        return None
    for candidate in ALTERNATE_CHANNELS.get(notification.channel, ()):
        if candidate in senders and circuits.allows(candidate):
            return candidate
    return None
