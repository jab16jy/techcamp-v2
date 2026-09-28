"""Notification outbox value and when a row becomes due (docs/03:298-306; docs/06 §4; ADR-0016).

Pure, no I/O. It knows nothing about alerts: criticality arrives as a `bool`,
so `notifications` never imports `alerts` (no cycle, docs/05 D14).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo

BOGOTA = ZoneInfo("America/Bogota")
"""Farm local time (docs/06 §4 hours of silence and 05:00 release are Bogotá)."""

QUIET_HOURS_FROM = 20
QUIET_HOURS_UNTIL = 5

MAX_ATTEMPTS = 5
"""docs/06 §4: "máximo 5 intentos, luego `failed`"."""

CLAIM_LIMIT = 50
"""docs/06 §4: `FOR UPDATE SKIP LOCKED LIMIT 50` — one dispatcher pass takes at
most 50 rows, so a burst never holds a worker on an unbounded batch."""

RETRY_DELAYS = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
)
"""docs/06 §4: "Backoff exponencial: 1 min, 5 min, 30 min, 2 h" — the wait after
the first, second, third and fourth failed attempt. The fifth is the last one,
so there is no delay after it."""

BREAKER_FAILURE_THRESHOLD = 5
"""Consecutive failures of one provider that open its circuit (docs/06 §4:
"Con 5 fallos seguidos se abre 5 min"; ADR-0016's "un circuit breaker por
proveedor"). Not a setting: the doc states the number, and a configurable
threshold would be a way to ship a breaker that does not match the doc."""

BREAKER_COOLDOWN = timedelta(minutes=5)
"""How long an open circuit refuses before letting one delivery through to try
the provider again (docs/06 §4: "se abre 5 min"; D35)."""


class Channel(StrEnum):
    """Delivery channel of a notification row (docs/03:301 `push|sms|whatsapp`)."""

    PUSH = "push"
    SMS = "sms"
    WHATSAPP = "whatsapp"


CRITICAL_SEVERITY = "critical"
"""docs/03:288, the one severity `docs/06 §4` gives a second channel.

Spelled out here rather than imported from `alerts.domain.Severity` because
`notifications` never imports `alerts` (D14, docs/05) — and `Severity` is a
`StrEnum`, so the value compared against `PendingNotification.severity` is this
same string either way.
"""

ALTERNATE_CHANNELS: Mapping[Channel, tuple[Channel, ...]] = {
    Channel.PUSH: (Channel.SMS, Channel.WHATSAPP),
}
"""Where a row goes when its own channel's provider is down (docs/06 §4).

docs/06 §4's circuit-breaker row: "mientras tanto las críticas pasan al canal
alterno", and docs/01 RF-08 names the alternates for a critical: "SMS o
WhatsApp como respaldo para alertas críticas". The order is the doc's own
severity order — push first, then SMS or WhatsApp — so the first one that has a
sender and a closed circuit is the one to use.

A channel missing from this map has NO alternate, and that is deliberate for
`sms`/`whatsapp`: docs/06 §4's "Canales por severidad" makes them the last
resort, D34 leaves the critical's `sms` row to T8's escalation job, and D4 sends
that row to the farm's technician. Falling back from an escalation to push would
turn the escalation back into the notification the recipient already got.
"""


@dataclass(frozen=True, slots=True)
class NotificationDraft:
    """A pending `notification` row to write in the alert's transaction (docs/06 §4)."""

    user_id: UUID
    channel: Channel
    next_attempt_at: datetime


def in_quiet_hours(now: datetime) -> bool:
    """Whether `now` falls in the farm's silence, 20:00–05:00 (docs/06 §4).

    Read in **America/Bogota**, never in UTC and never against the UTC date: the
    hour belongs to the farmer who is not going to be woken up, and 05:00 Bogotá is
    10:00 UTC, so an instant can be quiet in one clock and the middle of the
    afternoon in the other. That is why both facts live in one function: the write
    side (`next_attempt_at`) and the send side (the dispatcher) must not be able to
    disagree about which clock they mean.
    """
    local = now.astimezone(BOGOTA)
    return QUIET_HOURS_FROM <= local.hour or local.hour < QUIET_HOURS_UNTIL


def quiet_hours_until(now: datetime) -> datetime:
    """The next 05:00 Bogotá after `now`, as an instant (docs/06 §4, D6).

    The DATE is Bogotá's too: at 00:30 Bogotá the next 05:00 is the same calendar
    day in Bogotá, and computing it from the UTC date would hold the row until a
    05:00 that had already passed.
    """
    local = now.astimezone(BOGOTA)
    morning = local.replace(hour=QUIET_HOURS_UNTIL, minute=0, second=0, microsecond=0)
    if local.hour >= QUIET_HOURS_FROM:
        morning += timedelta(days=1)
    return morning.astimezone(UTC)


def next_attempt_at(
    *, critical: bool, now: datetime, group_time: datetime | None = None
) -> datetime:
    """When a new row becomes due (docs/06 §4 quiet hours and grouping; D6).

    A critical is due now: it is the one severity that may break the silence.
    A non-critical row joins the group it lands in (the `next_attempt_at` of the
    pending row it groups with) and otherwise waits out 20:00–05:00 Bogotá.

    This is the WRITE side of the quiet hours. The send side re-checks the hour
    before a row leaves, because a row's `next_attempt_at` is also written by a
    retry backoff and by the circuit's cooldown, and neither of those knows about
    20:00 (D39).
    """
    if critical:
        return now.astimezone(UTC)
    if group_time is not None:
        return group_time.astimezone(UTC)
    if in_quiet_hours(now):
        return quiet_hours_until(now)
    return now.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class RetrySchedule:
    """A row that lost a message and will be tried again, on its own count.

    `attempts` is a column of the ROW (docs/03) and docs/06 §4's "máximo 5
    intentos" is per notification, so a message covering several rows adds one
    attempt to each of them and derives each one's next instant from that row's
    own count — never one row's count written over the others, which is how a row
    that had never been attempted could reach `failed` on its first real delivery
    (R3-002).
    """

    notification_id: UUID
    attempts: int
    next_attempt_at: datetime


@dataclass(frozen=True, slots=True)
class FinalAttempt:
    """A row whose last attempt of a message has failed (docs/06 §4).

    No instant, and deliberately not a nullable one: a row being given up is not
    due again, and the two facts are different facts rather than one value and a
    hole. A grouped message ends with a `FinalAttempt` beside a `RetrySchedule`,
    never one row's fate written over another's.
    """

    notification_id: UUID
    attempts: int


@dataclass(frozen=True, slots=True)
class PendingNotification:
    """A due outbox row, as the dispatcher holds it (docs/06 §4; ADR-0016).

    The alert's own fields come along because they are what a message is *about*:
    `rule_code` and `severity` are the two a provider needs to render an alert
    (the Web Push body, the seminar log line), and `org_id` is the
    organization the row belongs to, denormalized on `alert` (docs/03:18).
    `attempts` travels because the next delay depends on how many are already
    spent, and the dispatcher must not have to re-read the row to know it.

    `farm_id` is what docs/06 §4's grouping is keyed on ("Varias alertas no
    críticas de la misma finca"), and `alert` stores no farm: it is the alert's
    plot's farm, or for a node alert the farm of the plot the node hangs on
    (docs/03:284-297). It is `None` when neither resolves, and `None` means "this
    row's farm is not known", never "this row has no farm": such a row is sent on
    its own rather than merged with another unknown one, and it is never dropped
    from a claim because of it.
    """

    id: UUID
    alert_id: UUID
    org_id: UUID
    user_id: UUID
    channel: Channel
    rule_code: str
    severity: str
    attempts: int
    farm_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class PushSubscription:
    """One browser's `push_subscription` row, as the sender needs it
    (docs/03:312-318; docs/06 §4).

    `endpoint` and the VAPID key pair travel together because that is the
    `PushSubscription` a push service expects (docs/04:145 names `p256dh` and
    `auth`), and `id` travels because a `404`/`410` deletes THIS row — the sender
    has no other way to name it.
    """

    id: UUID
    endpoint: str
    keys: dict[str, str]


def retry_delay(attempts: int) -> timedelta:
    """How long to wait after the `attempts`-th attempt failed (docs/06 §4).

    `attempts` counts the attempt that just failed, so it starts at 1 and stops
    at `MAX_ATTEMPTS - 1`: the fifth failure is the row's last, not a wait.
    """
    return RETRY_DELAYS[attempts - 1]
