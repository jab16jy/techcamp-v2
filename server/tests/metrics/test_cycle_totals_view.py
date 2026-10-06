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

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from metrics.conftest import (
    add_alert,
    add_cycle,
    add_logbook_entry,
    add_water_balance,
    bogota_midnight,
    make_env,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import CropCycleRow
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
    with no balance at all reads `None` (D-T0.3).

    The cycle is finished, so its window is bounded by the entry that registers
    its closure (D-T7.2): the Sep 20 `harvest` entry is what makes the cycle
    measurable at all. Without it the figure would be `None`, which is the rule
    the next test pins — so this one has to carry the anchor to keep 0 and
    `None` apart."""
    env = await make_env(db_session)
    cycle_id = await add_cycle(db_session, env, **_CYCLE)
    await add_logbook_entry(
        db_session,
        env,
        kind="harvest",
        occurred_on=date(2026, 9, 20),
        yield_kg=400,
        crop_cycle_id=cycle_id,
    )
    await add_water_balance(db_session, env, day=date(2026, 9, 2), depletion_mm=10.0)

    totals = await SqlAlchemyMetricsSourceRepository(db_session).cycle_totals(env.org_id, cycle_id)

    assert totals is not None and totals.water_stress_days == 0


async def test_a_finished_cycle_stops_counting_at_the_entry_that_closed_it(
    db_session: AsyncSession,
) -> None:
    """A finished cycle's stress window ends at its closing entry, not at the
    harvest date it expected and not at today.

    The cycle was sown on Jun 1 and harvested on Aug 10, so the closure entry is
    Aug 10. `expected_harvest_on` says Aug 31, which is **wrong**: a late
    expectation must not extend the window, because the crop was already out of
    the ground on Aug 10. Two stressed days sit inside the closure (Aug 5, Aug
    8) and two sit outside it: Aug 20, still before the expectation, and Sep 1,
    which belongs to the next cycle of the same plot. So the figure is 2.

    Every date is in the past, so `now()` cannot move any of them and the count
    is exactly hand-computable.
    """
    env = await make_env(db_session)
    cycle_id = await add_cycle(
        db_session,
        env,
        sown_on=date(2026, 6, 1),
        status="harvested",
        expected_harvest_on=date(2026, 8, 31),
    )
    await add_logbook_entry(
        db_session,
        env,
        kind="harvest",
        occurred_on=date(2026, 8, 10),
        yield_kg=500,
        crop_cycle_id=cycle_id,
    )
    for day in (date(2026, 8, 5), date(2026, 8, 8), date(2026, 8, 20), date(2026, 9, 1)):
        await add_water_balance(db_session, env, day=day, depletion_mm=60.0, raw_mm=46.2)

    totals = await SqlAlchemyMetricsSourceRepository(db_session).cycle_totals(env.org_id, cycle_id)

    assert totals is not None and totals.water_stress_days == 2


async def test_a_lost_cycle_stops_at_its_loss_observation(
    db_session: AsyncSession,
) -> None:
    """The anchor is the entry that **registers the closure**, and for a lost
    cycle that is the observation carrying an `alert_id` — that observation *is*
    the loss record (docs/03-modelo-datos.md:424, D-T7.2). It is the same anchor
    `metrics_org_month_cycles` dates the cycle with, so the two views cannot
    disagree about when the cycle ended.

    Sown May 1, lost Jul 5, and `expected_harvest_on` (Nov 30) is a third
    unrelated date. Only Jul 2 and Jul 4 are inside the closure.
    """
    env = await make_env(db_session)
    cycle_id = await add_cycle(
        db_session,
        env,
        sown_on=date(2026, 5, 1),
        status="lost",
        expected_harvest_on=date(2026, 11, 30),
    )
    alert_id = await add_alert(db_session, env, rule_code="water_stress")
    await add_logbook_entry(
        db_session,
        env,
        kind="observation",
        occurred_on=date(2026, 7, 5),
        quantity=40,
        alert_id=alert_id,
        crop_cycle_id=cycle_id,
    )
    for day in (date(2026, 7, 2), date(2026, 7, 4), date(2026, 7, 20)):
        await add_water_balance(db_session, env, day=day, depletion_mm=60.0, raw_mm=46.2)

    totals = await SqlAlchemyMetricsSourceRepository(db_session).cycle_totals(env.org_id, cycle_id)

    assert totals is not None and totals.water_stress_days == 2


async def test_a_finished_cycle_with_no_closing_entry_has_no_stress_days(
    db_session: AsyncSession,
) -> None:
    """The missing-evidence case, and the one the finding is really about: a
    finished cycle whose closure nothing registered has **no window**, so the
    figure is `None`.

    `expected_harvest_on` is null — `compute_expected_harvest_on` returns `None`
    for a crop with no FAO-56 stages (yam), and a PATCH may null it because
    `_reject_explicit_null` exempts that field. With the old bound the window fell
    back to `today`, so this cycle kept accruing stressed days indefinitely,
    including days that belong to the next cycle on the same plot. The four
    stressed September days below are exactly that: a cycle sown in April and
    never closed must not claim the September of the cycle that followed it, and
    none of those days is evidence about a cycle nothing closed.

    Every date is frozen in the past, so the assertion holds whatever the day the
    suite runs on: `now()` only ever *widened* the old window, and a frozen day
    inside it is what the old bound counted. There is no live clock here to drift.

    `None`, not 0: "nothing measured it" and "measured it and it was never
    stressed" are different facts (D-T0.3, docs/03-modelo-datos.md:441).
    """
    env = await make_env(db_session)
    cycle_id = await add_cycle(
        db_session, env, sown_on=date(2026, 4, 1), status="harvested", expected_harvest_on=None
    )
    for day in (2, 5, 20, 28):
        await add_water_balance(
            db_session,
            env,
            day=date(2026, 9, day),
            depletion_mm=60.0,
            raw_mm=46.2,
        )

    totals = await SqlAlchemyMetricsSourceRepository(db_session).cycle_totals(env.org_id, cycle_id)

    assert totals is not None and totals.water_stress_days is None


async def test_cycle_totals_measure_an_active_cycle_up_to_today(
    db_session: AsyncSession,
) -> None:
    """Closes `R3-reliability.cycle-totals.now-dependence`: the documented
    active-cycle path, which D-T0.8 makes T4 hit on every read, had no test.

    With no `expected_harvest_on` the window's upper bound falls back to today in
    America/Bogota, so a cycle with no end date is still measured up to now. The
    assertion is a bound rather than an exact count, because `now()` lives in SQL
    and cannot be frozen; what is pinned is that the window reaches today instead
    of stopping at the sowing date or at a null bound.

    The negative: a stressed day after today must not be counted, so the bound is
    doing the work rather than the count being unbounded.
    """
    source = SqlAlchemyMetricsSourceRepository(db_session)
    env = await make_env(db_session)
    today = datetime.now(UTC).astimezone(ZoneInfo("America/Bogota")).date()
    cycle_id = await add_cycle(
        db_session,
        env,
        sown_on=today - timedelta(days=10),
        status="active",
        expected_harvest_on=None,
    )
    for offset in range(3):
        await add_water_balance(
            db_session,
            env,
            day=today - timedelta(days=2) + timedelta(days=offset),
            depletion_mm=60.0,
            raw_mm=46.2,
        )
    # A stressed day in the future is outside the window whatever `now()` is.
    await add_water_balance(
        db_session, env, day=today + timedelta(days=1), depletion_mm=60.0, raw_mm=46.2
    )

    totals = await source.cycle_totals(env.org_id, cycle_id)

    assert totals is not None
    assert totals.water_stress_days == 3
    # The fallback really is what ran: an `active` cycle has no `expected_harvest_on`.
    assert (await db_session.get(CropCycleRow, cycle_id)).expected_harvest_on is None


async def test_cycle_totals_never_count_another_organizations_balance(
    db_session: AsyncSession,
) -> None:
    """docs/09 §Seguridad, closing `R3-reliability.cycle-totals.plot-isolation`.

    The water-balance lateral filters by `wb.plot_id` only and never joins
    `organization`, so the proof that it cannot leak is the plot key itself: a
    cycle belongs to a plot, and a plot belongs to one organization, so another
    organization's balance rows sit under a `plot_id` this cycle can never name.

    Here the foreign plot's balance rows exist and are stressed, and this cycle
    still reads `None`: no evidence reached it.
    """
    source = SqlAlchemyMetricsSourceRepository(db_session)
    env = await make_env(db_session)
    cycle_id = await add_cycle(db_session, env, **_CYCLE)
    foreign = await make_env(db_session, name="Finca Extranjera")
    await add_water_balance(
        db_session, foreign, day=date(2026, 9, 2), depletion_mm=60.0, raw_mm=46.2
    )

    totals = await source.cycle_totals(env.org_id, cycle_id)

    assert totals is not None and totals.water_stress_days is None


async def test_a_partial_sale_cannot_be_written_without_a_price(
    db_session: AsyncSession,
) -> None:
    """Closes `R3-reliability.cycle-totals.null-sale-price`, which read
    `revenue_cop` as able to silently understate a sale recorded without a price.

    It cannot: `ck_logbook_entry_sold_and_price` (docs/03-modelo-datos.md:409)
    requires `sold_kg` and `sale_price_cop_per_kg` together, so the null-
    propagation branch the finding describes is unreachable. Proving the CHECK
    here is what stops the next review from re-raising it.
    """
    env = await make_env(db_session)

    with pytest.raises(IntegrityError):
        await add_logbook_entry(
            db_session,
            env,
            kind="harvest",
            occurred_on=date(2026, 9, 28),
            yield_kg=500,
            sold_kg=300,
        )
