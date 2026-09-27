"""Tests for irrigation daily job and dev trigger (E6 T4, docs/06 §5, docs/04-api.md:181).

ADR-0012, ADR-0021. Tests against real Postgres with TDD:
- Fan-out queues one job per eligible plot and none for an irrigated plot without a cycle
- Re-running the fan-out for the same day does not duplicate jobs
- Per-plot task persists balance and recommendation and commits
- Skipped plot logs and stores nothing
- Dev route returns job_id, defaults day to local today, rejects future days with 422
- Dev route is absent in production profile
"""

from __future__ import annotations

import datetime
import importlib
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import CropCycleRow, FarmRow, PlotRow, SoilProfileRow
from techcamp.farms.adapters.repositories import (
    SqlAlchemyCropRepository,
    SqlAlchemyWeatherRepository,
)
from techcamp.farms.domain.models import CropCycleStatus
from techcamp.identity.adapters.orm import OrganizationRow
from techcamp.irrigation.adapters.jobs import (
    QUEUE_NAME,
    RUN_DAILY_PLOTS_TASK_NAME,
    RUN_PLOT_BALANCE_TASK_NAME,
    local_today,
    run_daily_plots,
    run_plot_balance,
)
from techcamp.irrigation.adapters.repositories import (
    SqlAlchemyIrrigationRecommendationRepository,
    SqlAlchemyWaterBalanceRepository,
)
from techcamp.main import app
from techcamp.shared.ids import uuid7
from techcamp.weather.adapters.orm import WeatherDailyRow

pytestmark = pytest.mark.anyio

_ROUTE = "/dev/jobs/irrigation:run"
_REGISTERED_PATH = "/api/v1/dev/jobs/irrigation:run"
_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)


@pytest.fixture(autouse=True)
async def _clear_jobs(db_session: AsyncSession):
    """Clean up procrastinate_jobs between test runs."""
    yield
    await db_session.execute(text("DELETE FROM procrastinate_jobs"))
    await db_session.commit()


async def _make_org_and_farm(db_session: AsyncSession) -> tuple[UUID, UUID, int]:
    org_id = uuid7()
    db_session.add(OrganizationRow(id=org_id, name="Test Org", kind="individual"))
    await db_session.commit()

    farm_id = uuid7()
    db_session.add(
        FarmRow(
            id=farm_id,
            org_id=org_id,
            name="Test Farm",
            municipality_code="47001",
            location=_POINT,
        )
    )
    await db_session.commit()

    cell_repo = SqlAlchemyWeatherRepository(db_session)
    cell_id = await cell_repo.get_or_create_cell(10.9, -74.1)
    return org_id, farm_id, cell_id


async def _create_plot(
    db_session: AsyncSession,
    *,
    org_id: UUID,
    farm_id: UUID,
    cell_id: int,
    name: str,
    irrigation_system: str,
    has_active_cycle: bool = False,
) -> UUID:
    plot_id = uuid7()
    is_irrigated = irrigation_system != "none"
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name=name,
            boundary=_BOUNDARY,
            weather_cell_id=cell_id,
            irrigation_system=irrigation_system,
            irrigation_efficiency=Decimal("0.90") if is_irrigated else None,
            system_flow_lph=Decimal("1000.0") if is_irrigated else None,
        )
    )
    await db_session.commit()

    if has_active_cycle:
        crops = await SqlAlchemyCropRepository(db_session).list_all()
        crop = next(c for c in crops if c.code == "maize")
        cycle_id = uuid7()
        db_session.add(
            CropCycleRow(
                id=cycle_id,
                plot_id=plot_id,
                crop_id=crop.id,
                sown_on=local_today() - datetime.timedelta(days=30),
                status=CropCycleStatus.ACTIVE.value,
            )
        )
        await db_session.commit()

    return plot_id


async def _queued_jobs(db_session: AsyncSession) -> list[dict[str, object]]:
    result = await db_session.execute(
        text(
            "SELECT id, queue_name, task_name, lock, queueing_lock, args, status "
            "FROM procrastinate_jobs WHERE queue_name = :queue ORDER BY id"
        ),
        {"queue": QUEUE_NAME},
    )
    return [dict(row) for row in result.mappings().all()]


async def test_fanout_queues_one_job_per_eligible_plot_and_none_for_irrigated_without_cycle(
    db_session: AsyncSession,
) -> None:
    """The 04:30 daily fan-out queues one per-plot job for eligible plots:

    plots with an active cycle plus rainfed plots without one.
    An irrigated plot without an active cycle must NOT be queued.
    """
    org_id, farm_id, cell_id = await _make_org_and_farm(db_session)

    # 1. Irrigated plot with active cycle -> eligible
    plot_irrigated_with_cycle = await _create_plot(
        db_session,
        org_id=org_id,
        farm_id=farm_id,
        cell_id=cell_id,
        name="Irrigated with cycle",
        irrigation_system="drip",
        has_active_cycle=True,
    )

    # 2. Rainfed plot without cycle -> eligible (sowing advice)
    plot_rainfed_no_cycle = await _create_plot(
        db_session,
        org_id=org_id,
        farm_id=farm_id,
        cell_id=cell_id,
        name="Rainfed without cycle",
        irrigation_system="none",
        has_active_cycle=False,
    )

    # 3. Irrigated plot without active cycle -> NOT eligible
    plot_irrigated_no_cycle = await _create_plot(
        db_session,
        org_id=org_id,
        farm_id=farm_id,
        cell_id=cell_id,
        name="Irrigated without cycle",
        irrigation_system="drip",
        has_active_cycle=False,
    )

    await run_daily_plots(timestamp=0)

    jobs = await _queued_jobs(db_session)
    queued_plot_ids = {str(job["args"]["plot_id"]) for job in jobs}

    assert str(plot_irrigated_with_cycle) in queued_plot_ids
    assert str(plot_rainfed_no_cycle) in queued_plot_ids
    assert str(plot_irrigated_no_cycle) not in queued_plot_ids
    assert len(jobs) == 2

    today_str = local_today().isoformat()
    for job in jobs:
        assert job["queue_name"] == QUEUE_NAME
        assert job["task_name"] == RUN_PLOT_BALANCE_TASK_NAME
        assert job["status"] == "todo"
        assert job["lock"] == f"irrigation:plot:{job['args']['plot_id']}"
        assert job["queueing_lock"] == f"irrigation:plot:{job['args']['plot_id']}:{today_str}"
        assert job["args"]["day"] == today_str


async def test_fanout_rerun_same_day_does_not_duplicate_jobs(
    db_session: AsyncSession,
) -> None:
    """Re-running the daily fan-out for the same day must deduplicate jobs:

    a plot that already has a 'todo' job for day D is not enqueued a second time.
    """
    org_id, farm_id, cell_id = await _make_org_and_farm(db_session)
    await _create_plot(
        db_session,
        org_id=org_id,
        farm_id=farm_id,
        cell_id=cell_id,
        name="Eligible plot",
        irrigation_system="none",
        has_active_cycle=False,
    )

    # First run queues 1 job
    await run_daily_plots(timestamp=0)
    jobs_first = await _queued_jobs(db_session)
    assert len(jobs_first) == 1

    # Second run for the same day (today) should not duplicate
    await run_daily_plots(timestamp=0)
    jobs_second = await _queued_jobs(db_session)
    assert len(jobs_second) == 1
    assert jobs_second[0]["id"] == jobs_first[0]["id"]


async def _set_weather(
    db_session: AsyncSession,
    cell_id: int,
    day: datetime.date,
    *,
    et0_d_minus_1: float = 5.0,
    rain_d_minus_1: float = 0.0,
    rain_48h: float = 0.0,
    rain_7d: float = 0.0,
    et0_7d: float = 35.0,
) -> None:
    """Populates weather_daily rows for D-1 through D+6."""
    ref_time = datetime.datetime.now(datetime.UTC)
    fetched_at = ref_time - datetime.timedelta(hours=2)
    d_minus_1 = day - datetime.timedelta(days=1)
    rows: list[WeatherDailyRow] = [
        WeatherDailyRow(
            cell_id=cell_id,
            day=d_minus_1,
            is_forecast=False,
            et0_mm=Decimal(str(et0_d_minus_1)),
            rain_mm=Decimal(str(rain_d_minus_1)),
            tmin_c=Decimal("22.0"),
            tmax_c=Decimal("32.0"),
            rh_mean_pct=Decimal("65.0"),
            fetched_at=fetched_at,
        )
    ]
    daily_rain_48h = rain_48h / 2.0
    daily_rain_rem = (rain_7d - rain_48h) / 5.0 if rain_7d > rain_48h else 0.0
    daily_et0 = et0_7d / 7.0

    for offset in range(7):
        f_day = day + datetime.timedelta(days=offset)
        r_mm = daily_rain_48h if offset < 2 else daily_rain_rem
        rows.append(
            WeatherDailyRow(
                cell_id=cell_id,
                day=f_day,
                is_forecast=True,
                et0_mm=Decimal(str(round(daily_et0, 2))),
                rain_mm=Decimal(str(round(r_mm, 2))),
                tmin_c=Decimal("23.0"),
                tmax_c=Decimal("33.0"),
                rh_mean_pct=Decimal("60.0"),
                fetched_at=fetched_at,
            )
        )
    for r in rows:
        await db_session.merge(r)
    await db_session.commit()


async def test_per_plot_task_persists_balance_and_recommendation_and_commits(
    db_session: AsyncSession,
) -> None:
    """The per-plot task executes run_daily_balance, persists the D-1 balance row

    and the D recommendation row, and commits the transaction.
    """
    target_day = local_today()
    d_minus_1 = target_day - datetime.timedelta(days=1)
    org_id, farm_id, cell_id = await _make_org_and_farm(db_session)
    plot_id = await _create_plot(
        db_session,
        org_id=org_id,
        farm_id=farm_id,
        cell_id=cell_id,
        name="Lote Balance",
        irrigation_system="drip",
        has_active_cycle=True,
    )

    db_session.add(
        SoilProfileRow(
            plot_id=plot_id,
            source="lab",
            texture="sandy_loam",
            field_capacity_pct=Decimal("23.0"),
            wilting_point_pct=Decimal("9.0"),
            root_depth_cm=Decimal("60.0"),
        )
    )
    await db_session.commit()
    await _set_weather(db_session, cell_id, target_day)

    # Run the per-plot task
    await run_plot_balance(plot_id=str(plot_id), day=target_day.isoformat())

    # Assert rows exist in DB and committed
    balances = SqlAlchemyWaterBalanceRepository(db_session)
    recs = SqlAlchemyIrrigationRecommendationRepository(db_session)

    balance = await balances.get_for_plot(plot_id, d_minus_1)
    assert balance is not None
    assert balance.plot_id == plot_id
    assert balance.day == d_minus_1

    rec = await recs.get_for_plot(plot_id, target_day)
    assert rec is not None
    assert rec.day == target_day


async def test_skipped_plot_logs_and_stores_nothing(
    db_session: AsyncSession,
) -> None:
    """When a plot cannot be calculated (e.g. incomplete soil profile),

    the per-plot task logs the skip reason, does not raise, and stores no rows.
    """
    target_day = local_today()
    d_minus_1 = target_day - datetime.timedelta(days=1)
    org_id, farm_id, cell_id = await _make_org_and_farm(db_session)
    plot_id = await _create_plot(
        db_session,
        org_id=org_id,
        farm_id=farm_id,
        cell_id=cell_id,
        name="Lote Incomplete Soil",
        irrigation_system="drip",
        has_active_cycle=True,
    )

    # Add incomplete soil profile (missing field_capacity_pct)
    db_session.add(
        SoilProfileRow(
            plot_id=plot_id,
            source="lab",
            texture="sandy_loam",
            field_capacity_pct=None,
            wilting_point_pct=Decimal("9.0"),
            root_depth_cm=Decimal("60.0"),
        )
    )
    await db_session.commit()
    await _set_weather(db_session, cell_id, target_day)

    # Run the per-plot task
    await run_plot_balance(plot_id=str(plot_id), day=target_day.isoformat())

    balances = SqlAlchemyWaterBalanceRepository(db_session)
    recs = SqlAlchemyIrrigationRecommendationRepository(db_session)

    assert await balances.get_for_plot(plot_id, d_minus_1) is None
    assert await recs.get_for_plot(plot_id, target_day) is None


class _RaisingRecommendationRepository:
    """Always raises on upsert, standing in for
    `SqlAlchemyIrrigationRecommendationRepository` to prove atomicity
    (R3-atomic-write-unproved): `run_daily_balance` itself never commits (only
    flushes, T3b/docs check), and the caller (`run_plot_balance`) commits once
    at the end, so a failure here must leave no balance row behind either.
    """

    def __init__(self, _session: AsyncSession) -> None:
        pass

    async def upsert(
        self, recommendation: object, *, plot_id: UUID, day: datetime.date, **_: object
    ) -> object:
        raise RuntimeError("boom: recommendation upsert failed")

    async def get_for_plot(
        self, plot_id: UUID, day: datetime.date, org_id: UUID | None = None
    ) -> object | None:
        return None


async def test_per_plot_task_leaves_no_balance_row_if_recommendation_upsert_fails(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The per-plot task's own session is only committed once, at the end
    (jobs.py docstring). If the recommendation upsert fails after the balance
    upsert already flushed within that same (never-committed) session, no
    balance row survives — proven from this test's own, separate session.
    """
    target_day = local_today()
    d_minus_1 = target_day - datetime.timedelta(days=1)
    org_id, farm_id, cell_id = await _make_org_and_farm(db_session)
    plot_id = await _create_plot(
        db_session,
        org_id=org_id,
        farm_id=farm_id,
        cell_id=cell_id,
        name="Lote Atomic",
        irrigation_system="drip",
        has_active_cycle=True,
    )
    db_session.add(
        SoilProfileRow(
            plot_id=plot_id,
            source="lab",
            texture="sandy_loam",
            field_capacity_pct=Decimal("23.0"),
            wilting_point_pct=Decimal("9.0"),
            root_depth_cm=Decimal("60.0"),
        )
    )
    await db_session.commit()
    await _set_weather(db_session, cell_id, target_day)

    monkeypatch.setattr(
        "techcamp.irrigation.adapters.jobs.SqlAlchemyIrrigationRecommendationRepository",
        _RaisingRecommendationRepository,
    )

    with pytest.raises(RuntimeError, match="boom"):
        await run_plot_balance(plot_id=str(plot_id), day=target_day.isoformat())

    balances = SqlAlchemyWaterBalanceRepository(db_session)
    assert await balances.get_for_plot(plot_id, d_minus_1) is None


def _client() -> TestClient:
    return TestClient(app, base_url="http://testserver/api/v1")


def _registered_paths(application: object) -> set[str]:
    return set(application.openapi()["paths"])  # type: ignore[union-attr]


async def _queued_fanout_jobs(db_session: AsyncSession) -> dict[int, dict[str, object]]:
    return {
        job.id: {
            "task_name": job.task_name,
            "queue_name": job.queue_name,
            "status": job.status,
            "args": job.args,
        }
        for job in (
            await db_session.execute(
                text(
                    "SELECT id, task_name, queue_name, status, args FROM procrastinate_jobs "
                    "WHERE task_name = :t ORDER BY id"
                ),
                {"t": RUN_DAILY_PLOTS_TASK_NAME},
            )
        ).all()
    }


async def test_dev_route_returns_job_id_and_defaults_day_to_today(
    db_session: AsyncSession,
) -> None:
    """POST /dev/jobs/irrigation:run returns job_id and queues the fan-out task

    defaulting to local today (docs/04-api.md:181).
    """
    response = _client().post(_ROUTE, json={})
    assert response.request.url.path == _REGISTERED_PATH
    assert response.status_code == 200
    body = response.json()
    assert "job_id" in body
    queued = await _queued_fanout_jobs(db_session)
    assert body["job_id"] in queued
    assert queued[body["job_id"]]["task_name"] == RUN_DAILY_PLOTS_TASK_NAME
    assert queued[body["job_id"]]["queue_name"] == QUEUE_NAME
    assert queued[body["job_id"]]["status"] == "todo"
    assert queued[body["job_id"]]["args"]["day"] == local_today().isoformat()


async def test_dev_route_accepts_specific_day(db_session: AsyncSession) -> None:
    """The route accepts a specific past day."""
    day = local_today() - datetime.timedelta(days=2)
    response = _client().post(_ROUTE, json={"day": day.isoformat()})
    assert response.status_code == 200
    body = response.json()
    queued = await _queued_fanout_jobs(db_session)
    assert queued[body["job_id"]]["args"]["day"] == day.isoformat()


async def test_dev_route_rejects_future_day_with_422(db_session: AsyncSession) -> None:
    """A day after today cannot be computed (balance day D-1 must be over) -> 422 problem+json."""
    tomorrow = local_today() + datetime.timedelta(days=1)
    response = _client().post(_ROUTE, json={"day": tomorrow.isoformat()})
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    queued = await _queued_fanout_jobs(db_session)
    assert queued == {}


async def test_dev_route_rejects_invalid_date_format(db_session: AsyncSession) -> None:
    """An unparseable string is FastAPI validation error -> 422 application/json."""
    response = _client().post(_ROUTE, json={"day": "manana"})
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/json"
    queued = await _queued_fanout_jobs(db_session)
    assert queued == {}


async def test_dev_route_is_absent_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """The /dev routes are only registered in the seminar profile (ADR-0021)."""
    import techcamp.main as main

    assert _REGISTERED_PATH in _registered_paths(app), "seminar profile keeps the route"
    monkeypatch.setenv("TECHCAMP_PROFILE", "production")
    try:
        production_app = importlib.reload(main).app
        assert _REGISTERED_PATH not in _registered_paths(production_app)
    finally:
        monkeypatch.undo()
        importlib.reload(main)
    assert _REGISTERED_PATH in _registered_paths(main.app)
