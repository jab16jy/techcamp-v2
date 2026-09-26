"""Daily water balance application use case (docs/06 §5; ADR-0022; ADR-0023).

Computes the water balance row for D-1 and stores the irrigation recommendation for D.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.application.ports import (
    CropCycleRepository,
    CropRepository,
    PlotRepository,
    SoilProfileRepository,
)
from techcamp.farms.domain.models import IrrigationSystem, KcSource
from techcamp.irrigation.application.ports import (
    IrrigationRecommendationRepository,
    WaterBalanceRepository,
)
from techcamp.irrigation.domain.models import (
    IrrigationRecommendation,
    WaterBalanceDay,
    compute_adjusted_p,
    compute_effective_rain,
    compute_etc,
    compute_kc_for_cycle_day,
    compute_model_depletion,
    compute_raw,
    compute_stress_moisture,
    compute_taw,
    decide_recommendation,
    stage_for_cycle_day,
)
from techcamp.telemetry.application.ports import (
    CalibrationRepository,
    NodeRepository,
    ReadingRepository,
    SensorRepository,
)
from techcamp.weather.application.ports import WeatherRepository


@dataclass(frozen=True, slots=True)
class DailyBalanceResult:
    balance: WaterBalanceDay | None
    recommendation: IrrigationRecommendation | None
    skipped: bool = False
    skip_reason: str | None = None


async def run_daily_balance(
    *,
    plot_id: UUID,
    day: date,
    session: AsyncSession,
    plots: PlotRepository,
    crop_cycles: CropCycleRepository,
    crops: CropRepository,
    soil_profiles: SoilProfileRepository,
    weather: WeatherRepository,
    water_balances: WaterBalanceRepository,
    recommendations: IrrigationRecommendationRepository,
    nodes: NodeRepository | None = None,
    sensors: SensorRepository | None = None,
    calibrations: CalibrationRepository | None = None,
    readings: ReadingRepository | None = None,
    now: datetime | None = None,
) -> DailyBalanceResult:
    """Execute the daily water balance and recommendation use case for a plot."""
    plot = await plots.get_by_id(plot_id)
    if plot is None:
        return DailyBalanceResult(
            balance=None,
            recommendation=None,
            skipped=True,
            skip_reason="plot_not_found",
        )

    if plot.weather_cell_id is None:
        return DailyBalanceResult(
            balance=None,
            recommendation=None,
            skipped=True,
            skip_reason="plot_has_no_weather_cell",
        )

    if now is None:
        now = datetime.now(UTC)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    d_balance = day - timedelta(days=1)
    d_rec = day

    cycle = await crop_cycles.get_active_for_plot(plot_id)

    # Branch: no active crop cycle
    if cycle is None:
        if plot.irrigation_system != IrrigationSystem.NONE:
            return DailyBalanceResult(
                balance=None,
                recommendation=None,
                skipped=True,
                skip_reason="no_active_cycle_on_irrigated_plot",
            )
        # Rainfed plot without cycle -> delay_sowing recommendation
        weather_rows = await weather.list_daily(
            plot.weather_cell_id, from_day=d_rec, to_day=d_rec + timedelta(days=6)
        )
        rain_48h = sum(
            float(r.rain_mm)
            for r in weather_rows
            if r.is_forecast
            and r.day in (d_rec, d_rec + timedelta(days=1))
            and r.rain_mm is not None
        )
        rain_7d = sum(
            float(r.rain_mm)
            for r in weather_rows
            if r.is_forecast
            and d_rec <= r.day <= d_rec + timedelta(days=6)
            and r.rain_mm is not None
        )
        et0_7d = sum(
            float(r.et0_mm)
            for r in weather_rows
            if r.is_forecast
            and d_rec <= r.day <= d_rec + timedelta(days=6)
            and r.et0_mm is not None
        )
        is_low_confidence = any(
            (now - r.fetched_at) > timedelta(hours=24)
            for r in weather_rows
            if r.fetched_at is not None
        )
        rec = decide_recommendation(
            has_active_cycle=False,
            is_rainfed=True,
            kc_source="none",
            dr=0.0,
            raw=0.0,
            irrigation_efficiency=None,
            area_m2=plot.area_ha * 10000.0,
            system_flow_lph=None,
            forecast_rain_48h_mm=rain_48h,
            forecast_rain_7d_mm=rain_7d,
            forecast_et0_7d_mm=et0_7d,
            stage="",
            rationale_context={"low_confidence": is_low_confidence},
        )
        saved_rec: IrrigationRecommendation | None = None
        if rec is not None:
            saved_rec = await recommendations.upsert(rec, plot_id=plot.id, day=d_rec)
        await session.commit()
        return DailyBalanceResult(balance=None, recommendation=saved_rec)

    # Active crop cycle exists
    crop = await crops.get(cycle.crop_id)
    if crop is None:
        return DailyBalanceResult(
            balance=None,
            recommendation=None,
            skipped=True,
            skip_reason="crop_not_found",
        )

    # Branch: kc_source is none -> no_kc recommendation, skip balance row
    if crop.kc_source is KcSource.NONE or str(crop.kc_source).lower() == "none":
        weather_rows = await weather.list_daily(
            plot.weather_cell_id, from_day=d_balance, to_day=d_rec + timedelta(days=6)
        )
        rain_48h = sum(
            float(r.rain_mm)
            for r in weather_rows
            if r.is_forecast
            and r.day in (d_rec, d_rec + timedelta(days=1))
            and r.rain_mm is not None
        )
        rain_7d = sum(
            float(r.rain_mm)
            for r in weather_rows
            if r.is_forecast
            and d_rec <= r.day <= d_rec + timedelta(days=6)
            and r.rain_mm is not None
        )
        et0_7d = sum(
            float(r.et0_mm)
            for r in weather_rows
            if r.is_forecast
            and d_rec <= r.day <= d_rec + timedelta(days=6)
            and r.et0_mm is not None
        )
        is_low_confidence = any(
            (now - r.fetched_at) > timedelta(hours=24)
            for r in weather_rows
            if r.fetched_at is not None
        )
        rec = decide_recommendation(
            has_active_cycle=True,
            is_rainfed=(plot.irrigation_system == IrrigationSystem.NONE),
            kc_source=crop.kc_source,
            dr=0.0,
            raw=0.0,
            irrigation_efficiency=plot.irrigation_efficiency,
            area_m2=plot.area_ha * 10000.0,
            system_flow_lph=plot.system_flow_lph,
            forecast_rain_48h_mm=rain_48h,
            forecast_rain_7d_mm=rain_7d,
            forecast_et0_7d_mm=et0_7d,
            stage="",
            rationale_context={"low_confidence": is_low_confidence},
        )
        saved_rec = None
        if rec is not None:
            saved_rec = await recommendations.upsert(rec, plot_id=plot.id, day=d_rec)
        await session.commit()
        return DailyBalanceResult(balance=None, recommendation=saved_rec)

    # Soil completeness check
    soil = await soil_profiles.get_for_plot(plot_id)
    if (
        soil is None
        or soil.field_capacity_pct is None
        or soil.wilting_point_pct is None
        or soil.root_depth_cm is None
    ):
        return DailyBalanceResult(
            balance=None,
            recommendation=None,
            skipped=True,
            skip_reason="soil_profile_incomplete",
        )

    # Fetch weather rows for [D-1, D+6]
    weather_rows = await weather.list_daily(
        plot.weather_cell_id, from_day=d_balance, to_day=d_rec + timedelta(days=6)
    )

    obs_row = next((r for r in weather_rows if r.day == d_balance and not r.is_forecast), None)
    forecast_row_d_minus_1 = next(
        (r for r in weather_rows if r.day == d_balance and r.is_forecast), None
    )

    if obs_row is not None:
        weather_d_minus_1 = obs_row
        missing_observed = False
    elif forecast_row_d_minus_1 is not None:
        weather_d_minus_1 = forecast_row_d_minus_1
        missing_observed = True
    else:
        return DailyBalanceResult(
            balance=None,
            recommendation=None,
            skipped=True,
            skip_reason="missing_weather_for_balance_day",
        )

    et0_d_minus_1 = float(weather_d_minus_1.et0_mm) if weather_d_minus_1.et0_mm is not None else 0.0
    rain_d_minus_1 = (
        float(weather_d_minus_1.rain_mm) if weather_d_minus_1.rain_mm is not None else 0.0
    )

    forecast_rain_48h = sum(
        float(r.rain_mm)
        for r in weather_rows
        if r.is_forecast and r.day in (d_rec, d_rec + timedelta(days=1)) and r.rain_mm is not None
    )
    forecast_rain_7d = sum(
        float(r.rain_mm)
        for r in weather_rows
        if r.is_forecast and d_rec <= r.day <= d_rec + timedelta(days=6) and r.rain_mm is not None
    )
    forecast_et0_7d = sum(
        float(r.et0_mm)
        for r in weather_rows
        if r.is_forecast and d_rec <= r.day <= d_rec + timedelta(days=6) and r.et0_mm is not None
    )

    is_low_confidence = any(
        (now - r.fetched_at) > timedelta(hours=24) for r in weather_rows if r.fetched_at is not None
    )

    # Balance calculation for D-1
    day_of_cycle = (d_balance - cycle.sown_on).days + 1
    if day_of_cycle < 1:
        return DailyBalanceResult(
            balance=None,
            recommendation=None,
            skipped=True,
            skip_reason="cycle_not_started",
        )

    fc = float(soil.field_capacity_pct) / 100.0
    wp = float(soil.wilting_point_pct) / 100.0
    root_depth_m = float(soil.root_depth_cm) / 100.0
    taw = compute_taw(fc, wp, root_depth_m)
    stage = stage_for_cycle_day(crop.stages, day_of_cycle)
    kc = compute_kc_for_cycle_day(crop.stages, day_of_cycle)
    etc = compute_etc(kc, et0_d_minus_1)

    stage_obj = next((s for s in crop.stages if s.stage == stage), crop.stages[-1])
    p = compute_adjusted_p(stage_obj.depletion_fraction_p, etc)
    raw = compute_raw(p, taw)
    stress_moisture = compute_stress_moisture(fc, wp, p)
    pe = compute_effective_rain(rain_d_minus_1)
    irrigation_mm = 0.0

    d_prev = d_balance - timedelta(days=1)
    prev_balance = await water_balances.get_for_plot(plot_id, d_prev)
    dr_prev = prev_balance.depletion_mm if prev_balance is not None else 0.0
    dr_model = compute_model_depletion(dr_prev, etc, pe, irrigation_mm, taw)

    k = 0.0
    dr_assimilated = dr_model
    soil_moisture_obs_pct: float | None = None

    rec = decide_recommendation(
        has_active_cycle=True,
        is_rainfed=(plot.irrigation_system == IrrigationSystem.NONE),
        kc_source=crop.kc_source,
        dr=dr_assimilated,
        raw=raw,
        irrigation_efficiency=plot.irrigation_efficiency,
        area_m2=plot.area_ha * 10000.0,
        system_flow_lph=plot.system_flow_lph,
        forecast_rain_48h_mm=forecast_rain_48h,
        forecast_rain_7d_mm=forecast_rain_7d,
        forecast_et0_7d_mm=forecast_et0_7d,
        stage=stage,
        rationale_context={
            "et0_mm": et0_d_minus_1,
            "kc": kc,
            "p": p,
            "taw_mm": taw,
            "dr_model": dr_model,
            "k": k,
            "low_confidence": is_low_confidence,
            "missing_observed_weather": missing_observed,
        },
    )

    balance_day = WaterBalanceDay(
        plot_id=plot.id,
        day=d_balance,
        etc_mm=etc,
        effective_rain_mm=pe,
        irrigation_mm=irrigation_mm,
        taw_mm=taw,
        raw_mm=raw,
        depletion_model_mm=dr_model,
        depletion_mm=dr_assimilated,
        soil_moisture_obs_pct=soil_moisture_obs_pct,
        assimilation_k=k,
        stress_moisture_pct=stress_moisture * 100.0,
    )

    await water_balances.upsert(balance_day)
    saved_rec = None
    if rec is not None:
        saved_rec = await recommendations.upsert(rec, plot_id=plot.id, day=d_rec)
    await session.commit()

    return DailyBalanceResult(balance=balance_day, recommendation=saved_rec)
