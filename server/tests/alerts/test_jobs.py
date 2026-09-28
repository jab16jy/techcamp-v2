"""The node-health sweep in the worker (docs/06 §3 "Salud del nodo";
docs/10 §3 `m[cada 5 min: salud de nodos]`; D21, ADR-0012).

One job per organization per 5 minutes, then one job per organization that
pages its own nodes. The job coroutines are called directly, never through the
scheduler: what matters is the work the queue is asked to do and the alert it
lands. The rows are leaner than in `test_node_health.py` — no people, no
sensor: the sweep only needs claimed nodes, and the technician of the farm is
resolved by `open_alert` (D4) whether or not a test person is attached.
"""

from __future__ import annotations

import decimal
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.jobs import (
    EVALUATE_ORG_BALANCE_TASK_NAME,
    EVALUATE_ORG_FORECAST_TASK_NAME,
    EVALUATE_ORG_FUNGAL_TASK_NAME,
    EVALUATE_ORG_TASK_NAME,
    QUEUE_NAME,
    evaluate_org_balance_rules,
    evaluate_org_forecast_rules,
    evaluate_org_fungal_risk,
    evaluate_org_node_health,
    local_date,
    sweep_balance_rules,
    sweep_forecast_rules,
    sweep_fungal_risk,
    sweep_node_health,
)
from techcamp.alerts.adapters.orm import AlertRow, AlertRuleRow
from techcamp.farms.adapters.orm import FarmRow, PlotRow, SoilProfileRow
from techcamp.identity.adapters.orm import OrganizationRow
from techcamp.irrigation.adapters.orm import WaterBalanceDailyRow
from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.orm import CalibrationRow, NodeRow, ReadingRow, SensorRow
from techcamp.weather.adapters.orm import WeatherCellRow, WeatherDailyRow


async def test_a_job_day_is_the_products_day_and_not_utc_s() -> None:
    """A forecast rule read on the wrong calendar day warns a farmer about the
    wrong weather: docs/10 §3 fixes every job hour to America/Bogota, and from
    19:00 to 23:59 local the UTC date is already tomorrow's."""
    # 01:00 UTC is 20:00 of the PREVIOUS day in Bogota (UTC-5).
    late_local_evening = datetime(2026, 9, 27, 1, 0, tzinfo=UTC)

    assert local_date(late_local_evening) == date(2026, 9, 26)
    # The contrast is the point: the UTC date, which is what the job used, is a
    # different calendar day for five hours of every local evening.
    assert late_local_evening.date() == date(2026, 9, 27)
    # Midday agrees on both, so the helper changes nothing outside that window.
    midday = datetime(2026, 9, 27, 16, 0, tzinfo=UTC)
    assert local_date(midday) == midday.date() == date(2026, 9, 27)


pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)
_SILENT = timedelta(hours=2)  # past 3 × `interval_s` of 300 s
_RECENT = timedelta(minutes=1)


@pytest.fixture(autouse=True)
async def _clear_jobs(db_session: AsyncSession):
    """`procrastinate_jobs` is keyed by org id and `db_session`'s own cleanup
    truncates only the organization tables, so a `todo` job left by one test
    would make the next test's per-org defer a no-op (the `queueing_lock`
    refuses a duplicate) and hide a broken fan-out."""
    yield
    await db_session.execute(text("DELETE FROM procrastinate_jobs"))
    await db_session.commit()


@dataclass(frozen=True, slots=True)
class Org:
    org_id: UUID
    farm_id: UUID
    plot_id: UUID


async def _make_org(db_session: AsyncSession) -> Org:
    """One organization with a farm and a plot, the two a claimed node needs."""
    org_id, farm_id, plot_id = uuid7(), uuid7(), uuid7()
    db_session.add(OrganizationRow(id=org_id, name="Test Org", kind="individual"))
    await db_session.commit()
    db_session.add(
        FarmRow(
            id=farm_id,
            org_id=org_id,
            name="Finca Principal",
            municipality_code="47001",
            location=_POINT,
        )
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
    await db_session.commit()
    return Org(org_id, farm_id, plot_id)


async def _add_node(db_session: AsyncSession, org: Org, *, last_seen_at: datetime) -> UUID:
    """A node claimed onto that org's plot (`ck_node_ownership_all_or_nothing`
    makes org, plot and `claimed_at` all or nothing)."""
    node_id = uuid7()
    db_session.add(
        NodeRow(
            id=node_id,
            org_id=org.org_id,
            plot_id=org.plot_id,
            transport="wifi",
            claim_code=f"claim-{uuid7().hex}",
            credential_hash="hash",
            interval_s=300,
            claimed_at=last_seen_at - timedelta(days=1),
            last_seen_at=last_seen_at,
            status="online",
        )
    )
    await db_session.commit()
    return node_id


async def _jobs(db_session: AsyncSession) -> list[Any]:
    return (
        await db_session.execute(
            text(
                "SELECT args, lock, queueing_lock, status, queue_name, task_name "
                "FROM procrastinate_jobs ORDER BY id"
            )
        )
    ).all()


async def _node_alerts(db_session: AsyncSession, org_id: UUID) -> list[tuple[str, str]]:
    """`(rule_code, state)` of the org's node alerts."""
    rows = await db_session.execute(
        select(AlertRuleRow.code, AlertRow.state)
        .join(AlertRuleRow, AlertRuleRow.id == AlertRow.rule_id)
        .where(AlertRow.org_id == org_id)
    )
    return [(code, state) for code, state in rows]


async def test_the_five_minute_sweep_defers_one_job_per_org_with_its_locks(
    db_session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    first_org = await _make_org(db_session)
    second_org = await _make_org(db_session)
    await _add_node(db_session, first_org, last_seen_at=now - _SILENT)
    await _add_node(db_session, second_org, last_seen_at=now - _SILENT)
    # An organization with no claimed node has no node to judge, so it must not
    # cost a job: the sweep reads the orgs of the nodes, not the orgs.
    empty = uuid7()
    db_session.add(OrganizationRow(id=empty, name="Empty Org", kind="individual"))
    await db_session.commit()

    await sweep_node_health(timestamp=0)

    jobs = await _jobs(db_session)
    assert {job.args["org_id"] for job in jobs} == {
        str(first_org.org_id),
        str(second_org.org_id),
    }
    assert {job.task_name for job in jobs} == {EVALUATE_ORG_TASK_NAME}
    assert all(job.queue_name == QUEUE_NAME and job.status == "todo" for job in jobs)
    # One organization never sweeps twice at once: the per-org lock serializes
    # its jobs and the queueing lock refuses a duplicate while one is `todo`.
    assert {job.lock for job in jobs} == {
        f"alerts:org:{first_org.org_id}",
        f"alerts:org:{second_org.org_id}",
    }
    assert {job.queueing_lock for job in jobs} == {job.lock for job in jobs}
    assert str(empty) not in {job.args["org_id"] for job in jobs}


async def test_the_org_job_opens_the_alert_of_a_silent_node_and_skips_a_recent_one(
    db_session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    org = await _make_org(db_session)
    await _add_node(db_session, org, last_seen_at=now - _SILENT)
    # The same page covers both nodes, so the negative case is a node of the
    # same org that was heard from a minute ago: it must open nothing.
    await _add_node(db_session, org, last_seen_at=now - _RECENT)

    await evaluate_org_node_health(org_id=str(org.org_id))

    alerts = await _node_alerts(db_session, org.org_id)
    assert len(alerts) == 1
    assert alerts[0] == ("node_offline", "open")


# -- the forecast and daily rules on their own periodics (D10, D20, D23) --


async def test_the_weather_sweeps_defer_one_job_per_org_with_their_own_locks(
    db_session: AsyncSession,
) -> None:
    """D23 fixes the two hours docs/10 §3 does not name: the forecast rules ten
    minutes after each 3 h refresh, `fungal_risk` after the 04:30 balance and
    before the 05:00 morning push. Each sweep reads the orgs that have PLOTS (a
    weather rule is about a plot) and defers one job per org, on its own lock so
    the two sources never wait on each other."""
    first = await _make_org(db_session)
    second = await _make_org(db_session)
    # An organization with no plot has nothing a weather rule could be decided
    # for, so it must not cost a job.
    empty = uuid7()
    db_session.add(OrganizationRow(id=empty, name="Empty Org", kind="individual"))
    await db_session.commit()

    await sweep_forecast_rules(timestamp=0)
    await sweep_fungal_risk(timestamp=0)

    jobs = await _jobs(db_session)
    expected = {str(first.org_id), str(second.org_id)}
    assert len(jobs) == 4
    assert {job.args["org_id"] for job in jobs} == expected
    assert {job.task_name for job in jobs} == {
        EVALUATE_ORG_FORECAST_TASK_NAME,
        EVALUATE_ORG_FUNGAL_TASK_NAME,
    }
    assert all(job.queue_name == QUEUE_NAME and job.status == "todo" for job in jobs)
    assert {job.lock for job in jobs} == {
        f"alerts:org:{org_id}:{source}" for org_id in expected for source in ("forecast", "fungal")
    }
    assert {job.queueing_lock for job in jobs} == {job.lock for job in jobs}
    assert str(empty) not in {job.args["org_id"] for job in jobs}


async def test_the_forecast_job_reads_the_forecast_day_and_the_daily_job_the_cell_day(
    db_session: AsyncSession,
) -> None:
    """Each job decides the day its own rule is about, read from the run's own
    clock: the forecast rule the next forecast day, `fungal_risk` the cell-day
    the 03:00 consolidation just closed. A row for any other day is ignored.

    Both days come from `local_date`, the SAME helper the jobs read the day with
    (docs/10 §3 fixes every job hour to America/Bogota), so the fixture stores
    the row the job will look for. Derived from the UTC date instead, this test
    asked for the wrong day for the five hours a day in which the two disagree —
    19:00 to 23:59 local — and failed only when it was run inside that window."""
    org = await _make_org(db_session)
    cell_id = await _cell(db_session)
    await _point_plot_at_cell(db_session, org, cell_id)
    now = datetime.now(UTC)
    today = local_date(now)
    forecast_day = today + timedelta(days=1)
    cell_day = today - timedelta(days=1)
    _store_weather(db_session, cell_id, forecast_day, True, rain_mm=62.0, fetched_at=now)
    _store_weather(
        db_session,
        cell_id,
        cell_day,
        False,
        rh_mean_pct=90.0,
        tmin_c=21.0,
        tmax_c=25.0,
        fetched_at=now,
    )
    await _commit(db_session)

    await evaluate_org_forecast_rules(org_id=str(org.org_id))
    await evaluate_org_fungal_risk(org_id=str(org.org_id))

    assert sorted(await _plot_alerts(db_session, org.org_id)) == [
        ("fungal_risk", "open"),
        ("heavy_rain_forecast", "open"),
    ]


async def _cell(db_session: AsyncSession) -> int:
    """The one 0.1° cell the org's plot falls into (D23: a cell is shared)."""
    row = WeatherCellRow(lat=10.9, lon=-74.1)
    db_session.add(row)
    await db_session.commit()
    return row.id


async def _point_plot_at_cell(db_session: AsyncSession, org: Org, cell_id: int) -> None:
    db_session.add(
        PlotRow(
            id=uuid7(),
            org_id=org.org_id,
            farm_id=org.farm_id,
            name="Lote con celda",
            boundary=_BOUNDARY,
            irrigation_system="drip",
            weather_cell_id=cell_id,
        )
    )
    await db_session.commit()


def _store_weather(
    db_session: AsyncSession,
    cell_id: int,
    day: date,
    is_forecast: bool,
    *,
    fetched_at: datetime,
    rain_mm: float | None = None,
    rh_mean_pct: float | None = None,
    tmin_c: float | None = None,
    tmax_c: float | None = None,
) -> None:
    db_session.add(
        WeatherDailyRow(
            cell_id=cell_id,
            day=day,
            is_forecast=is_forecast,
            et0_mm=4.2,
            rain_mm=rain_mm,
            tmin_c=tmin_c,
            tmax_c=tmax_c,
            rh_mean_pct=rh_mean_pct,
            fetched_at=fetched_at,
        )
    )


async def _commit(db_session: AsyncSession) -> None:
    await db_session.commit()


async def _plot_alerts(db_session: AsyncSession, org_id: UUID) -> list[tuple[str, str]]:
    """`(rule_code, state)` of the org's plot alerts."""
    rows = await db_session.execute(
        select(AlertRuleRow.code, AlertRow.state)
        .join(AlertRow, AlertRow.rule_id == AlertRuleRow.id)
        .where(AlertRow.org_id == org_id)
    )
    return [(code, state) for code, state in rows]


# -- the balance branch of water_stress on its own periodic (D28, Q2) --


async def test_the_balance_sweep_defers_one_job_per_org_with_its_own_lock(
    db_session: AsyncSession,
) -> None:
    """D28 fixes the hour docs/10 §3 does not name: 04:50, after the 04:30 balance
    and before the 05:00 morning push. It reads the orgs that have PLOTS and
    defers one job per org on the `balance` lock, so it never waits on the
    forecast or fungal sweeps of the same organization."""
    first = await _make_org(db_session)
    second = await _make_org(db_session)

    await sweep_balance_rules(timestamp=0)

    jobs = await _jobs(db_session)
    assert len(jobs) == 2
    assert {job.task_name for job in jobs} == {EVALUATE_ORG_BALANCE_TASK_NAME}
    assert {job.args["org_id"] for job in jobs} == {str(first.org_id), str(second.org_id)}
    assert {job.lock for job in jobs} == {
        f"alerts:org:{org_id}:balance" for org_id in (str(first.org_id), str(second.org_id))
    }


async def test_the_balance_job_opens_the_stress_of_a_plot_with_no_representative_sensor(
    db_session: AsyncSession,
) -> None:
    """The job end to end: it reads the balance row for D−1 in the PRODUCT's day
    (docs/10 §3) and opens the warning on `Dr > RAW` (ADR-0022).

    The first plot carries no sensor at all, so no representative sensor exists
    and the balance owns the plot (D29). The SECOND plot of the SAME organization
    has one, so however deep its depletion this job must open nothing for it: the
    reading rule owns that plot, and one job deciding both ways would be two
    sources on one alert."""
    org = await _make_org(db_session)
    now = datetime.now(UTC)
    today = local_date(now)
    # The 04:30 run for D wrote the row for D−1.
    _store_balance(db_session, org.plot_id, today - timedelta(days=1), depletion_mm=60.0)
    with_sensor = await _add_plot_to_farm(db_session, org)
    _store_balance(db_session, with_sensor, today - timedelta(days=1), depletion_mm=60.0)
    await _add_representative_sensor(db_session, org, plot_id=with_sensor)
    await _commit(db_session)

    await evaluate_org_balance_rules(org_id=str(org.org_id))

    assert await _plot_alerts(db_session, org.org_id) == [("water_stress", "open")]


async def _add_plot_to_farm(db_session: AsyncSession, org: Org) -> UUID:
    """A second plot of the same farm (the same org, so the same job decides it)."""
    plot_id = uuid7()
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org.org_id,
            farm_id=org.farm_id,
            name="Lote 2",
            boundary=_BOUNDARY,
            irrigation_system="drip",
        )
    )
    await db_session.flush()
    return plot_id


def _store_balance(
    db_session: AsyncSession, plot_id: UUID, day: date, *, depletion_mm: float
) -> None:
    """One `water_balance_daily` row, as the 04:30 irrigation job writes it."""
    db_session.add(
        WaterBalanceDailyRow(
            plot_id=plot_id,
            day=day,
            etc_mm=decimal.Decimal("5.0"),
            effective_rain_mm=decimal.Decimal("0.0"),
            irrigation_mm=decimal.Decimal("0.0"),
            taw_mm=decimal.Decimal("100.0"),
            raw_mm=decimal.Decimal("50.0"),
            depletion_model_mm=decimal.Decimal(str(depletion_mm)),
            depletion_mm=decimal.Decimal(str(depletion_mm)),
            soil_moisture_obs_pct=None,
            assimilation_k=decimal.Decimal("0"),
            stress_moisture_pct=decimal.Decimal("15.3"),
        )
    )


async def _add_representative_sensor(db_session: AsyncSession, org: Org, *, plot_id: UUID) -> None:
    """A `field`-calibrated soil-moisture sensor at Zr/2 of a 100 cm root zone
    with a valid reading in the last 24 h: the plot's representative sensor
    (docs/06 §5, ADR-0022)."""
    now = datetime.now(UTC)
    node_id = uuid7()
    db_session.add(
        NodeRow(
            id=node_id,
            org_id=org.org_id,
            plot_id=plot_id,
            transport="wifi",
            claim_code=f"claim-{uuid7().hex}",
            credential_hash="hash",
            interval_s=300,
            claimed_at=now - timedelta(days=30),
            status="online",
        )
    )
    sensor = SensorRow(
        node_id=node_id,
        channel_key="soil_moisture_0",
        metric="soil_moisture",
        unit="pct",
        depth_cm=50,
    )
    db_session.add(sensor)
    db_session.add(
        SoilProfileRow(
            plot_id=plot_id,
            source="lab",
            field_capacity_pct=23.0,
            wilting_point_pct=9.0,
            root_depth_cm=100.0,
        )
    )
    await db_session.flush()
    db_session.add(
        CalibrationRow(
            id=uuid7(),
            sensor_id=sensor.id,
            version=1,
            method="linear",
            kind="field",
            params={"scale": 1.0, "offset": 0.0},
            valid_from=now - timedelta(days=30),
        )
    )
    db_session.add(
        ReadingRow(
            time=now - timedelta(hours=2),
            sensor_id=sensor.id,
            raw_value=18.0,
            value=18.0,
            received_at=now - timedelta(hours=2),
            quality=0,
        )
    )
    await db_session.flush()
