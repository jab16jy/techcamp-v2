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

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import CropCycleRow, FarmRow, PlotRow, SoilProfileRow
from techcamp.farms.adapters.repositories import (
    SqlAlchemyCropCycleRepository,
    SqlAlchemyCropRepository,
    SqlAlchemyPlotRepository,
    SqlAlchemySoilProfileRepository,
)
from techcamp.farms.domain.models import CropCycleStatus
from techcamp.identity.adapters.orm import OrganizationRow
from techcamp.irrigation.adapters.repositories import (
    SqlAlchemyIrrigationRecommendationRepository,
    SqlAlchemyWaterBalanceRepository,
)
from techcamp.irrigation.application.run_daily_balance import run_daily_balance
from techcamp.irrigation.domain.models import RainfedAdvice, RecommendationKind, WaterBalanceDay
from techcamp.shared.ids import uuid7
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
        session=db_session,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=wb_repo,
        recommendations=SqlAlchemyIrrigationRecommendationRepository(db_session),
    )

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
        session=db_session,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=wb_repo,
        recommendations=SqlAlchemyIrrigationRecommendationRepository(db_session),
    )

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
        session=db_session,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=SqlAlchemyWaterBalanceRepository(db_session),
        recommendations=SqlAlchemyIrrigationRecommendationRepository(db_session),
    )

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
        session=db_session,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=wb_repo,
        recommendations=SqlAlchemyIrrigationRecommendationRepository(db_session),
    )

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
        session=db_session,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=wb_repo,
        recommendations=rec_repo,
    )

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
        session=db_session,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=wb_repo,
        recommendations=rec_repo,
    )

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
        session=db_session,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=wb_repo,
        recommendations=rec_repo,
    )

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
        session=db_session,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=wb_repo,
        recommendations=SqlAlchemyIrrigationRecommendationRepository(db_session),
    )

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
        session=db_session,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=SqlAlchemyWaterBalanceRepository(db_session),
        recommendations=SqlAlchemyIrrigationRecommendationRepository(db_session),
    )

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
        session=db_session,
        plots=SqlAlchemyPlotRepository(db_session),
        crop_cycles=SqlAlchemyCropCycleRepository(db_session),
        crops=SqlAlchemyCropRepository(db_session),
        soil_profiles=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        water_balances=wb_repo,
        recommendations=rec_repo,
    )

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
