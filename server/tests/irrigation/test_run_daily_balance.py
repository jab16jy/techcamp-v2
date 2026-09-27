"""Tests for daily water balance and recommendation use case (docs/06 §5; ADR-0022; ADR-0023).

Tests against real Postgres with TDD:
- Irrigated plot -> irrigate/postpone/not_needed
- Rainfed with and without cycle (delay_sowing)
- no_kc skips balance and stores no_kc recommendation
- Incomplete soil skipped (no rows written, skip reason returned)
- Previous-day chaining (D-2 row feeds D-1 Dr_prev)
- Missing observed weather falls back to forecast and is flagged in rationale
- Org of the rows is the plot's org
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import CropCycleRow, FarmRow, PlotRow, SoilProfileRow
from techcamp.farms.adapters.repositories import (
    SqlAlchemyCropCycleRepository,
    SqlAlchemyCropRepository,
    SqlAlchemyPlotRepository,
    SqlAlchemySoilProfileRepository,
)
from techcamp.farms.domain.models import Crop, CropCycleStatus, CropStage, KcSource
from techcamp.identity.adapters.orm import OrganizationRow
from techcamp.irrigation.adapters.repositories import (
    SqlAlchemyIrrigationRecommendationRepository,
    SqlAlchemyWaterBalanceRepository,
)
from techcamp.irrigation.application.run_daily_balance import (
    DailyBalanceResult,
    run_daily_balance,
)
from techcamp.irrigation.domain.models import RainfedAdvice, RecommendationKind, WaterBalanceDay
from techcamp.shared.db import engine
from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.orm import CalibrationRow, NodeRow, ReadingRow, SensorRow
from techcamp.telemetry.adapters.repositories import (
    SqlAlchemyCalibrationRepository,
    SqlAlchemyNodeRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemySensorRepository,
)
from techcamp.weather.adapters.orm import WeatherDailyRow
from techcamp.weather.adapters.repositories import SqlAlchemyWeatherRepository

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)


async def _create_test_fixture(
    db_session: AsyncSession,
    *,
    irrigation_system: str = "drip",
    crop_code: str = "maize",
    has_cycle: bool = True,
    days_sown_ago: int = 40,
    has_soil: bool = True,
    incomplete_soil: bool = False,
    day: date = date(2026, 9, 25),
) -> tuple[UUID, UUID, int, int]:
    """Sets up an org, farm, cell, plot, cycle, and soil profile."""
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

    plot_id = uuid7()
    is_irrigated = irrigation_system != "none"
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote Test",
            boundary=_BOUNDARY,
            weather_cell_id=cell_id,
            irrigation_system=irrigation_system,
            irrigation_efficiency=Decimal("0.90") if is_irrigated else None,
            system_flow_lph=Decimal("1000.0") if is_irrigated else None,
        )
    )
    await db_session.commit()

    crop_repo = SqlAlchemyCropRepository(db_session)
    crops = await crop_repo.list_all()
    crop = next(c for c in crops if c.code == crop_code)

    if has_cycle:
        sown_on = (day - timedelta(days=1)) - timedelta(days=days_sown_ago - 1)
        cycle_id = uuid7()
        db_session.add(
            CropCycleRow(
                id=cycle_id,
                plot_id=plot_id,
                crop_id=crop.id,
                sown_on=sown_on,
                status=CropCycleStatus.ACTIVE.value,
            )
        )
        await db_session.commit()

    if has_soil:
        db_session.add(
            SoilProfileRow(
                plot_id=plot_id,
                source="lab",
                texture="sandy_loam",
                field_capacity_pct=None if incomplete_soil else Decimal("23.0"),
                wilting_point_pct=Decimal("9.0"),
                root_depth_cm=Decimal("60.0"),
            )
        )
        await db_session.commit()

    return org_id, plot_id, cell_id, crop.id


async def _set_weather(
    db_session: AsyncSession,
    cell_id: int,
    day: date,
    *,
    et0_d_minus_1: float = 5.0,
    rain_d_minus_1: float = 0.0,
    rain_48h: float = 0.0,
    rain_7d: float = 0.0,
    et0_7d: float = 35.0,
    missing_observed: bool = False,
    stale_weather: bool = False,
    now: datetime | None = None,
) -> None:
    """Populates weather_daily for D-1 through D+6."""
    ref_time = now or datetime.now(UTC)
    fetched_at = ref_time - timedelta(hours=25) if stale_weather else ref_time - timedelta(hours=2)

    d_minus_1 = day - timedelta(days=1)
    rows: list[WeatherDailyRow] = []

    if not missing_observed:
        rows.append(
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
        )
    else:
        rows.append(
            WeatherDailyRow(
                cell_id=cell_id,
                day=d_minus_1,
                is_forecast=True,
                et0_mm=Decimal(str(et0_d_minus_1)),
                rain_mm=Decimal(str(rain_d_minus_1)),
                tmin_c=Decimal("22.0"),
                tmax_c=Decimal("32.0"),
                rh_mean_pct=Decimal("65.0"),
                fetched_at=fetched_at,
            )
        )

    daily_rain_48h = rain_48h / 2.0
    daily_rain_rem = (rain_7d - rain_48h) / 5.0 if rain_7d > rain_48h else 0.0
    daily_et0 = et0_7d / 7.0

    for offset in range(7):
        f_day = day + timedelta(days=offset)
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


async def test_irrigated_plot_irrigate(db_session: AsyncSession) -> None:
    """Irrigated plot with high depletion (Dr >= RAW) and no rain -> IRRIGATE."""
    target_day = date(2026, 9, 25)
    _org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(
        db_session, irrigation_system="drip", day=target_day
    )
    await _set_weather(db_session, cell_id, target_day, et0_d_minus_1=6.0, rain_48h=0.0)

    wb_repo = SqlAlchemyWaterBalanceRepository(db_session)
    d_minus_2 = target_day - timedelta(days=2)

    await wb_repo.upsert(
        WaterBalanceDay(
            plot_id=plot_id,
            day=d_minus_2,
            etc_mm=5.0,
            effective_rain_mm=0.0,
            irrigation_mm=0.0,
            taw_mm=84.0,
            raw_mm=46.2,
            depletion_model_mm=45.0,
            depletion_mm=45.0,
            soil_moisture_obs_pct=None,
            assimilation_k=0.0,
            stress_moisture_pct=15.3,
        )
    )
    await db_session.commit()

    result = await run_daily_balance(
        plot_id=plot_id,
        day=target_day,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=wb_repo,
        recommendations=SqlAlchemyIrrigationRecommendationRepository(db_session),
    )
    await db_session.commit()

    assert not result.skipped
    assert result.balance is not None
    assert result.balance.day == target_day - timedelta(days=1)
    assert result.recommendation is not None
    assert result.recommendation.kind == RecommendationKind.IRRIGATE
    assert result.recommendation.depth_mm is not None and result.recommendation.depth_mm > 0
    assert result.recommendation.duration_min is not None and result.recommendation.duration_min > 0


async def test_irrigated_plot_postpone(db_session: AsyncSession) -> None:
    """Irrigated plot with Dr >= RAW, but 48h forecast rain covers Dr -> POSTPONE."""
    target_day = date(2026, 9, 25)
    _org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(
        db_session, irrigation_system="drip", day=target_day
    )
    # Heavy 48h forecast rain: 60mm
    await _set_weather(db_session, cell_id, target_day, et0_d_minus_1=5.0, rain_48h=60.0)

    wb_repo = SqlAlchemyWaterBalanceRepository(db_session)
    d_minus_2 = target_day - timedelta(days=2)
    await wb_repo.upsert(
        WaterBalanceDay(
            plot_id=plot_id,
            day=d_minus_2,
            etc_mm=5.0,
            effective_rain_mm=0.0,
            irrigation_mm=0.0,
            taw_mm=84.0,
            raw_mm=46.2,
            depletion_model_mm=45.0,
            depletion_mm=45.0,
            soil_moisture_obs_pct=None,
            assimilation_k=0.0,
            stress_moisture_pct=15.3,
        )
    )
    await db_session.commit()

    result = await run_daily_balance(
        plot_id=plot_id,
        day=target_day,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=wb_repo,
        recommendations=SqlAlchemyIrrigationRecommendationRepository(db_session),
    )
    await db_session.commit()

    assert not result.skipped
    assert result.recommendation is not None
    assert result.recommendation.kind == RecommendationKind.POSTPONE
    assert result.recommendation.depth_mm is None
    assert result.recommendation.duration_min is None


async def test_irrigated_plot_not_needed(db_session: AsyncSession) -> None:
    """Irrigated plot with low depletion (Dr < RAW) -> NOT_NEEDED."""
    target_day = date(2026, 9, 25)
    _org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(
        db_session, irrigation_system="drip", day=target_day
    )
    # Day D-1 has mild ET0 and heavy rain, Dr stays near 0
    await _set_weather(
        db_session, cell_id, target_day, et0_d_minus_1=2.0, rain_d_minus_1=20.0, rain_48h=0.0
    )

    result = await run_daily_balance(
        plot_id=plot_id,
        day=target_day,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=SqlAlchemyWaterBalanceRepository(db_session),
        recommendations=SqlAlchemyIrrigationRecommendationRepository(db_session),
    )
    await db_session.commit()

    assert not result.skipped
    assert result.recommendation is not None
    assert result.recommendation.kind == RecommendationKind.NOT_NEEDED
    assert result.recommendation.depth_mm is None


async def test_rainfed_plot_with_cycle(db_session: AsyncSession) -> None:
    """Rainfed plot with active cycle gets RAINFED kind and advice."""
    target_day = date(2026, 9, 25)
    _org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(
        db_session, irrigation_system="none", day=target_day
    )
    await _set_weather(
        db_session, cell_id, target_day, et0_d_minus_1=5.0, rain_48h=0.0, rain_7d=0.0
    )

    # High depletion at D-2
    wb_repo = SqlAlchemyWaterBalanceRepository(db_session)
    d_minus_2 = target_day - timedelta(days=2)
    await wb_repo.upsert(
        WaterBalanceDay(
            plot_id=plot_id,
            day=d_minus_2,
            etc_mm=5.0,
            effective_rain_mm=0.0,
            irrigation_mm=0.0,
            taw_mm=84.0,
            raw_mm=46.2,
            depletion_model_mm=50.0,
            depletion_mm=50.0,
            soil_moisture_obs_pct=None,
            assimilation_k=0.0,
            stress_moisture_pct=15.3,
        )
    )
    await db_session.commit()

    result = await run_daily_balance(
        plot_id=plot_id,
        day=target_day,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=wb_repo,
        recommendations=SqlAlchemyIrrigationRecommendationRepository(db_session),
    )
    await db_session.commit()

    assert not result.skipped
    assert result.balance is not None
    assert result.recommendation is not None
    assert result.recommendation.kind == RecommendationKind.RAINFED
    assert result.recommendation.depth_mm is None
    assert RainfedAdvice.CONSERVE_MOISTURE in result.recommendation.advice


async def test_rainfed_plot_without_cycle_delay_sowing(db_session: AsyncSession) -> None:
    """Rainfed plot without active cycle stores delay_sowing advice and no balance row."""
    target_day = date(2026, 9, 25)
    _org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(
        db_session, irrigation_system="none", has_cycle=False, day=target_day
    )
    # rain_7d (5 mm) < et0_7d (30 mm) -> delay_sowing
    await _set_weather(db_session, cell_id, target_day, rain_7d=5.0, et0_7d=30.0)

    wb_repo = SqlAlchemyWaterBalanceRepository(db_session)
    rec_repo = SqlAlchemyIrrigationRecommendationRepository(db_session)

    result = await run_daily_balance(
        plot_id=plot_id,
        day=target_day,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=wb_repo,
        recommendations=rec_repo,
    )
    await db_session.commit()

    assert not result.skipped
    assert result.balance is None  # No balance row for plot without active cycle
    assert result.recommendation is not None
    assert result.recommendation.kind == RecommendationKind.RAINFED
    assert RainfedAdvice.DELAY_SOWING in result.recommendation.advice

    # Ensure no balance row was written to the database
    d_minus_1 = target_day - timedelta(days=1)
    db_bal = await wb_repo.get_for_plot(plot_id, d_minus_1)
    assert db_bal is None


async def test_no_kc_skips_balance_and_stores_no_kc_recommendation(
    db_session: AsyncSession,
) -> None:
    """Plot with crop having kc_source = none stores no_kc recommendation and skips balance."""
    target_day = date(2026, 9, 25)
    _org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(
        db_session, irrigation_system="drip", crop_code="yam", day=target_day
    )
    await _set_weather(db_session, cell_id, target_day)

    wb_repo = SqlAlchemyWaterBalanceRepository(db_session)
    rec_repo = SqlAlchemyIrrigationRecommendationRepository(db_session)

    result = await run_daily_balance(
        plot_id=plot_id,
        day=target_day,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=wb_repo,
        recommendations=rec_repo,
    )
    await db_session.commit()

    assert not result.skipped
    assert result.balance is None
    assert result.recommendation is not None
    assert result.recommendation.kind == RecommendationKind.NO_KC
    assert result.recommendation.depth_mm is None

    # Verify database state
    d_minus_1 = target_day - timedelta(days=1)
    db_bal = await wb_repo.get_for_plot(plot_id, d_minus_1)
    assert db_bal is None
    db_rec = await rec_repo.get_for_plot(plot_id, target_day)
    assert db_rec is not None
    assert db_rec.kind == RecommendationKind.NO_KC


async def test_incomplete_soil_skips_balance_and_recommendation(
    db_session: AsyncSession,
) -> None:
    """Soil missing field_capacity_pct, wilting_point_pct, or root_depth_cm skips run."""
    target_day = date(2026, 9, 25)
    _org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(
        db_session, irrigation_system="drip", incomplete_soil=True, day=target_day
    )
    await _set_weather(db_session, cell_id, target_day)

    wb_repo = SqlAlchemyWaterBalanceRepository(db_session)
    rec_repo = SqlAlchemyIrrigationRecommendationRepository(db_session)

    result = await run_daily_balance(
        plot_id=plot_id,
        day=target_day,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=wb_repo,
        recommendations=rec_repo,
    )
    await db_session.commit()

    assert result.skipped
    assert result.skip_reason == "soil_profile_incomplete"
    assert result.balance is None
    assert result.recommendation is None

    d_minus_1 = target_day - timedelta(days=1)
    assert await wb_repo.get_for_plot(plot_id, d_minus_1) is None
    assert await rec_repo.get_for_plot(plot_id, target_day) is None


async def test_previous_day_chaining_d2_feeds_d1(db_session: AsyncSession) -> None:
    """D-2 balance row feeds Dr_prev for D-1 model depletion."""
    target_day = date(2026, 9, 25)
    _org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(
        db_session, irrigation_system="drip", day=target_day
    )
    # Day D-1 has ETc ≈ 0 (et0 = 0) and no rain
    await _set_weather(db_session, cell_id, target_day, et0_d_minus_1=0.0, rain_d_minus_1=0.0)

    wb_repo = SqlAlchemyWaterBalanceRepository(db_session)
    d_minus_2 = target_day - timedelta(days=2)
    # Seed D-2 with depletion_mm = 25.0
    await wb_repo.upsert(
        WaterBalanceDay(
            plot_id=plot_id,
            day=d_minus_2,
            etc_mm=4.0,
            effective_rain_mm=0.0,
            irrigation_mm=0.0,
            taw_mm=84.0,
            raw_mm=46.2,
            depletion_model_mm=25.0,
            depletion_mm=25.0,
            soil_moisture_obs_pct=None,
            assimilation_k=0.0,
            stress_moisture_pct=15.3,
        )
    )
    await db_session.commit()

    result = await run_daily_balance(
        plot_id=plot_id,
        day=target_day,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=wb_repo,
        recommendations=SqlAlchemyIrrigationRecommendationRepository(db_session),
    )
    await db_session.commit()

    assert not result.skipped
    assert result.balance is not None
    # Dr_prev = 25.0 carried forward (plus etc=0, pe=0)
    assert result.balance.depletion_model_mm == pytest.approx(25.0)


async def test_missing_observed_weather_falls_back_to_forecast_and_flagged(
    db_session: AsyncSession,
) -> None:
    """Missing observed row for D-1 falls back to forecast row and sets flag in rationale."""
    target_day = date(2026, 9, 25)
    _org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(
        db_session, irrigation_system="drip", day=target_day
    )
    await _set_weather(db_session, cell_id, target_day, et0_d_minus_1=4.5, missing_observed=True)

    result = await run_daily_balance(
        plot_id=plot_id,
        day=target_day,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=SqlAlchemyWaterBalanceRepository(db_session),
        recommendations=SqlAlchemyIrrigationRecommendationRepository(db_session),
    )
    await db_session.commit()

    assert not result.skipped
    assert result.balance is not None
    assert result.recommendation is not None
    assert result.recommendation.rationale.get("missing_observed_weather") is True


async def test_org_of_rows_is_the_plots_org(db_session: AsyncSession) -> None:
    """Balance and recommendation rows belong to the plot's organization."""
    target_day = date(2026, 9, 25)
    org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(
        db_session, irrigation_system="drip", day=target_day
    )
    await _set_weather(db_session, cell_id, target_day)

    wb_repo = SqlAlchemyWaterBalanceRepository(db_session)
    rec_repo = SqlAlchemyIrrigationRecommendationRepository(db_session)

    result = await run_daily_balance(
        plot_id=plot_id,
        day=target_day,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=wb_repo,
        recommendations=rec_repo,
    )
    await db_session.commit()

    assert not result.skipped
    d_minus_1 = target_day - timedelta(days=1)

    # 1. Reading with the plot's org_id succeeds
    bal_own = await wb_repo.get_for_plot(plot_id, d_minus_1, org_id=org_id)
    assert bal_own is not None
    rec_own = await rec_repo.get_for_plot(plot_id, target_day, org_id=org_id)
    assert rec_own is not None

    # 2. Reading with a foreign org_id returns None (docs/09 org isolation)
    foreign_org = uuid7()
    bal_foreign = await wb_repo.get_for_plot(plot_id, d_minus_1, org_id=foreign_org)
    assert bal_foreign is None
    rec_foreign = await rec_repo.get_for_plot(plot_id, target_day, org_id=foreign_org)
    assert rec_foreign is None


async def _setup_sensor(
    db_session: AsyncSession,
    *,
    org_id: UUID,
    plot_id: UUID,
    depth_cm: int = 30,
    calibration_kind: str = "field",
    reading_fresh: bool = True,
    daily_value: float = 16.0,
    target_day: date = date(2026, 9, 25),
    now: datetime | None = None,
) -> int:
    """Creates a node, soil_moisture sensor, calibration, and readings for testing assimilation."""
    from uuid import uuid4

    ref_time = now or datetime.now(UTC)
    node_id = uuid7()
    db_session.add(
        NodeRow(
            id=node_id,
            org_id=org_id,
            plot_id=plot_id,
            transport="wifi",
            dev_eui=f"eui-{uuid4().hex[:12]}",
            claim_code=f"claim-{uuid4().hex[:12]}",
            credential_hash="hash",
            interval_s=900,
            status="online",
            claimed_at=ref_time - timedelta(days=10),
            last_seen_at=ref_time,
        )
    )
    await db_session.flush()

    sensor_row = SensorRow(
        node_id=node_id,
        channel_key=f"sm_{depth_cm}_{uuid4().hex[:6]}",
        metric="soil_moisture",
        depth_cm=depth_cm,
        unit="pct",
    )
    db_session.add(sensor_row)
    await db_session.flush()

    cal_id = uuid7()
    db_session.add(
        CalibrationRow(
            id=cal_id,
            sensor_id=sensor_row.id,
            version=1,
            method="linear",
            kind=calibration_kind,
            params={"scale": 1.0, "offset": 0.0},
            rmse_pct=None,
            valid_from=ref_time - timedelta(days=10),
        )
    )
    await db_session.commit()

    d_minus_1 = target_day - timedelta(days=1)
    if reading_fresh:
        fresh_time = ref_time - timedelta(hours=2)
        db_session.add(
            ReadingRow(
                time=fresh_time,
                sensor_id=sensor_row.id,
                raw_value=float(daily_value),
                value=float(daily_value),
                received_at=fresh_time,
                quality=0,
            )
        )
        d1_time = datetime(d_minus_1.year, d_minus_1.month, d_minus_1.day, 12, 0, tzinfo=UTC)
        db_session.add(
            ReadingRow(
                time=d1_time,
                sensor_id=sensor_row.id,
                raw_value=float(daily_value),
                value=float(daily_value),
                received_at=d1_time,
                quality=0,
            )
        )
    else:
        stale_time = ref_time - timedelta(hours=26)
        db_session.add(
            ReadingRow(
                time=stale_time,
                sensor_id=sensor_row.id,
                raw_value=float(daily_value),
                value=float(daily_value),
                received_at=stale_time,
                quality=0,
            )
        )
    await db_session.commit()

    autocommit_engine = engine.execution_options(isolation_level="AUTOCOMMIT")
    async with autocommit_engine.connect() as conn:
        await conn.execute(text("CALL refresh_continuous_aggregate('reading_daily', NULL, NULL)"))

    return sensor_row.id


async def test_sensor_assimilation_field_representative_fresh(db_session: AsyncSession) -> None:
    """Field calibration + representative depth + fresh reading -> K 0.5 and Dr assimilated."""
    target_day = date(2026, 9, 25)
    now = datetime(2026, 9, 25, 4, 30, tzinfo=UTC)
    org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(
        db_session, irrigation_system="drip", day=target_day
    )
    await _set_weather(db_session, cell_id, target_day, now=now)

    # Root depth is 60 cm in fixture. 30 cm is exactly Zr/2 -> representative!
    await _setup_sensor(
        db_session,
        org_id=org_id,
        plot_id=plot_id,
        depth_cm=30,
        calibration_kind="field",
        reading_fresh=True,
        daily_value=16.0,
        target_day=target_day,
        now=now,
    )

    result = await run_daily_balance(
        plot_id=plot_id,
        day=target_day,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=SqlAlchemyWaterBalanceRepository(db_session),
        recommendations=SqlAlchemyIrrigationRecommendationRepository(db_session),
        nodes=SqlAlchemyNodeRepository(db_session),
        sensors=SqlAlchemySensorRepository(db_session),
        calibrations=SqlAlchemyCalibrationRepository(db_session),
        readings=SqlAlchemyReadingRepository(db_session),
        now=now,
    )
    await db_session.commit()

    assert not result.skipped
    assert result.balance is not None
    assert result.balance.assimilation_k == pytest.approx(0.5)
    assert result.balance.soil_moisture_obs_pct == pytest.approx(16.0)
    expected_dr_obs = 1000.0 * (0.23 - 0.16) * 0.6  # 42.0 mm
    expected_dr_assimilated = result.balance.depletion_model_mm + 0.5 * (
        expected_dr_obs - result.balance.depletion_model_mm
    )
    assert result.balance.depletion_mm == pytest.approx(expected_dr_assimilated)
    assert result.recommendation is not None
    assert result.recommendation.rationale.get("without_sensor") is False
    assert result.recommendation.rationale.get("k") == pytest.approx(0.5)


async def test_sensor_assimilation_lab_calibration_yields_k_zero(db_session: AsyncSession) -> None:
    """Lab calibration -> K 0, without_sensor True, no assimilation."""
    target_day = date(2026, 9, 25)
    now = datetime(2026, 9, 25, 4, 30, tzinfo=UTC)
    org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(
        db_session, irrigation_system="drip", day=target_day
    )
    await _set_weather(db_session, cell_id, target_day, now=now)

    await _setup_sensor(
        db_session,
        org_id=org_id,
        plot_id=plot_id,
        depth_cm=30,
        calibration_kind="lab",
        reading_fresh=True,
        daily_value=16.0,
        target_day=target_day,
        now=now,
    )

    result = await run_daily_balance(
        plot_id=plot_id,
        day=target_day,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=SqlAlchemyWaterBalanceRepository(db_session),
        recommendations=SqlAlchemyIrrigationRecommendationRepository(db_session),
        nodes=SqlAlchemyNodeRepository(db_session),
        sensors=SqlAlchemySensorRepository(db_session),
        calibrations=SqlAlchemyCalibrationRepository(db_session),
        readings=SqlAlchemyReadingRepository(db_session),
        now=now,
    )
    await db_session.commit()

    assert not result.skipped
    assert result.balance is not None
    assert result.balance.assimilation_k == pytest.approx(0.0)
    assert result.balance.soil_moisture_obs_pct is None
    assert result.balance.depletion_mm == result.balance.depletion_model_mm
    assert result.recommendation is not None
    assert result.recommendation.rationale.get("without_sensor") is True
    assert result.recommendation.rationale.get("k") == pytest.approx(0.0)


async def test_sensor_assimilation_stale_reading_yields_k_zero(db_session: AsyncSession) -> None:
    """Stale sensor reading (> 24 h) -> K 0, without_sensor True."""
    target_day = date(2026, 9, 25)
    now = datetime(2026, 9, 25, 4, 30, tzinfo=UTC)
    org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(
        db_session, irrigation_system="drip", day=target_day
    )
    await _set_weather(db_session, cell_id, target_day, now=now)

    # Reading is 26h old
    await _setup_sensor(
        db_session,
        org_id=org_id,
        plot_id=plot_id,
        depth_cm=30,
        calibration_kind="field",
        reading_fresh=False,
        daily_value=16.0,
        target_day=target_day,
        now=now,
    )

    result = await run_daily_balance(
        plot_id=plot_id,
        day=target_day,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=SqlAlchemyWaterBalanceRepository(db_session),
        recommendations=SqlAlchemyIrrigationRecommendationRepository(db_session),
        nodes=SqlAlchemyNodeRepository(db_session),
        sensors=SqlAlchemySensorRepository(db_session),
        calibrations=SqlAlchemyCalibrationRepository(db_session),
        readings=SqlAlchemyReadingRepository(db_session),
        now=now,
    )
    await db_session.commit()

    assert not result.skipped
    assert result.balance is not None
    assert result.balance.assimilation_k == pytest.approx(0.0)
    assert result.balance.soil_moisture_obs_pct is None
    assert result.balance.depletion_mm == result.balance.depletion_model_mm
    assert result.recommendation is not None
    assert result.recommendation.rationale.get("without_sensor") is True


async def test_sensor_assimilation_shallow_sensor_yields_k_zero(db_session: AsyncSession) -> None:
    """Shallow sensor (10 cm under a 1 m root zone) -> not representative -> K 0."""
    target_day = date(2026, 9, 25)
    now = datetime(2026, 9, 25, 4, 30, tzinfo=UTC)
    org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(
        db_session, irrigation_system="drip", day=target_day
    )
    await _set_weather(db_session, cell_id, target_day, now=now)

    # Update soil root zone to 100 cm (1 m)
    soil_repo = SqlAlchemySoilProfileRepository(db_session)
    prof = await soil_repo.get_for_plot(plot_id)
    assert prof is not None
    from techcamp.farms.domain.models import SoilProfile

    await soil_repo.put(
        SoilProfile(
            plot_id=plot_id,
            source=prof.source,
            ph=prof.ph,
            organic_matter_pct=prof.organic_matter_pct,
            texture=prof.texture,
            field_capacity_pct=prof.field_capacity_pct,
            wilting_point_pct=prof.wilting_point_pct,
            root_depth_cm=100.0,
        )
    )

    # Sensor at 10 cm: Zr=100cm, Zr/2=50cm, tolerance=15cm -> [35..65cm]. 10cm is shallow!
    await _setup_sensor(
        db_session,
        org_id=org_id,
        plot_id=plot_id,
        depth_cm=10,
        calibration_kind="field",
        reading_fresh=True,
        daily_value=16.0,
        target_day=target_day,
        now=now,
    )

    result = await run_daily_balance(
        plot_id=plot_id,
        day=target_day,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=soil_repo,
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=SqlAlchemyWaterBalanceRepository(db_session),
        recommendations=SqlAlchemyIrrigationRecommendationRepository(db_session),
        nodes=SqlAlchemyNodeRepository(db_session),
        sensors=SqlAlchemySensorRepository(db_session),
        calibrations=SqlAlchemyCalibrationRepository(db_session),
        readings=SqlAlchemyReadingRepository(db_session),
        now=now,
    )
    await db_session.commit()

    assert not result.skipped
    assert result.balance is not None
    assert result.balance.assimilation_k == pytest.approx(0.0)
    assert result.balance.soil_moisture_obs_pct is None
    assert result.recommendation is not None
    assert result.recommendation.rationale.get("without_sensor") is True


async def test_sensor_assimilation_two_sensors_averaged(db_session: AsyncSession) -> None:
    """Two sensors at different depths in root zone -> averaged daily mean and K 0.5."""
    target_day = date(2026, 9, 25)
    now = datetime(2026, 9, 25, 4, 30, tzinfo=UTC)
    org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(
        db_session, irrigation_system="drip", day=target_day
    )
    await _set_weather(db_session, cell_id, target_day, now=now)

    # Root depth 60 cm. Sensor 1 at 20 cm (14%), Sensor 2 at 40 cm (18%).
    # Average = 16.0%.
    await _setup_sensor(
        db_session,
        org_id=org_id,
        plot_id=plot_id,
        depth_cm=20,
        calibration_kind="field",
        reading_fresh=True,
        daily_value=14.0,
        target_day=target_day,
        now=now,
    )
    await _setup_sensor(
        db_session,
        org_id=org_id,
        plot_id=plot_id,
        depth_cm=40,
        calibration_kind="field",
        reading_fresh=True,
        daily_value=18.0,
        target_day=target_day,
        now=now,
    )

    result = await run_daily_balance(
        plot_id=plot_id,
        day=target_day,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=SqlAlchemyWaterBalanceRepository(db_session),
        recommendations=SqlAlchemyIrrigationRecommendationRepository(db_session),
        nodes=SqlAlchemyNodeRepository(db_session),
        sensors=SqlAlchemySensorRepository(db_session),
        calibrations=SqlAlchemyCalibrationRepository(db_session),
        readings=SqlAlchemyReadingRepository(db_session),
        now=now,
    )
    await db_session.commit()

    assert not result.skipped
    assert result.balance is not None
    assert result.balance.assimilation_k == pytest.approx(0.5)
    assert result.balance.soil_moisture_obs_pct == pytest.approx(16.0)
    expected_dr_obs = 1000.0 * (0.23 - 0.16) * 0.6  # 42.0 mm
    expected_dr_assimilated = result.balance.depletion_model_mm + 0.5 * (
        expected_dr_obs - result.balance.depletion_model_mm
    )
    assert result.balance.depletion_mm == pytest.approx(expected_dr_assimilated)
    assert result.recommendation is not None
    assert result.recommendation.rationale.get("without_sensor") is False
    assert result.recommendation.rationale.get("k") == pytest.approx(0.5)


# --- Issue #99 T4 Sensor assimilation tests ---


@dataclass(frozen=True)
class SensorSpec:
    depth_cm: int | None = 30
    metric: str = "soil_moisture"
    calibration_kind: str = "field"
    readings: Sequence[tuple[datetime, float, int]] = ()


async def _setup_plot_with_sensors(
    db_session: AsyncSession,
    sensors: Sequence[SensorSpec],
    *,
    target_day: date = date(2026, 9, 25),
    now: datetime | None = None,
) -> tuple[UUID, UUID]:
    """Creates a plot with weather, node, and the specified field-calibrated sensors + readings."""
    ref_time = now or datetime(target_day.year, target_day.month, target_day.day, 4, 30, tzinfo=UTC)
    org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(
        db_session, irrigation_system="drip", day=target_day
    )
    await _set_weather(db_session, cell_id, target_day, now=ref_time)

    node_id = uuid7()
    db_session.add(
        NodeRow(
            id=node_id,
            org_id=org_id,
            plot_id=plot_id,
            transport="lorawan",
            claim_code=f"claim-{uuid4().hex[:6]}",
            credential_hash="hash",
            interval_s=900,
            status="online",
            claimed_at=ref_time - timedelta(days=10),
            last_seen_at=ref_time,
        )
    )
    await db_session.flush()

    for s in sensors:
        sensor_row = SensorRow(
            node_id=node_id,
            channel_key=f"s_{s.metric}_{s.depth_cm}_{uuid4().hex[:6]}",
            metric=s.metric,
            depth_cm=s.depth_cm,
            unit="pct" if s.metric == "soil_moisture" else "c",
        )
        db_session.add(sensor_row)
        await db_session.flush()

        db_session.add(
            CalibrationRow(
                id=uuid7(),
                sensor_id=sensor_row.id,
                version=1,
                method="linear",
                kind=s.calibration_kind,
                params={"scale": 1.0, "offset": 0.0},
                rmse_pct=None,
                valid_from=ref_time - timedelta(days=10),
            )
        )
        for t, val, qual in s.readings:
            db_session.add(
                ReadingRow(
                    time=t,
                    sensor_id=sensor_row.id,
                    raw_value=val,
                    value=val,
                    received_at=t,
                    quality=qual,
                )
            )

    await db_session.commit()
    return org_id, plot_id


async def _run_plot_daily_balance(
    db_session: AsyncSession,
    plot_id: UUID,
    target_day: date = date(2026, 9, 25),
    *,
    now: datetime | None = None,
) -> DailyBalanceResult:
    result = await run_daily_balance(
        plot_id=plot_id,
        day=target_day,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=SqlAlchemyWaterBalanceRepository(db_session),
        recommendations=SqlAlchemyIrrigationRecommendationRepository(db_session),
        nodes=SqlAlchemyNodeRepository(db_session),
        sensors=SqlAlchemySensorRepository(db_session),
        calibrations=SqlAlchemyCalibrationRepository(db_session),
        readings=SqlAlchemyReadingRepository(db_session),
        now=now,
    )
    await db_session.commit()
    return result


async def test_sensor_assimilation_rerun_with_now_far_from_d_gives_same_result(
    db_session: AsyncSession,
) -> None:
    """D2: Freshness and calibration lookup anchor to local day D-1 end, not now."""
    target_day = date(2026, 9, 25)
    now_day_d = datetime(2026, 9, 25, 4, 30, tzinfo=UTC)
    t1 = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)

    _, plot_id = await _setup_plot_with_sensors(
        db_session,
        [SensorSpec(depth_cm=30, readings=[(t1, 16.0, 0)])],
        target_day=target_day,
        now=now_day_d,
    )

    res_initial = await _run_plot_daily_balance(db_session, plot_id, target_day, now=now_day_d)
    assert not res_initial.skipped
    assert res_initial.balance is not None
    assert res_initial.balance.assimilation_k == pytest.approx(0.5)

    now_rerun = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
    res_rerun = await _run_plot_daily_balance(db_session, plot_id, target_day, now=now_rerun)
    assert not res_rerun.skipped
    assert res_rerun.balance is not None
    assert res_rerun.balance.assimilation_k == pytest.approx(0.5)
    assert res_rerun.balance.soil_moisture_obs_pct == pytest.approx(16.0)
    expected_dr_obs = 1000.0 * (0.23 - 0.16) * 0.6
    expected_dr = res_rerun.balance.depletion_model_mm + 0.5 * (
        expected_dr_obs - res_rerun.balance.depletion_model_mm
    )
    assert res_rerun.balance.depletion_mm == pytest.approx(expected_dr)


async def test_sensor_assimilation_reading_at_0200_utc_day_d_belongs_to_local_d_minus_1(
    db_session: AsyncSession,
) -> None:
    """D3: A reading at 02:00 UTC of day D is 21:00 Bogota of day D-1, so it belongs to D-1."""
    target_day = date(2026, 9, 25)
    now = datetime(2026, 9, 25, 4, 30, tzinfo=UTC)
    t = datetime(2026, 9, 25, 2, 0, tzinfo=UTC)

    _, plot_id = await _setup_plot_with_sensors(
        db_session,
        [SensorSpec(depth_cm=30, readings=[(t, 16.0, 0)])],
        target_day=target_day,
        now=now,
    )

    result = await _run_plot_daily_balance(db_session, plot_id, target_day, now=now)
    assert not result.skipped
    assert result.balance is not None
    assert result.balance.assimilation_k == pytest.approx(0.5)
    assert result.balance.soil_moisture_obs_pct == pytest.approx(16.0)


async def test_sensor_assimilation_reading_at_0300_utc_day_d_minus_1_belongs_to_local_d_minus_2(
    db_session: AsyncSession,
) -> None:
    """R3-local-day-start-boundary-untested: 03:00 UTC on day D-1 is 22:00 Bogota on day D-2,
    so it is before the 05:00 UTC local-day start and excluded -> K=0."""
    target_day = date(2026, 9, 25)
    now = datetime(2026, 9, 25, 4, 30, tzinfo=UTC)
    t = datetime(2026, 9, 24, 3, 0, tzinfo=UTC)

    _, plot_id = await _setup_plot_with_sensors(
        db_session,
        [SensorSpec(depth_cm=30, readings=[(t, 16.0, 0)])],
        target_day=target_day,
        now=now,
    )

    result = await _run_plot_daily_balance(db_session, plot_id, target_day, now=now)
    assert not result.skipped
    assert result.balance is not None
    assert result.balance.assimilation_k == pytest.approx(0.0)
    assert result.balance.soil_moisture_obs_pct is None
    assert result.balance.depletion_mm == result.balance.depletion_model_mm
    assert result.recommendation is not None
    assert result.recommendation.rationale.get("without_sensor") is True


async def test_sensor_assimilation_out_of_range_reading_excluded_from_daily_mean(
    db_session: AsyncSession,
) -> None:
    """D3: Out-of-range reading (quality & 2 != 0) is excluded from sensor daily mean."""
    target_day = date(2026, 9, 25)
    now = datetime(2026, 9, 25, 4, 30, tzinfo=UTC)
    t1 = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
    t2 = datetime(2026, 9, 24, 14, 0, tzinfo=UTC)

    _, plot_id = await _setup_plot_with_sensors(
        db_session,
        [SensorSpec(depth_cm=30, readings=[(t1, 16.0, 0), (t2, 99.0, 2)])],
        target_day=target_day,
        now=now,
    )

    result = await _run_plot_daily_balance(db_session, plot_id, target_day, now=now)
    assert not result.skipped
    assert result.balance is not None
    assert result.balance.assimilation_k == pytest.approx(0.5)
    assert result.balance.soil_moisture_obs_pct == pytest.approx(16.0)


async def test_sensor_assimilation_sensor_with_no_valid_readings_or_nan_mean_yields_k_zero(
    db_session: AsyncSession,
) -> None:
    """R3-daily-value-none: Sensor with out-of-range readings falls back to K=0, no TypeError."""
    target_day = date(2026, 9, 25)
    now = datetime(2026, 9, 25, 4, 30, tzinfo=UTC)
    t = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)

    _, plot_id = await _setup_plot_with_sensors(
        db_session,
        [SensorSpec(depth_cm=30, readings=[(t, 999.0, 2)])],
        target_day=target_day,
        now=now,
    )

    result = await _run_plot_daily_balance(db_session, plot_id, target_day, now=now)
    assert not result.skipped
    assert result.balance is not None
    assert result.balance.assimilation_k == pytest.approx(0.0)
    assert result.balance.soil_moisture_obs_pct is None
    assert result.balance.depletion_mm == result.balance.depletion_model_mm
    assert result.recommendation is not None
    assert result.recommendation.rationale.get("without_sensor") is True


async def test_sensor_assimilation_nan_reading_value_filtered_and_yields_k_zero(
    db_session: AsyncSession,
) -> None:
    """R3-nan-filter-branch-untested: A reading with value=NaN is excluded by the NaN filter,
    leaving no valid readings -> K=0, no TypeError or NaN propagation."""
    target_day = date(2026, 9, 25)
    now = datetime(2026, 9, 25, 4, 30, tzinfo=UTC)
    t = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)

    _, plot_id = await _setup_plot_with_sensors(
        db_session,
        [SensorSpec(depth_cm=30, readings=[(t, float("nan"), 0)])],
        target_day=target_day,
        now=now,
    )

    result = await _run_plot_daily_balance(db_session, plot_id, target_day, now=now)
    assert not result.skipped
    assert result.balance is not None
    assert result.balance.assimilation_k == pytest.approx(0.0)
    assert result.balance.soil_moisture_obs_pct is None
    assert result.balance.depletion_mm == result.balance.depletion_model_mm
    assert result.recommendation is not None
    assert result.recommendation.rationale.get("without_sensor") is True


async def test_sensor_assimilation_fresh_reading_no_balance_day_readings_yields_k_zero(
    db_session: AsyncSession,
) -> None:
    """R3-untested-branches (a): Sensor has fresh reading outside D-1, but none in D-1 -> K=0."""
    target_day = date(2026, 9, 25)
    now = datetime(2026, 9, 25, 8, 0, tzinfo=UTC)
    # Reading at 07:00 UTC day D (02:00 Bogota day D) is after the end of local day D-1 (05:00 UTC)
    t = datetime(2026, 9, 25, 7, 0, tzinfo=UTC)

    _, plot_id = await _setup_plot_with_sensors(
        db_session,
        [SensorSpec(depth_cm=30, readings=[(t, 16.0, 0)])],
        target_day=target_day,
        now=now,
    )

    result = await _run_plot_daily_balance(db_session, plot_id, target_day, now=now)
    assert not result.skipped
    assert result.balance is not None
    assert result.balance.assimilation_k == pytest.approx(0.0)
    assert result.balance.soil_moisture_obs_pct is None
    assert result.balance.depletion_mm == result.balance.depletion_model_mm
    assert result.recommendation is not None
    assert result.recommendation.rationale.get("without_sensor") is True


async def test_sensor_assimilation_three_or_more_sensors_yields_k_zero(
    db_session: AsyncSession,
) -> None:
    """R3-untested-branches (b): 3 or more qualifying sensors -> K=0 (only 1 or 2 supported)."""
    target_day = date(2026, 9, 25)
    now = datetime(2026, 9, 25, 4, 30, tzinfo=UTC)
    t = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)

    sensors = [
        SensorSpec(depth_cm=d, readings=[(t, v, 0)])
        for d, v in [(20, 14.0), (30, 16.0), (40, 18.0)]
    ]
    _, plot_id = await _setup_plot_with_sensors(db_session, sensors, target_day=target_day, now=now)

    result = await _run_plot_daily_balance(db_session, plot_id, target_day, now=now)
    assert not result.skipped
    assert result.balance is not None
    assert result.balance.assimilation_k == pytest.approx(0.0)
    assert result.balance.soil_moisture_obs_pct is None
    assert result.balance.depletion_mm == result.balance.depletion_model_mm
    assert result.recommendation is not None
    assert result.recommendation.rationale.get("without_sensor") is True


async def test_sensor_assimilation_non_soil_moisture_or_null_depth_ignored(
    db_session: AsyncSession,
) -> None:
    """R3-untested-branches (c): Non soil-moisture metric or null depth sensor is ignored."""
    target_day = date(2026, 9, 25)
    now = datetime(2026, 9, 25, 4, 30, tzinfo=UTC)
    t = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)

    sensors = [
        SensorSpec(depth_cm=30, metric="air_temp", readings=[(t, 25.0, 0)]),
        SensorSpec(depth_cm=None, metric="soil_moisture", readings=[(t, 25.0, 0)]),
    ]
    _, plot_id = await _setup_plot_with_sensors(db_session, sensors, target_day=target_day, now=now)

    result = await _run_plot_daily_balance(db_session, plot_id, target_day, now=now)
    assert not result.skipped
    assert result.balance is not None
    assert result.balance.assimilation_k == pytest.approx(0.0)
    assert result.balance.soil_moisture_obs_pct is None
    assert result.balance.depletion_mm == result.balance.depletion_model_mm
    assert result.recommendation is not None
    assert result.recommendation.rationale.get("without_sensor") is True


# --- Issue #115 round 1 findings ---


@dataclass
class _FakeCropRepositoryWithBadStage:
    """A stage name outside initial/development/mid/late that only a
    hand-built `Crop` can carry: `crop_stage`'s `ck_crop_stage_name` CHECK
    constraint rejects it at the real Postgres adapter, so this fakes the
    farms port instead of writing an invalid row (R3-kc-unknown-stage-
    raises-in-job)."""

    crop: Crop

    async def get(self, crop_id: int) -> Crop | None:
        return self.crop


async def test_invalid_crop_stage_name_skips_with_typed_reason_and_no_rows(
    db_session: AsyncSession,
) -> None:
    """A stage name compute_kc_for_cycle_day does not recognize must skip this one
    plot with a typed reason, not abort the whole job with an uncaught ValueError
    (R3-kc-unknown-stage-raises-in-job).
    """
    target_day = date(2026, 9, 25)
    _org_id, plot_id, cell_id, crop_id = await _create_test_fixture(
        db_session, irrigation_system="drip", day=target_day
    )
    await _set_weather(db_session, cell_id, target_day)

    bad_crop = Crop(
        id=crop_id,
        code="maize",
        name_es="Maíz",
        kc_source=KcSource.FAO56,
        stages=(
            CropStage(stage="inicial", length_days=18, kc=0.30, depletion_fraction_p=0.55),
            CropStage(stage="development", length_days=27, kc=0.75, depletion_fraction_p=0.55),
            CropStage(stage="mid", length_days=31, kc=1.20, depletion_fraction_p=0.55),
            CropStage(stage="late", length_days=14, kc=0.35, depletion_fraction_p=0.55),
        ),
    )

    wb_repo = SqlAlchemyWaterBalanceRepository(db_session)
    rec_repo = SqlAlchemyIrrigationRecommendationRepository(db_session)

    result = await run_daily_balance(
        plot_id=plot_id,
        day=target_day,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=_FakeCropRepositoryWithBadStage(bad_crop),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=wb_repo,
        recommendations=rec_repo,
    )
    await db_session.commit()

    assert result.skipped
    assert result.skip_reason == "crop_stage_invalid"
    assert result.balance is None
    assert result.recommendation is None

    d_minus_1 = target_day - timedelta(days=1)
    assert await wb_repo.get_for_plot(plot_id, d_minus_1) is None
    assert await rec_repo.get_for_plot(plot_id, target_day) is None


async def test_sensor_assimilation_clamps_depletion_within_0_taw_at_app_level(
    db_session: AsyncSession,
) -> None:
    """R3-clamp-wiring-untested-at-app-level: a field-calibrated representative
    sensor reading far above field capacity (fc=23%) must still persist
    depletion_mm inside [0, TAW], not a negative value from the raw formula.
    """
    target_day = date(2026, 9, 25)
    now = datetime(2026, 9, 25, 4, 30, tzinfo=UTC)
    org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(
        db_session, irrigation_system="drip", day=target_day
    )
    await _set_weather(db_session, cell_id, target_day, now=now)

    # Sensor at Zr/2 (30 cm of a 60 cm root zone) reads 40% (well above fc=23%,
    # e.g. right after heavy rain): the raw Dr_obs formula alone would be negative.
    await _setup_sensor(
        db_session,
        org_id=org_id,
        plot_id=plot_id,
        depth_cm=30,
        calibration_kind="field",
        reading_fresh=True,
        daily_value=40.0,
        target_day=target_day,
        now=now,
    )

    result = await run_daily_balance(
        plot_id=plot_id,
        day=target_day,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=SqlAlchemyWaterBalanceRepository(db_session),
        recommendations=SqlAlchemyIrrigationRecommendationRepository(db_session),
        nodes=SqlAlchemyNodeRepository(db_session),
        sensors=SqlAlchemySensorRepository(db_session),
        calibrations=SqlAlchemyCalibrationRepository(db_session),
        readings=SqlAlchemyReadingRepository(db_session),
        now=now,
    )
    await db_session.commit()

    assert not result.skipped
    assert result.balance is not None
    assert result.balance.assimilation_k == pytest.approx(0.5)
    assert 0.0 <= result.balance.depletion_mm <= result.balance.taw_mm


# --- T3: #97 daily balance use case follow-ups ---


def _core_repos(db_session: AsyncSession, *, crops: object | None = None) -> dict[str, object]:
    """The repository set every T3 test below passes, `crops` overridable for a fake."""
    return dict(
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=crops if crops is not None else SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=SqlAlchemyWaterBalanceRepository(db_session),
        recommendations=SqlAlchemyIrrigationRecommendationRepository(db_session),
    )


async def _assert_skipped_no_rows(
    db_session: AsyncSession,
    plot_id: UUID,
    target_day: date,
    reason: str,
    *,
    crops: object | None = None,
) -> None:
    result = await run_daily_balance(
        plot_id=plot_id, day=target_day, **_core_repos(db_session, crops=crops)
    )
    await db_session.commit()
    assert result.skipped
    assert result.skip_reason == reason
    assert result.balance is None
    assert result.recommendation is None
    d_minus_1 = target_day - timedelta(days=1)
    wb_repo = SqlAlchemyWaterBalanceRepository(db_session)
    rec_repo = SqlAlchemyIrrigationRecommendationRepository(db_session)
    assert await wb_repo.get_for_plot(plot_id, d_minus_1) is None
    assert await rec_repo.get_for_plot(plot_id, target_day) is None


async def test_forecast_missing_flagged_when_window_has_no_forecast_rows(
    db_session: AsyncSession,
) -> None:
    """D1 (R3-missing-forecast-silently-zero): no forecast rows for [D, D+6] keeps
    computing (not skipped) but flags forecast_missing in the rationale, instead of
    silently presenting zeroed forecast sums as trustworthy.
    """
    target_day = date(2026, 9, 25)
    _org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(db_session, day=target_day)
    # Only the D-1 observed row, no forecast rows for D..D+6 at all.

    d_minus_1 = target_day - timedelta(days=1)
    db_session.add(
        WeatherDailyRow(
            cell_id=cell_id,
            day=d_minus_1,
            is_forecast=False,
            et0_mm=Decimal("5.0"),
            rain_mm=Decimal("0.0"),
            tmin_c=Decimal("22.0"),
            tmax_c=Decimal("32.0"),
            rh_mean_pct=Decimal("65.0"),
            fetched_at=datetime.now(UTC) - timedelta(hours=2),
        )
    )
    await db_session.commit()

    result = await run_daily_balance(plot_id=plot_id, day=target_day, **_core_repos(db_session))
    await db_session.commit()

    assert not result.skipped
    assert result.recommendation is not None
    assert result.recommendation.rationale.get("forecast_missing") is True


async def test_forecast_missing_false_when_window_is_complete(db_session: AsyncSession) -> None:
    """The normal fixture populates all 7 forecast days with non-null rain/ET0."""
    target_day = date(2026, 9, 25)
    _org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(db_session, day=target_day)
    await _set_weather(db_session, cell_id, target_day)

    result = await run_daily_balance(plot_id=plot_id, day=target_day, **_core_repos(db_session))
    await db_session.commit()

    assert not result.skipped
    assert result.recommendation is not None
    assert result.recommendation.rationale.get("forecast_missing") is False


async def test_missing_et0_on_balance_day_skips_with_typed_reason(db_session: AsyncSession) -> None:
    """D1: a D-1 row with a null et0_mm and no other D-1 row to fall back to must
    skip with a typed reason, not silently compute ETc = 0.
    """
    target_day = date(2026, 9, 25)
    _org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(db_session, day=target_day)

    d_minus_1 = target_day - timedelta(days=1)
    db_session.add(
        WeatherDailyRow(
            cell_id=cell_id,
            day=d_minus_1,
            is_forecast=False,
            et0_mm=None,
            rain_mm=Decimal("0.0"),
            tmin_c=Decimal("22.0"),
            tmax_c=Decimal("32.0"),
            rh_mean_pct=Decimal("65.0"),
            fetched_at=datetime.now(UTC) - timedelta(hours=2),
        )
    )
    await db_session.commit()

    await _assert_skipped_no_rows(db_session, plot_id, target_day, "missing_et0_for_balance_day")


async def test_low_confidence_true_when_weather_fetched_over_24h_ago(
    db_session: AsyncSession,
) -> None:
    target_day = date(2026, 9, 25)
    _org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(db_session, day=target_day)
    await _set_weather(db_session, cell_id, target_day, stale_weather=True)

    result = await run_daily_balance(plot_id=plot_id, day=target_day, **_core_repos(db_session))
    await db_session.commit()

    assert not result.skipped
    assert result.recommendation is not None
    assert result.recommendation.rationale.get("low_confidence") is True


async def test_low_confidence_false_at_exactly_24h_boundary(db_session: AsyncSession) -> None:
    """R3-low-confidence-untested: fetched_at exactly 24h before `now` is NOT low
    confidence — the contract is "more than 24 h" (docs/06 §5/§6), strictly greater.
    """
    target_day = date(2026, 9, 25)
    now = datetime(2026, 9, 25, 4, 30, tzinfo=UTC)
    _org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(db_session, day=target_day)
    await _set_weather(db_session, cell_id, target_day, now=now)

    # Overwrite every row's fetched_at to exactly 24h before `now`.
    rows = (
        await db_session.execute(
            text("SELECT cell_id, day, is_forecast FROM weather_daily WHERE cell_id = :c"),
            {"c": cell_id},
        )
    ).all()
    for cell_id_, day_, is_forecast_ in rows:
        await db_session.merge(
            WeatherDailyRow(
                cell_id=cell_id_,
                day=day_,
                is_forecast=is_forecast_,
                fetched_at=now - timedelta(hours=24),
                et0_mm=Decimal("5.0"),
                rain_mm=Decimal("0.0"),
                tmin_c=Decimal("22.0"),
                tmax_c=Decimal("32.0"),
                rh_mean_pct=Decimal("65.0"),
            )
        )
    await db_session.commit()

    result = await run_daily_balance(
        plot_id=plot_id, day=target_day, now=now, **_core_repos(db_session)
    )
    await db_session.commit()

    assert not result.skipped
    assert result.recommendation is not None
    assert result.recommendation.rationale.get("low_confidence") is False


async def test_low_confidence_handles_naive_now_as_utc(db_session: AsyncSession) -> None:
    """R3-low-confidence-untested: a naive `now` (no tzinfo) is read as UTC, matching
    `local_today`'s own contract, so staleness compares against the right instant.
    """
    target_day = date(2026, 9, 25)
    aware_now = datetime(2026, 9, 25, 4, 30, tzinfo=UTC)
    naive_now = aware_now.replace(tzinfo=None)
    _org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(db_session, day=target_day)
    await _set_weather(db_session, cell_id, target_day, stale_weather=True, now=aware_now)

    result = await run_daily_balance(
        plot_id=plot_id, day=target_day, now=naive_now, **_core_repos(db_session)
    )
    await db_session.commit()

    assert not result.skipped
    assert result.recommendation is not None
    assert result.recommendation.rationale.get("low_confidence") is True


async def test_skip_crop_stage_empty(db_session: AsyncSession) -> None:
    """R3-empty-stages-indexerror: an empty stages tuple on a non-none kc_source
    crop must skip with a typed reason instead of `crop.stages[-1]` raising
    IndexError. Faked at the port for the same reason as the bad-name test.
    """
    target_day = date(2026, 9, 25)
    _org_id, plot_id, cell_id, crop_id = await _create_test_fixture(
        db_session, irrigation_system="drip", day=target_day
    )
    await _set_weather(db_session, cell_id, target_day)

    empty_stage_crop = Crop(
        id=crop_id, code="maize", name_es="Maíz", kc_source=KcSource.FAO56, stages=()
    )

    await _assert_skipped_no_rows(
        db_session,
        plot_id,
        target_day,
        "crop_stage_invalid",
        crops=_FakeCropRepositoryWithBadStage(empty_stage_crop),
    )


async def test_skip_plot_not_found(db_session: AsyncSession) -> None:
    await _assert_skipped_no_rows(db_session, uuid7(), date(2026, 9, 25), "plot_not_found")


async def test_skip_plot_has_no_weather_cell(db_session: AsyncSession) -> None:
    org_id = uuid7()
    db_session.add(OrganizationRow(id=org_id, name="Test Org", kind="individual"))
    await db_session.commit()
    farm_id = uuid7()
    db_session.add(
        FarmRow(
            id=farm_id, org_id=org_id, name="Test Farm", municipality_code="47001", location=_POINT
        )
    )
    await db_session.commit()
    plot_id = uuid7()
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="No Cell",
            boundary=_BOUNDARY,
            weather_cell_id=None,
            irrigation_system="drip",
            irrigation_efficiency=Decimal("0.90"),
            system_flow_lph=Decimal("1000.0"),
        )
    )
    await db_session.commit()

    await _assert_skipped_no_rows(
        db_session, plot_id, date(2026, 9, 25), "plot_has_no_weather_cell"
    )


async def test_skip_no_active_cycle_on_irrigated_plot(db_session: AsyncSession) -> None:
    target_day = date(2026, 9, 25)
    _org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(
        db_session, irrigation_system="drip", has_cycle=False, day=target_day
    )
    await _set_weather(db_session, cell_id, target_day)

    await _assert_skipped_no_rows(
        db_session, plot_id, target_day, "no_active_cycle_on_irrigated_plot"
    )


async def test_skip_crop_not_found(db_session: AsyncSession) -> None:
    """R3-skip-branches-untested: the crop lookup returning None (e.g. deleted
    between the cycle read and this job run) skips this plot rather than crashing.
    Faked at the port, since `crop_cycle.crop_id` has a real FK to `crop`.
    """
    target_day = date(2026, 9, 25)
    _org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(
        db_session, irrigation_system="drip", day=target_day
    )
    await _set_weather(db_session, cell_id, target_day)

    @dataclass
    class _NoCropRepository:
        async def get(self, crop_id: int) -> Crop | None:
            return None

    await _assert_skipped_no_rows(
        db_session, plot_id, target_day, "crop_not_found", crops=_NoCropRepository()
    )


async def test_skip_missing_weather_for_balance_day(db_session: AsyncSession) -> None:
    target_day = date(2026, 9, 25)
    _org_id, plot_id, _cell_id, _crop_id = await _create_test_fixture(db_session, day=target_day)
    # No weather rows created at all.

    await _assert_skipped_no_rows(
        db_session, plot_id, target_day, "missing_weather_for_balance_day"
    )


async def test_skip_cycle_not_started(db_session: AsyncSession) -> None:
    """R3-skip-branches-untested: a cycle sown after the balance day (D-1) skips
    instead of computing a negative cycle day."""
    target_day = date(2026, 9, 25)
    _org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(
        db_session, days_sown_ago=0, day=target_day
    )
    await _set_weather(db_session, cell_id, target_day)

    await _assert_skipped_no_rows(db_session, plot_id, target_day, "cycle_not_started")


async def test_skip_soil_incomplete_missing_wilting_point(db_session: AsyncSession) -> None:
    target_day = date(2026, 9, 25)
    _org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(
        db_session, has_soil=False, day=target_day
    )
    db_session.add(
        SoilProfileRow(
            plot_id=plot_id,
            source="lab",
            texture="sandy_loam",
            field_capacity_pct=Decimal("23.0"),
            wilting_point_pct=None,
            root_depth_cm=Decimal("60.0"),
        )
    )
    await db_session.commit()
    await _set_weather(db_session, cell_id, target_day)

    await _assert_skipped_no_rows(db_session, plot_id, target_day, "soil_profile_incomplete")


async def test_skip_soil_incomplete_missing_root_depth(db_session: AsyncSession) -> None:
    target_day = date(2026, 9, 25)
    _org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(
        db_session, has_soil=False, day=target_day
    )
    db_session.add(
        SoilProfileRow(
            plot_id=plot_id,
            source="lab",
            texture="sandy_loam",
            field_capacity_pct=Decimal("23.0"),
            wilting_point_pct=Decimal("9.0"),
            root_depth_cm=None,
        )
    )
    await db_session.commit()
    await _set_weather(db_session, cell_id, target_day)

    await _assert_skipped_no_rows(db_session, plot_id, target_day, "soil_profile_incomplete")


# --- Issue #116 round 2 findings ---


async def test_et0_fallback_to_forecast_flags_missing_observed_weather(
    db_session: AsyncSession,
) -> None:
    """R3-et0-fallback-untested-and-unflagged: D-1 has both an observed row (used
    for rain) and a forecast row; the observed row's et0_mm is null, so ET0 comes
    from the forecast row instead. docs/06 §5 requires the fallback be noted in
    the rationale, so missing_observed_weather must be True even though the
    observed row itself was found (it just could not supply et0).
    """
    target_day = date(2026, 9, 25)
    now = datetime(2026, 9, 25, 4, 30, tzinfo=UTC)
    _org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(db_session, day=target_day)
    await _set_weather(db_session, cell_id, target_day, rain_d_minus_1=2.0, now=now)

    d_minus_1 = target_day - timedelta(days=1)
    fetched_at = now - timedelta(hours=2)
    # Overwrite the D-1 observed row: et0_mm -> null, rain_mm stays.
    await db_session.merge(
        WeatherDailyRow(
            cell_id=cell_id,
            day=d_minus_1,
            is_forecast=False,
            et0_mm=None,
            rain_mm=Decimal("2.0"),
            tmin_c=Decimal("22.0"),
            tmax_c=Decimal("32.0"),
            rh_mean_pct=Decimal("65.0"),
            fetched_at=fetched_at,
        )
    )
    # Add a D-1 forecast row: the fallback source for et0.
    db_session.add(
        WeatherDailyRow(
            cell_id=cell_id,
            day=d_minus_1,
            is_forecast=True,
            et0_mm=Decimal("4.5"),
            rain_mm=Decimal("1.0"),
            tmin_c=Decimal("23.0"),
            tmax_c=Decimal("33.0"),
            rh_mean_pct=Decimal("60.0"),
            fetched_at=fetched_at,
        )
    )
    await db_session.commit()

    result = await run_daily_balance(
        plot_id=plot_id, day=target_day, now=now, **_core_repos(db_session)
    )
    await db_session.commit()

    assert not result.skipped
    assert result.recommendation is not None
    assert result.recommendation.rationale.get("missing_observed_weather") is True
    assert result.recommendation.rationale.get("et0_mm") == pytest.approx(4.5)


async def test_forecast_missing_true_when_a_single_day_is_missing_at_d_plus_6(
    db_session: AsyncSession,
) -> None:
    """R3-forecast-missing-partial-window-untested: a gap at the far edge of the
    window (D+6) must flag forecast_missing, not only a fully-empty window.
    """
    target_day = date(2026, 9, 25)
    _org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(db_session, day=target_day)
    await _set_weather(db_session, cell_id, target_day)
    d_plus_6 = target_day + timedelta(days=6)
    await db_session.execute(
        text("DELETE FROM weather_daily WHERE cell_id = :c AND day = :d AND is_forecast"),
        {"c": cell_id, "d": d_plus_6},
    )
    await db_session.commit()

    result = await run_daily_balance(plot_id=plot_id, day=target_day, **_core_repos(db_session))
    await db_session.commit()

    assert not result.skipped
    assert result.recommendation is not None
    assert result.recommendation.rationale.get("forecast_missing") is True


async def test_forecast_missing_true_when_a_forecast_row_has_null_rain(
    db_session: AsyncSession,
) -> None:
    """R3-forecast-missing-partial-window-untested: a present forecast row with a
    null rain_mm must still flag forecast_missing, not just an absent row."""
    target_day = date(2026, 9, 25)
    now = datetime(2026, 9, 25, 4, 30, tzinfo=UTC)
    _org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(db_session, day=target_day)
    await _set_weather(db_session, cell_id, target_day, now=now)
    fetched_at = now - timedelta(hours=2)
    await db_session.merge(
        WeatherDailyRow(
            cell_id=cell_id,
            day=target_day,
            is_forecast=True,
            et0_mm=Decimal("5.0"),
            rain_mm=None,
            tmin_c=Decimal("23.0"),
            tmax_c=Decimal("33.0"),
            rh_mean_pct=Decimal("60.0"),
            fetched_at=fetched_at,
        )
    )
    await db_session.commit()

    result = await run_daily_balance(
        plot_id=plot_id, day=target_day, now=now, **_core_repos(db_session)
    )
    await db_session.commit()

    assert not result.skipped
    assert result.recommendation is not None
    assert result.recommendation.rationale.get("forecast_missing") is True


async def test_forecast_missing_true_when_a_forecast_row_has_null_et0(
    db_session: AsyncSession,
) -> None:
    """R3-forecast-missing-partial-window-untested: a present forecast row with a
    null et0_mm must still flag forecast_missing, not just an absent row."""
    target_day = date(2026, 9, 25)
    now = datetime(2026, 9, 25, 4, 30, tzinfo=UTC)
    _org_id, plot_id, cell_id, _crop_id = await _create_test_fixture(db_session, day=target_day)
    await _set_weather(db_session, cell_id, target_day, now=now)
    fetched_at = now - timedelta(hours=2)
    await db_session.merge(
        WeatherDailyRow(
            cell_id=cell_id,
            day=target_day,
            is_forecast=True,
            et0_mm=None,
            rain_mm=Decimal("0.0"),
            tmin_c=Decimal("23.0"),
            tmax_c=Decimal("33.0"),
            rh_mean_pct=Decimal("60.0"),
            fetched_at=fetched_at,
        )
    )
    await db_session.commit()

    result = await run_daily_balance(
        plot_id=plot_id, day=target_day, now=now, **_core_repos(db_session)
    )
    await db_session.commit()

    assert not result.skipped
    assert result.recommendation is not None
    assert result.recommendation.rationale.get("forecast_missing") is True
