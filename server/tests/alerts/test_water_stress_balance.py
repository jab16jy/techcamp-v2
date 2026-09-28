"""The balance branch of `water_stress`: `Dr > RAW` on the daily balance of a
plot WITHOUT a representative sensor (docs/06 §3 "Balance hídrico" and §5,
ADR-0022; Q2, D25, D27, D28, D29).

Real Postgres, no doubles: the job composes the evaluator exactly as
`alerts/adapters/jobs.py` does, and what matters is the alert the stored
`water_balance_daily` rows produce.
"""

from __future__ import annotations

import decimal
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.orm import AlertRow, AlertRuleRow
from techcamp.alerts.adapters.repositories import (
    SqlAlchemyAlertRepository,
    SqlAlchemyAlertRuleRepository,
)
from techcamp.alerts.domain import AlertState, Severity
from techcamp.farms.adapters.orm import FarmRow, PlotRow, SoilProfileRow
from techcamp.farms.adapters.repositories import (
    SqlAlchemyFarmRepository,
    SqlAlchemyPlotRepository,
    SqlAlchemySoilProfileRepository,
)
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.domain.models import Role
from techcamp.irrigation.adapters.orm import WaterBalanceDailyRow
from techcamp.irrigation.adapters.repositories import SqlAlchemyWaterBalanceRepository
from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.orm import CalibrationRow, NodeRow, ReadingRow, SensorRow
from techcamp.telemetry.adapters.repositories import (
    SqlAlchemyCalibrationRepository,
    SqlAlchemyNodeRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemySensorRepository,
)

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)
"""America/Bogota 05:00 on 2026-09-26: the 04:50 job, so the newest balance day
is 2026-09-25 (the run for D writes the row for D−1, docs/06 §5)."""
_AT = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
_DAY = date(2026, 9, 26)
_ROOT_DEPTH_CM = 100.0


@dataclass(frozen=True, slots=True)
class Plot:
    org_id: UUID
    farm_id: UUID
    plot_id: UUID


async def _make_plot(
    db_session: AsyncSession,
    *,
    depth_cm: int | None = None,
    calibration_kind: str | None = None,
) -> Plot:
    org_id, farm_id, plot_id, node_id = uuid7(), uuid7(), uuid7(), uuid7()
    user_id = uuid7()
    db_session.add(
        AppUserRow(id=user_id, phone=f"+57{uuid7().int % 10**13:013d}", full_name="Producer")
    )
    db_session.add(
        OrganizationRow(id=org_id, name="Org", kind="individual"),
        MembershipRow(user_id=user_id, org_id=org_id, role=Role.OWNER),
    )
    await db_session.flush()
    db_session.add(
        FarmRow(id=farm_id, org_id=org_id, name="Farm", municipality_code="47001", location=_POINT)
    )
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=_BOUNDARY,
            irrigation_system="drip",
        )
    )
    db_session.add(
        SoilProfileRow(
            plot_id=plot_id,
            source="lab",
            field_capacity_pct=23.0,
            wilting_point_pct=9.0,
            root_depth_cm=_ROOT_DEPTH_CM,
        )
    )
    if depth_cm is not None:
        db_session.add(
            NodeRow(
                id=node_id,
                org_id=org_id,
                plot_id=plot_id,
                transport="wifi",
                claim_code=f"claim-{uuid7().hex}",
                credential_hash="hash",
                interval_s=300,
                claimed_at=_AT - timedelta(days=30),
                status="online",
            )
        )
        sensor = SensorRow(
            node_id=node_id,
            channel_key="soil_moisture_0",
            metric="soil_moisture",
            unit="pct",
            depth_cm=depth_cm,
        )
        db_session.add(sensor)
        await db_session.flush()
        if calibration_kind is not None:
            db_session.add(
                CalibrationRow(
                    id=uuid7(),
                    sensor_id=sensor.id,
                    version=1,
                    method="linear",
                    kind=calibration_kind,
                    params={"scale": 1.0, "offset": 0.0},
                    valid_from=_AT - timedelta(days=30),
                )
            )
            await db_session.flush()
            # docs/06 §5: the representative sensor also needs a valid reading in
            # the last 24 h, so a sensor with a calibration and no reading is not
            # one (`K = 0`, "sin sensor").
            for offset in range(0, 24, 6):
                db_session.add(
                    ReadingRow(
                        time=_AT - timedelta(hours=offset),
                        sensor_id=sensor.id,
                        raw_value=18.0,
                        value=18.0,
                        received_at=_AT - timedelta(hours=offset),
                        quality=0,
                    )
                )
    await db_session.commit()
    return Plot(org_id, farm_id, plot_id)


async def _store_balances(
    db_session: AsyncSession,
    plot: Plot,
    *,
    days: list[date],
    depletion_mm: float,
    raw_mm: float = 50.0,
) -> None:
    """One `water_balance_daily` row per day, as the 04:30 job writes them.

    A day already stored is overwritten, so a test can grow the series day by day
    the way the job does (`ON CONFLICT (plot_id, day)`).
    """
    for day in days:
        stored = (
            await db_session.execute(
                select(WaterBalanceDailyRow).where(
                    WaterBalanceDailyRow.plot_id == plot.plot_id,
                    WaterBalanceDailyRow.day == day,
                )
            )
        ).scalar_one_or_none()
        if stored is not None:
            stored.depletion_mm = decimal.Decimal(str(depletion_mm))
            stored.depletion_model_mm = decimal.Decimal(str(depletion_mm))
            stored.raw_mm = decimal.Decimal(str(raw_mm))
            continue
        db_session.add(
            WaterBalanceDailyRow(
                plot_id=plot.plot_id,
                day=day,
                etc_mm=decimal.Decimal("5.0"),
                effective_rain_mm=decimal.Decimal("0.0"),
                irrigation_mm=decimal.Decimal("0.0"),
                taw_mm=decimal.Decimal("100.0"),
                raw_mm=decimal.Decimal(str(raw_mm)),
                depletion_model_mm=decimal.Decimal(str(depletion_mm)),
                depletion_mm=decimal.Decimal(str(depletion_mm)),
                soil_moisture_obs_pct=None,
                assimilation_k=decimal.Decimal("0"),
                stress_moisture_pct=decimal.Decimal("15.3"),
            )
        )
    await db_session.commit()


def _days(count: int) -> list[date]:
    """The `count` local days that ended on D−1, newest last."""
    return [_DAY - timedelta(days=offset) for offset in range(count, 0, -1)]


def _day(offset: int) -> date:
    """The local day a run `offset` days after the baseline decided."""
    return _DAY + timedelta(days=offset)


def _at(offset: int) -> datetime:
    """The `at` of the run that decided `_day(offset)`: 05:00 America/Bogota."""
    return _AT + timedelta(days=offset)


async def _evaluate(
    db_session: AsyncSession, plot: Plot, *, at: datetime = _AT, day: date = _DAY
) -> None:
    from techcamp.alerts.application import evaluate_balance_rules

    await evaluate_balance_rules(
        org_id=plot.org_id,
        at=at,
        day=day,
        rules=SqlAlchemyAlertRuleRepository(db_session),
        farms=SqlAlchemyFarmRepository(db_session),
        plots=SqlAlchemyPlotRepository(db_session),
        soils=SqlAlchemySoilProfileRepository(db_session),
        balances=SqlAlchemyWaterBalanceRepository(db_session),
        nodes=SqlAlchemyNodeRepository(db_session),
        sensors=SqlAlchemySensorRepository(db_session),
        calibrations=SqlAlchemyCalibrationRepository(db_session),
        readings=SqlAlchemyReadingRepository(db_session),
        alerts=SqlAlchemyAlertRepository(db_session),
    )
    await db_session.commit()


async def _alerts(db_session: AsyncSession, plot: Plot) -> list[tuple[str, str, str]]:
    result = await db_session.execute(
        select(AlertRuleRow.code, AlertRow.state, AlertRow.severity)
        .join(AlertRow, AlertRow.rule_id == AlertRuleRow.id)
        .where(AlertRow.plot_id == plot.plot_id)
    )
    return [(code, state, severity) for code, state, severity in result]


# -- Dr > RAW opens, Dr <= RAW does not (docs/06 §3, ADR-0022) --


async def test_a_balance_over_raw_opens_water_stress_without_a_representative_sensor(
    db_session: AsyncSession,
) -> None:
    """The documented branch: no representative sensor, so the daily balance owns
    the plot (D29) and `Dr = 60 mm > RAW = 50 mm` opens the alert as a warning."""
    plot = await _make_plot(db_session)
    await _store_balances(db_session, plot, days=_days(1), depletion_mm=60.0)

    await _evaluate(db_session, plot)

    assert await _alerts(db_session, plot) == [("water_stress", AlertState.OPEN, Severity.WARNING)]


async def test_a_balance_at_or_below_raw_opens_nothing(db_session: AsyncSession) -> None:
    """Stress starts ABOVE RAW (ADR-0022, docs/04:75), so `Dr = RAW` is not
    stress, and neither is a depletion below it."""
    plot = await _make_plot(db_session)
    await _store_balances(db_session, plot, days=_days(1), depletion_mm=50.0)

    await _evaluate(db_session, plot)

    assert await _alerts(db_session, plot) == []


async def test_a_plot_without_a_balance_row_is_not_decided(db_session: AsyncSession) -> None:
    """The 04:30 job skips a plot with incomplete soil data (docs/06 §5), so
    there is no row to decide on and no θ_estrés either."""
    plot = await _make_plot(db_session)

    await _evaluate(db_session, plot)

    assert await _alerts(db_session, plot) == []


async def test_a_plot_with_a_representative_sensor_is_left_to_the_reading_rule(
    db_session: AsyncSession,
) -> None:
    """D29: exactly one branch decides a plot. A `field`-calibrated sensor at Zr/2
    of a 100 cm root zone is the representative one, so this evaluator opens
    nothing even with `Dr > RAW`: the reading rule owns the plot."""
    plot = await _make_plot(db_session, depth_cm=50, calibration_kind="field")
    await _store_balances(db_session, plot, days=_days(1), depletion_mm=60.0)

    await _evaluate(db_session, plot)

    assert await _alerts(db_session, plot) == []


async def test_a_plot_with_a_lab_calibrated_sensor_still_gets_the_balance_branch(
    db_session: AsyncSession,
) -> None:
    """ADR-0022: a `lab` calibration is `K = 0` (5,5–19 moisture points of error),
    so the sensor is not the representative one and the balance decides — the
    switch is the sensor, not the assimilation (D29)."""
    plot = await _make_plot(db_session, depth_cm=50, calibration_kind="lab")
    await _store_balances(db_session, plot, days=_days(1), depletion_mm=60.0)

    await _evaluate(db_session, plot)

    assert await _alerts(db_session, plot) == [("water_stress", AlertState.OPEN, Severity.WARNING)]


async def test_a_degenerate_soil_with_no_raw_opens_nothing(db_session: AsyncSession) -> None:
    """E6's D5: `RAW <= 0` has no positive threshold to cross, so there is no
    stress to report however deep the depletion is."""
    plot = await _make_plot(db_session)
    await _store_balances(db_session, plot, days=_days(1), depletion_mm=60.0, raw_mm=0.0)

    await _evaluate(db_session, plot)

    assert await _alerts(db_session, plot) == []


# -- the 48 h critical upgrade on daily evidence (docs/06 §3, D27) --


async def test_a_stressed_balance_stays_a_warning_for_two_days_and_upgrades_on_the_third(
    db_session: AsyncSession,
) -> None:
    """docs/06 §3: "crítica si dura 48 h". Three consecutive days of stress, each
    decided by its own 04:50 run: the first opens the alert, the second is still
    24 h old, and the third reaches 48 h, which upgrades it (D27: on a daily series
    48 h is only reached at the third stressed balance)."""
    plot = await _make_plot(db_session)

    # Each run decides the row for D−1, whose local day closed at 00:00 of D.
    await _store_balances(db_session, plot, days=[_day(-4)], depletion_mm=60.0)
    await _evaluate(db_session, plot, at=_at(-3), day=_day(-3))
    assert [severity for _, _, severity in await _alerts(db_session, plot)] == [Severity.WARNING]

    await _store_balances(db_session, plot, days=[_day(-3)], depletion_mm=60.0)
    await _evaluate(db_session, plot, at=_at(-2), day=_day(-2))
    assert [severity for _, _, severity in await _alerts(db_session, plot)] == [Severity.WARNING]

    await _store_balances(db_session, plot, days=[_day(-2)], depletion_mm=60.0)
    await _evaluate(db_session, plot, at=_at(-1), day=_day(-1))
    assert [severity for _, _, severity in await _alerts(db_session, plot)] == [Severity.CRITICAL]


# -- resolution on the daily series (D2's 60-minute run, D27) --


async def test_an_open_alert_resolves_on_the_second_clear_balance(
    db_session: AsyncSession,
) -> None:
    """A single clear balance is a zero-length clear run, which the 60-minute
    resolution window cannot accept, so the alert resolves with the second
    consecutive clear day (D27, D22's reasoning on daily evidence)."""
    plot = await _make_plot(db_session)
    await _store_balances(
        db_session,
        plot,
        days=[date(2026, 9, 23), date(2026, 9, 24), date(2026, 9, 25)],
        depletion_mm=60.0,
    )
    await _evaluate(db_session, plot)
    assert [state for _, state, _ in await _alerts(db_session, plot)] == [AlertState.OPEN]

    # One clear balance: a single clear sample is a zero-length clear run.
    await _store_balances(db_session, plot, days=[date(2026, 9, 25)], depletion_mm=10.0)
    await _evaluate(db_session, plot, at=_at(1), day=_day(1))
    assert [state for _, state, _ in await _alerts(db_session, plot)] == [AlertState.OPEN]

    # The second consecutive clear balance is a 24 h clear run, past the 60 min
    # the window asks for.
    await _store_balances(
        db_session, plot, days=[date(2026, 9, 24), date(2026, 9, 25)], depletion_mm=10.0
    )
    await _evaluate(db_session, plot, at=_at(2), day=_day(2))
    assert [state for _, state, _ in await _alerts(db_session, plot)] == [AlertState.RESOLVED]


async def test_a_stressed_balance_of_a_resolved_alert_opens_a_new_one(
    db_session: AsyncSession,
) -> None:
    """The partial unique index only holds a NON-resolved alert per (rule, plot),
    so a new stress episode after a resolution opens a new alert rather than
    reviving the resolved one."""
    plot = await _make_plot(db_session)
    await _store_balances(db_session, plot, days=_days(2), depletion_mm=10.0)
    await _evaluate(db_session, plot)
    assert await _alerts(db_session, plot) == []

    await _store_balances(db_session, plot, days=_days(2), depletion_mm=60.0)
    await _evaluate(db_session, plot)
    assert [state for _, state, _ in await _alerts(db_session, plot)] == [AlertState.OPEN]


# -- org isolation (docs/09) --


async def test_only_the_job_own_organization_is_decided(db_session: AsyncSession) -> None:
    """`org_id` is a parameter, not a filter a caller can leave out, and every
    read below keeps it (D21, docs/09 org isolation)."""
    mine = await _make_plot(db_session)
    other = await _make_plot(db_session)
    await _store_balances(db_session, mine, days=_days(1), depletion_mm=60.0)
    await _store_balances(db_session, other, days=_days(1), depletion_mm=60.0)

    await _evaluate(db_session, mine)

    assert len(await _alerts(db_session, mine)) == 1
    assert await _alerts(db_session, other) == []
