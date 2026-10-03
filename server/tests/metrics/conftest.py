"""Builders for the metrics module's tests (E11 T3).

The org, farm, plot, node, sensor, recommendation and alert builders are reused
from `tests/home/conftest.py` (E9 T2) instead of copied: one definition of "a
farm", so a change to the shared shape reaches every module.

They are imported as `home.conftest`, not `tests.home.conftest`: pytest puts
`server/tests` on `sys.path` when it imports the shared `tests/conftest.py`, so
a sibling test directory is reachable by its own name. The `tests.`-prefixed
form is not resolvable that early — the `TYPE_CHECKING`-only import in
`tests/logbook/test_push.py:35` never executes.

What the shared set does not have lives here: a logbook entry carrying the
impact fields `docs/03-modelo-datos.md:412-424` assigns to each `kind`, a node
claimed on a chosen day, and a reading whose `value` is null.

Reference dates are frozen in September 2026, a **closed** month: the
`monitoring` view caps its window at the month end only because the month is in
the past, so `now()` cannot move the expected numbers (E11 lessons, #12).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from home.conftest import (
    NOW,
    HomeEnv,
    add_alert,
    add_node,
    add_plot,
    add_reading,
    add_recommendation,
    add_sensor,
    add_water_balance,
    make_env,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.orm import AlertRuleRow
from techcamp.alerts.adapters.repositories import SqlAlchemyAlertRepository
from techcamp.alerts.application import open_alert
from techcamp.alerts.domain import AlertRule, Severity
from techcamp.farms.adapters.orm import CropCycleRow, CropRow  # noqa: F401
from techcamp.logbook.adapters.orm import LogbookEntryRow
from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.orm import NodeRow, ReadingRow

MONTH = date(2026, 9, 1)
"""September 2026, the closed month every adoption-index view is read for."""

BOGOTA_OFFSET = timedelta(hours=-5)
"""America/Bogota is UTC-5 with no DST (`shared.dates.BOGOTA_TZ`), so the local
midnight of a frozen date is a plain offset and the expected seconds of the
monitoring view can be written out by hand."""


def bogota_midnight(day: date) -> datetime:
    """Local midnight of `day` in America/Bogota, as an aware instant.

    Local midnight is 00:00 *local*, which is `UTC + 5 h` in absolute time, so
    the offset is subtracted from midnight UTC. Getting this backwards silently
    moves every expected value by five hours.
    """
    return datetime(day.year, day.month, day.day, tzinfo=UTC) - BOGOTA_OFFSET


def dec(value: float | None) -> Decimal | None:
    """`float | None` to `Decimal | None`, so a builder and its expectation can
    be written with the same numbers."""
    return None if value is None else Decimal(str(value))


async def add_claimed_node(
    session: AsyncSession,
    env: HomeEnv,
    *,
    claim_code: str,
    claimed_at: datetime,
    interval_s: int = 300,
) -> UUID:
    """A claimed node with the claim instant the test chooses.

    The shared `add_node` always writes `claimed_at` at 2026-01-01, which is
    enough for a node claimed before the month under test but cannot express a
    node claimed *inside* it — and that mid-month claim is exactly what the
    `monitoring` window is about (D-T0.4).
    """
    node_id = uuid7()
    session.add(
        NodeRow(
            id=node_id,
            org_id=env.org_id,
            plot_id=env.plot_id,
            transport="wifi",
            dev_eui=None,
            claim_code=claim_code,
            credential_hash="hash",
            firmware="v1",
            interval_s=interval_s,
            claimed_at=claimed_at,
            last_seen_at=None,
            status="online",
        )
    )
    await session.commit()
    return node_id


async def add_reading_row(
    session: AsyncSession,
    sensor_id: int,
    *,
    at: datetime,
    value: float | None,
    quality: int = 0,
) -> None:
    """One reading, `value` nullable.

    The shared `add_reading` always writes a value, so it cannot express an
    out-of-range reading (`value IS NULL`, `quality = 2`). Counting those is
    what proves `monitoring` reads the `reading` table and not the
    `reading_daily` continuous aggregate, whose `count(value)` skips them
    (docs/11 §2: "con cualquier `quality`").
    """
    session.add(
        ReadingRow(
            time=at,
            sensor_id=sensor_id,
            raw_value=value if value is not None else 9999.0,
            value=value,
            received_at=at,
            quality=quality,
        )
    )
    await session.commit()


async def add_logbook_entry(
    session: AsyncSession,
    env: HomeEnv,
    *,
    kind: str,
    occurred_on: date,
    crop_cycle_id: UUID | None = None,
    quantity: float | None = None,
    unit: str | None = None,
    cost_cop: float | None = None,
    yield_kg: float | None = None,
    sold_kg: float | None = None,
    sale_price_cop_per_kg: float | None = None,
    labor_days: float | None = None,
    irrigation_mm: float | None = None,
    alert_id: UUID | None = None,
    deleted: bool = False,
) -> UUID:
    """One logbook entry carrying the fields `docs/03-modelo-datos.md:412-424`
    assigns to `kind`. `deleted=True` is the tombstone an offline discard leaves
    behind, which no metric may read (docs/03:237)."""

    entry_id = uuid7()
    session.add(
        LogbookEntryRow(
            id=entry_id,
            org_id=env.org_id,
            plot_id=env.plot_id,
            crop_cycle_id=crop_cycle_id,
            kind=kind,
            occurred_on=occurred_on,
            quantity=dec(quantity),
            unit=unit,
            cost_cop=dec(cost_cop),
            yield_kg=dec(yield_kg),
            sold_kg=dec(sold_kg),
            sale_price_cop_per_kg=dec(sale_price_cop_per_kg),
            labor_days=dec(labor_days),
            irrigation_mm=dec(irrigation_mm),
            alert_id=alert_id,
            notes=None,
            created_by=env.user_id,
            client_updated_at=NOW,
            deleted_at=NOW if deleted else None,
        )
    )
    await session.commit()
    return entry_id


async def add_node_alert(
    session: AsyncSession,
    *,
    org_id: UUID,
    node_id: UUID,
    rule_code: str = "node_battery_low",
    at: datetime,
) -> UUID:
    """One alert on a node, not on a plot.

    Node alerts go to the technician and count for nothing in the plot's index
    (docs/11 §2), so the plot-alert test needs one of these to prove the view
    leaves it out.
    """
    row = (
        await session.execute(select(AlertRuleRow).where(AlertRuleRow.code == rule_code))
    ).scalar_one()
    rule = AlertRule(
        id=row.id,
        org_id=None,
        code=row.code,
        metric=row.metric,
        operator=row.operator,
        threshold=float(row.threshold) if row.threshold is not None else None,
        hysteresis=float(row.hysteresis),
        min_duration=timedelta(minutes=row.min_duration_min),
        severity=Severity(row.severity),
        crop_id=row.crop_id,
    )
    alert = await open_alert(
        rule=rule,
        at=at,
        alerts=SqlAlchemyAlertRepository(session),
        node_id=node_id,
        evidence={"battery_v": 3.1},
    )
    assert alert.org_id == org_id
    return alert.id


__all__ = [
    "MONTH",
    "NOW",
    "HomeEnv",
    "add_alert",
    "add_node",
    "add_plot",
    "add_reading",
    "add_recommendation",
    "add_sensor",
    "add_water_balance",
    "make_env",
]
"""The shared builders above are re-exported, not used here: ruff's F401 needs
the list to tell a re-export from a forgotten import."""
