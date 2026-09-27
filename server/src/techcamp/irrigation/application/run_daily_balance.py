"""Daily water balance application use case (docs/06 §5; ADR-0022; ADR-0023).

Computes the water balance row for D-1 and stores the irrigation recommendation for D.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

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
    K_ASSIMILATION_DEFAULT,
    K_ASSIMILATION_NONE,
    IrrigationRecommendation,
    KcSourceCode,
    WaterBalanceDay,
    assimilate_depletion,
    compute_adjusted_p,
    compute_effective_rain,
    compute_etc,
    compute_kc_for_cycle_day,
    compute_model_depletion,
    compute_observed_depletion,
    compute_raw,
    compute_stress_moisture,
    compute_taw,
    decide_recommendation,
    is_sensor_depth_representative,
    stage_for_cycle_day,
)
from techcamp.telemetry.application.ports import (
    CalibrationRepository,
    NodeRepository,
    ReadingRepository,
    SensorRepository,
)
from techcamp.telemetry.domain.models import CalibrationKind
from techcamp.weather.application.ports import WeatherRepository
from techcamp.weather.domain.models import WeatherDay

_KC_SOURCE_CODES: Mapping[KcSource, KcSourceCode] = {
    KcSource.FAO56: "fao56",
    KcSource.LOCAL: "local",
    KcSource.APPROXIMATE: "approximate",
    KcSource.NONE: "none",
}
"""Translation from the farms `KcSource` enum to the irrigation domain's
`KcSourceCode`, the plain string the rationale persists (docs/06 §5).

Declared here, in the one layer allowed to know both modules, because mypy widens
`KcSource.value` to `str` and would reject it against `KcSourceCode`. Spelling
the table out keeps the compiler checking both sides instead of silencing the
error with a `cast`. mypy cannot enforce exhaustiveness, so a member added to
`KcSource` later raises `KeyError` here rather than persisting a wrong code."""


@dataclass(frozen=True, slots=True)
class ForecastSummary:
    """Forecast totals and freshness the recommendation decision reads.

    Covers the `[D, D+6]` forecast window of the recommendation day `D`, from
    the already-fetched daily weather rows (docs/06 §5; E5 handoff). The rows
    themselves differ per branch (`from_day` is `D` or `D-1`), so the window is
    filtered here rather than in the query.
    """

    rain_48h_mm: float
    rain_7d_mm: float
    et0_7d_mm: float
    low_confidence: bool


def _summarize_forecast(
    weather_rows: Sequence[WeatherDay],
    *,
    d_rec: date,
    now: datetime,
) -> ForecastSummary:
    """Sum the 48 h and 7-day forecast rain, the 7-day forecast ET0, and flag
    rows fetched more than 24 h ago as low confidence (docs/06 §5, §6).
    """
    d_plus_6 = d_rec + timedelta(days=6)
    return ForecastSummary(
        rain_48h_mm=sum(
            float(r.rain_mm)
            for r in weather_rows
            if r.is_forecast
            and r.day in (d_rec, d_rec + timedelta(days=1))
            and r.rain_mm is not None
        ),
        rain_7d_mm=sum(
            float(r.rain_mm)
            for r in weather_rows
            if r.is_forecast and d_rec <= r.day <= d_plus_6 and r.rain_mm is not None
        ),
        et0_7d_mm=sum(
            float(r.et0_mm)
            for r in weather_rows
            if r.is_forecast and d_rec <= r.day <= d_plus_6 and r.et0_mm is not None
        ),
        low_confidence=any(
            (now - r.fetched_at) > timedelta(hours=24)
            for r in weather_rows
            if r.fetched_at is not None
        ),
    )


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
        forecast = _summarize_forecast(weather_rows, d_rec=d_rec, now=now)
        rec = decide_recommendation(
            has_active_cycle=False,
            is_rainfed=True,
            kc_source="none",
            dr=0.0,
            raw=0.0,
            irrigation_efficiency=None,
            area_m2=plot.area_ha * 10000.0,
            system_flow_lph=None,
            forecast_rain_48h_mm=forecast.rain_48h_mm,
            forecast_rain_7d_mm=forecast.rain_7d_mm,
            forecast_et0_7d_mm=forecast.et0_7d_mm,
            stage="",
            rationale_context={"low_confidence": forecast.low_confidence},
        )
        saved_rec: IrrigationRecommendation | None = None
        if rec is not None:
            saved_rec = await recommendations.upsert(rec, plot_id=plot.id, day=d_rec)
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
    if crop.kc_source is KcSource.NONE:
        weather_rows = await weather.list_daily(
            plot.weather_cell_id, from_day=d_balance, to_day=d_rec + timedelta(days=6)
        )
        forecast = _summarize_forecast(weather_rows, d_rec=d_rec, now=now)
        rec = decide_recommendation(
            has_active_cycle=True,
            is_rainfed=(plot.irrigation_system == IrrigationSystem.NONE),
            kc_source=_KC_SOURCE_CODES[crop.kc_source],
            dr=0.0,
            raw=0.0,
            irrigation_efficiency=plot.irrigation_efficiency,
            area_m2=plot.area_ha * 10000.0,
            system_flow_lph=plot.system_flow_lph,
            forecast_rain_48h_mm=forecast.rain_48h_mm,
            forecast_rain_7d_mm=forecast.rain_7d_mm,
            forecast_et0_7d_mm=forecast.et0_7d_mm,
            stage="",
            rationale_context={"low_confidence": forecast.low_confidence},
        )
        saved_rec = None
        if rec is not None:
            saved_rec = await recommendations.upsert(rec, plot_id=plot.id, day=d_rec)
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

    forecast = _summarize_forecast(weather_rows, d_rec=d_rec, now=now)

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

    k = K_ASSIMILATION_NONE
    dr_assimilated = dr_model
    soil_moisture_obs_pct: float | None = None

    if (
        nodes is not None
        and sensors is not None
        and calibrations is not None
        and readings is not None
    ):
        node_list = await nodes.list_for_org(plot.org_id, plot_id=plot.id, limit=500)
        candidate_sensors: list[tuple[float, float]] = []
        daily_start = datetime(d_balance.year, d_balance.month, d_balance.day, tzinfo=UTC)
        daily_end = daily_start + timedelta(days=1)

        for node in node_list:
            node_sensors = await sensors.list_for_node(node.id, plot.org_id)
            for sensor in node_sensors:
                if sensor.metric != "soil_moisture" or sensor.depth_cm is None:
                    continue

                # 1. Reading in the last 24 h before the run
                recent = await readings.query_raw(
                    sensor.id, start=now - timedelta(hours=24), end=now
                )
                if not recent:
                    continue

                # 2. Latest valid calibration kind `field`
                cal = await calibrations.get_latest_valid_at(sensor.id, plot.org_id, at=now)
                if cal is None:
                    continue
                if cal.kind is not CalibrationKind.FIELD:
                    continue

                # 3. Daily mean of D-1 from query_daily
                daily_points = await readings.query_daily(
                    sensor.id, start=daily_start, end=daily_end
                )
                if not daily_points:
                    continue

                candidate_sensors.append((float(sensor.depth_cm), daily_points[0].value))

        root_depth_cm = float(soil.root_depth_cm)
        if len(candidate_sensors) in (1, 2):
            sensor_depths = [depth for depth, _ in candidate_sensors]
            if is_sensor_depth_representative(sensor_depths, root_depth_cm):
                k = K_ASSIMILATION_DEFAULT
                theta_obs_pct = sum(mean for _, mean in candidate_sensors) / len(candidate_sensors)
                theta_obs = theta_obs_pct / 100.0
                dr_obs = compute_observed_depletion(fc, theta_obs, root_depth_m)
                dr_assimilated = assimilate_depletion(dr_model, dr_obs, k)
                soil_moisture_obs_pct = theta_obs_pct

    rec = decide_recommendation(
        has_active_cycle=True,
        is_rainfed=(plot.irrigation_system == IrrigationSystem.NONE),
        kc_source=_KC_SOURCE_CODES[crop.kc_source],
        dr=dr_assimilated,
        raw=raw,
        irrigation_efficiency=plot.irrigation_efficiency,
        area_m2=plot.area_ha * 10000.0,
        system_flow_lph=plot.system_flow_lph,
        forecast_rain_48h_mm=forecast.rain_48h_mm,
        forecast_rain_7d_mm=forecast.rain_7d_mm,
        forecast_et0_7d_mm=forecast.et0_7d_mm,
        stage=stage,
        rationale_context={
            "et0_mm": et0_d_minus_1,
            "kc": kc,
            "p": p,
            "taw_mm": taw,
            "dr_model": dr_model,
            "k": k,
            "low_confidence": forecast.low_confidence,
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

    return DailyBalanceResult(balance=balance_day, recommendation=saved_rec)
