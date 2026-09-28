"""Notification outbox value and when a row becomes due (docs/03:298-306; docs/06 §4; ADR-0016).

Pure, no I/O. It knows nothing about alerts: criticality arrives as a `bool`,
so `notifications` never imports `alerts` (no cycle, docs/05 D14).
"""

from __future__ import annotations

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


class Channel(StrEnum):
    """Delivery channel of a notification row (docs/03:301 `push|sms|whatsapp`)."""

    PUSH = "push"
    SMS = "sms"
    WHATSAPP = "whatsapp"


@dataclass(frozen=True, slots=True)
class NotificationDraft:
    """A pending `notification` row to write in the alert's transaction (docs/06 §4)."""

    user_id: UUID
    channel: Channel
    next_attempt_at: datetime


def next_attempt_at(
    *, critical: bool, now: datetime, group_time: datetime | None = None
) -> datetime:
    """When a new row becomes due (docs/06 §4 quiet hours and grouping; D6).

    A critical is due now: it is the one severity that may break the silence.
    A non-critical row joins the group it lands in (the `next_attempt_at` of the
    pending row it groups with) and otherwise waits out 20:00–05:00 Bogotá.
    """
    if critical:
        return now.astimezone(UTC)
    if group_time is not None:
        return group_time.astimezone(UTC)
    local = now.astimezone(BOGOTA)
    if QUIET_HOURS_FROM <= local.hour or local.hour < QUIET_HOURS_UNTIL:
        morning = local.replace(hour=QUIET_HOURS_UNTIL, minute=0, second=0, microsecond=0)
        if local.hour >= QUIET_HOURS_FROM:
            morning += timedelta(days=1)
        return morning.astimezone(UTC)
    return now.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class PendingNotification:
    """A due outbox row, as the dispatcher holds it (docs/06 §4; ADR-0016).

    The alert's own fields come along because they are what a message is *about*:
    `rule_code` and `severity` are the two a provider needs to render an alert
    (the push body in T7b, the seminar log line today), and `org_id` is the
    organization the row belongs to, denormalized on `alert` (docs/03:18).
    `attempts` travels because the next delay depends on how many are already
    spent, and the dispatcher must not have to re-read the row to know it.
    """

    id: UUID
    alert_id: UUID
    org_id: UUID
    user_id: UUID
    channel: Channel
    rule_code: str
    severity: str
    attempts: int


def retry_delay(attempts: int) -> timedelta:
    """How long to wait after the `attempts`-th attempt failed (docs/06 §4).

    `attempts` counts the attempt that just failed, so it starts at 1 and stops
    at `MAX_ATTEMPTS - 1`: the fifth failure is the row's last, not a wait.
    """
    return RETRY_DELAYS[attempts - 1]
