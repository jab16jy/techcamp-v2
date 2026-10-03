"""The per-cycle impact read view: the raw totals behind a cycle summary.

E11 T3 (D-T0.8; docs/11-metricas.md §1). The view is proven against the real
database with hand-computed expected rows, because the whole point of this view
is which field feeds which metric: an observation's `cost_cop` is a loss and
never a cost (docs/03-modelo-datos.md:424), and a sum with no rows behind it must
read `None` rather than 0 (docs/03:441, D-T0.3).

Every test carries its negative assertion: a tombstoned entry changes nothing, an
entry of another cycle and an entry with no cycle at all change nothing, another
organization cannot read the cycle (docs/09-cuellos-de-botella.md §Seguridad), a
balance day outside the cycle window does not count, and "no stress" (0) is kept
apart from "no evidence" (`None`).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from metrics.conftest import (
    add_alert,
    add_cycle,
    add_logbook_entry,
    add_water_balance,
    bogota_midnight,
    make_env,
)
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.metrics.adapters.source_repository import SqlAlchemyMetricsSourceRepository

pytestmark = pytest.mark.anyio

_OPENED_AT = bogota_midnight(date(2026, 9, 10)) + timedelta(hours=8)
"""A September instant the observation of the loss test is anchored to."""
_CYCLE = {
    "sown_on": date(2026, 9, 1),
    "status": "harvested",
    "expected_harvest_on": date(2026, 9, 30),
}
"""A cycle that closes inside September 2026, so its window is frozen."""


async def test_cycle_totals_sum_each_metric_field_of_its_own_kind(
    db_session: AsyncSession,
) -> None:
    """docs/11 §1 with the table of docs/03-modelo-datos.md:412-424: each field
    feeds its own metric and nothing else.

    The cycle holds one harvest of 500 kg with 300 kg sold at 2000 COP (600000 COP
    of revenue), two tasks of 3 and 2 labor days for 150000 and 90000 COP, an
    input of 300000 COP, a cost entry of 25000 COP, two irrigations of 20 and 5
    mm, and an observation carrying an alert with 40 kg and 70000 COP — the last
    two being the loss, never the cost. A ninth entry, a discarded task of 9
    labor days, changes nothing.

    So `cost_cop` is 150000 + 90000 + 300000 + 25000 = 565000, not 635000, and
    `labor_days` is 5. All four balance days inside the window are stressed
    (`depletion_mm > raw_mm`, i.e. `Ks < 1`) while a fifth one lies outside the
    cycle, so `water_stress_days` is 4.
    """
    env = await make_env(db_session)
    cycle_id = await add_cycle(db_session, env, **_CYCLE)
    alert_id = await add_alert(db_session, env, rule_code="water_stress", at=_OPENED_AT)
    await add_logbook_entry(
        db_session,
        env,
        kind="harvest",
        occurred_on=date(2026, 9, 28),
        yield_kg=500,
        sold_kg=300,
        sale_price_cop_per_kg=2000,
        crop_cycle_id=cycle_id,
    )
    for day, labor_days, cost_cop in ((5, 3, 150_000), (12, 2, 90_000)):
        await add_logbook_entry(
            db_session,
            env,
            kind="task",
            occurred_on=date(2026, 9, day),
            labor_days=labor_days,
            cost_cop=cost_cop,
            crop_cycle_id=cycle_id,
        )
    for kind, day, cost_cop in (("input", 14, 300_000), ("cost", 16, 25_000)):
        await add_logbook_entry(
            db_session,
            env,
            kind=kind,
            occurred_on=date(2026, 9, day),
            cost_cop=cost_cop,
            crop_cycle_id=cycle_id,
        )
    for depth in (20, 5):
        await add_logbook_entry(
            db_session,
            env,
            kind="irrigation",
            occurred_on=date(2026, 9, 20),
            irrigation_mm=depth,
            crop_cycle_id=cycle_id,
        )
    await add_logbook_entry(
        db_session,
        env,
        kind="observation",
        occurred_on=date(2026, 9, 21),
        quantity=40,
        unit="kg",
        cost_cop=70_000,
        alert_id=alert_id,
        crop_cycle_id=cycle_id,
    )
    await add_logbook_entry(
        db_session,
        env,
        kind="task",
        occurred_on=date(2026, 9, 22),
        labor_days=9,
        crop_cycle_id=cycle_id,
        deleted=True,
    )
    for day in (2, 3, 4, 5):
        await add_water_balance(
            db_session, env, day=date(2026, 9, day), depletion_mm=60.0, raw_mm=46.2
        )
    await add_water_balance(db_session, env, day=date(2026, 8, 31), depletion_mm=60.0)

    totals = await SqlAlchemyMetricsSourceRepository(db_session).cycle_totals(env.org_id, cycle_id)

    assert totals is not None
    assert (totals.crop_cycle_id, totals.plot_id) == (cycle_id, env.plot_id)
    assert (totals.yield_kg, totals.sold_kg, totals.revenue_cop) == (
        Decimal("500"),
        Decimal("300"),
        Decimal("600000"),
    )
    assert (totals.labor_days, totals.cost_cop, totals.irrigation_mm) == (
        Decimal("5"),
        Decimal("565000"),
        Decimal("25"),
    )
    assert (totals.loss_kg, totals.loss_cop, totals.water_stress_days) == (
        Decimal("40"),
        Decimal("70000"),
        4,
    )


async def test_cycle_totals_report_no_evidence_as_null(db_session: AsyncSession) -> None:
    """D-T0.3 and docs/03-modelo-datos.md:441: a cycle with no harvest, no
    labor, no cost, no irrigation and no balance reads every metric as `None`,
    not as zero — "no harvest recorded" is not "harvested zero kilos"."""
    env = await make_env(db_session)
    cycle_id = await add_cycle(db_session, env, **_CYCLE)

    totals = await SqlAlchemyMetricsSourceRepository(db_session).cycle_totals(env.org_id, cycle_id)

    assert totals is not None
    assert (
        totals.yield_kg,
        totals.sold_kg,
        totals.revenue_cop,
        totals.labor_days,
        totals.cost_cop,
        totals.irrigation_mm,
        totals.loss_kg,
        totals.loss_cop,
        totals.water_stress_days,
    ) == (None, None, None, None, None, None, None, None, None)


async def test_cycle_totals_ignore_entries_of_another_cycle_and_another_org(
    db_session: AsyncSession,
) -> None:
    """The negatives of `cycle_totals`: an entry of a different cycle and an
    entry with no cycle at all leave this cycle's totals untouched
    (docs/03-modelo-datos.md:410), and another organization cannot read the
    cycle (docs/09 §Seguridad)."""
    env = await make_env(db_session)
    cycle_id = await add_cycle(db_session, env, **_CYCLE)
    other_cycle = await add_cycle(
        db_session,
        env,
        sown_on=date(2026, 9, 1),
        expected_harvest_on=date(2026, 9, 30),
    )
    await add_logbook_entry(
        db_session,
        env,
        kind="harvest",
        occurred_on=date(2026, 9, 28),
        yield_kg=500,
        crop_cycle_id=other_cycle,
    )
    await add_logbook_entry(
        db_session, env, kind="harvest", occurred_on=date(2026, 9, 29), yield_kg=99
    )
    other = await make_env(db_session, name="Finca Otra")

    source = SqlAlchemyMetricsSourceRepository(db_session)
    totals = await source.cycle_totals(env.org_id, cycle_id)

    assert totals is not None and totals.yield_kg is None
    assert await source.cycle_totals(other.org_id, cycle_id) is None


async def test_a_cycle_with_balance_but_no_stress_reads_zero_stress_days(
    db_session: AsyncSession,
) -> None:
    """The difference between "no stress" and "no evidence": a cycle whose
    window holds balance days that are never stressed reads 0, and only a cycle
    with no balance at all reads `None` (D-T0.3)."""
    env = await make_env(db_session)
    cycle_id = await add_cycle(db_session, env, **_CYCLE)
    await add_water_balance(db_session, env, day=date(2026, 9, 2), depletion_mm=10.0)

    totals = await SqlAlchemyMetricsSourceRepository(db_session).cycle_totals(env.org_id, cycle_id)

    assert totals is not None and totals.water_stress_days == 0
