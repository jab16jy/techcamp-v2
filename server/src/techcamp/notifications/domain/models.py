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
