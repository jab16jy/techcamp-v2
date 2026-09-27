"""America/Bogota local-day helpers (docs/04-api.md:63-75, docs/06 §5, docs/10-dag.md:156).

The product operates in a single local timezone: America/Bogota (UTC-5, no DST).
Orchestration cron jobs (weather consolidation, irrigation daily run) and API defaults
resolve "today" and "yesterday" against this timezone.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

BOGOTA_TZ = ZoneInfo("America/Bogota")
"""The single local timezone of the product (docs/04-api.md:63-75, docs/06 §5)."""


def local_today(now: datetime | None = None) -> date:
    """The date of `now` (defaulting to current instant) in America/Bogota.

    A naive `now` is read as UTC, which is how the application layer stores and
    passes instants, so a naive value cannot silently become local time. Every
    read that defaults a date ("today" for a recommendation or weather forecast,
    "yesterday" for a water balance or weather consolidation) resolves it
    through here, so one place owns the timezone (docs/04-api.md:63-75; docs/06 §5).
    """
    if now is None:
        now = datetime.now(UTC)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(BOGOTA_TZ).date()
