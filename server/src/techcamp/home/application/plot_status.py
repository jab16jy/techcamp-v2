"""`GET /plots/{plot_id}/status` — the home screen in one request.

docs/04-api.md §Estado de la parcela (pantalla principal) and its field rule
table; docs/05-arquitectura.md §Módulos (C4 nivel 3) and D-T0.1; docs/06 §5 for
the representative sensor; ADR-0023 for the rainfed plot; docs/11-metricas.md
and D-T0.13 for `digital_adoption_index` (E11 T8).

The module is read-only, owns no tables, and nothing depends on it. It reaches
the other five modules through their public `application` facades only —
never their `domain` or their `adapters` (docs/05's dependency rules), which
is why `Alert`, `Plot`, `Crop` and the errors this module does not own are
projected into the dataclasses below instead of being re-exported.

`metrics` is reached the same way, only the import names the module instead of
the package: `metrics/application/__init__.py` is empty because T3, T4 and T5
each shipped their own slice of that facade (E11). `get_latest_plot_month` and
its `MonthlyMetricRepository` port come from `metrics.application.adoption`, and
the domain's `PlotMonthlyMetric` is only inferred, never imported: the stored row
stays behind the repository, like every other source this module reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from techcamp.alerts.application import list_open_alerts_for_plots
from techcamp.alerts.application.ports import AlertRepository
from techcamp.farms.application.manage_plots import resolve_plot_access
from techcamp.farms.application.ports import (
    CropCycleRepository,
    CropRepository,
    PlotRepository,
    SoilProfileRepository,
)
from techcamp.identity.application.ports import MembershipRepository
from techcamp.irrigation.application import (
    PlotWaterBalanceDay,
    RecommendationNotFoundError,
    crop_stage_for_day,
    query_plot_recommendation,
    query_plot_water_balance,
    representative_soil_moisture_sensors,
)
from techcamp.irrigation.application.ports import (
    IrrigationRecommendationRepository,
    WaterBalanceRepository,
)
from techcamp.metrics.application.adoption import (
    MonthlyMetricRepository,
    get_latest_plot_month,
)
from techcamp.shared.dates import local_today
from techcamp.telemetry.application import (
    PlotNodeHealth,
    ReadingPoint,
    get_plot_nodes_health,
    query_latest_plot_readings,
)
from techcamp.telemetry.application.ports import (
    CalibrationRepository,
    NodeRepository,
    ReadingRepository,
    SensorRepository,
)
from techcamp.weather.application import PlotWeatherDay, query_plot_weather
from techcamp.weather.application.ports import WeatherRepository

LATEST_METRICS = ("soil_moisture", "air_temp", "air_rh")
"""The three metrics `latest` carries (docs/04 §Estado)."""

LATEST_WINDOW = timedelta(hours=24)
"""D-T0.4: per metric, the newest valid reading within the last 24 h."""

FORECAST_DAYS = 3
"""`weather_next_3d`: today and the two days after (docs/04 §Estado)."""


@dataclass(frozen=True, slots=True)
class PlotSummary:
    """The docs/04 `plot` field: the plot's own identity and its irrigation
    system, which is what the home screen renders (the active plot of D-T0.7
    and the rainfed variant of ADR-0023). No boundary: the home screen draws
    no geometry, and this payload exists to be one request on 3G."""

    id: UUID
    org_id: UUID
    farm_id: UUID
    name: str
    area_ha: float
    irrigation_system: str


@dataclass(frozen=True, slots=True)
class CropSummary:
    """The crop of an active cycle. The web maps `code` to its Spanish label
    (`name_es` is the catalog's own)."""

    id: int
    code: str
    name_es: str


@dataclass(frozen=True, slots=True)
class ActiveCycleSummary:
    """`stage` and `day_of_cycle` are both null when the sowing is later than
    today: there is no cycle day yet (D-T0.6)."""

    crop: CropSummary
    stage: str | None
    day_of_cycle: int | None


@dataclass(frozen=True, slots=True)
class LatestReadings:
    """`at` is the time of the newest value RETURNED, null when every metric is
    null (D-T0.4). No metric without a reading is a zero."""

    soil_moisture_pct: float | None
    air_temp_c: float | None
    air_rh_pct: float | None
    at: datetime | None


@dataclass(frozen=True, slots=True)
class WaterBalanceSummary:
    """Yesterday's consolidated balance.

    docs/04 §Estado: "El último balance diario consolidado (el de ayer); `null`
    si no hay." The payload carries no `day` of its own, so serving an older
    row would present a stale depletion and its `status` as today's truth — a
    plot whose job has not run answers `null` instead.
    """

    depletion_mm: float
    taw_mm: float
    raw_mm: float
    stress_moisture_pct: float
    status: str
    """`compute_water_balance_status`, already rainfed-aware: on a rainfed
    plot `Dr > RAW` is `stress` and never `irrigate` (ADR-0022, ADR-0023)."""


@dataclass(frozen=True, slots=True)
class RecommendationSummary:
    kind: str
    depth_mm: float | None
    duration_min: int | None
    advice: list[str]
    rationale: dict[str, Any]
    """The stored calculation's own object, unchanged (D-T0.11): it carries
    `forecast_rain_7d_mm` and the rest of the numeric evidence the web writes
    the "why" from."""


@dataclass(frozen=True, slots=True)
class OpenAlert:
    """One alert of the plot with `state <> 'resolved'` (D-T0.3)."""

    id: UUID
    org_id: UUID
    rule_id: UUID
    rule_code: str
    plot_id: UUID | None
    node_id: UUID | None
    state: str
    severity: str
    evidence: dict[str, Any]
    opened_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    escalated_at: datetime | None
    resolution_note: str | None


@dataclass(frozen=True, slots=True)
class DigitalAdoption:
    """The docs/04 `digital_adoption_index` field: the stored index with the
    month it belongs to, so the home screen can write "Adopción digital: 72 ·
    septiembre" (docs/07 §Inicio) without guessing which month it read.

    `None` in the payload, never a zero: a plot whose job has not run, and a
    month with no evidence at all, are both missing data (D-T0.2).
    """

    value: Decimal
    """`Decimal` from `metrics`, kept exact: the stored 0-100 index crosses the
    wire unchanged and is rounded only where it is displayed."""
    month: date
    """The month's first day, the `plot_metric_monthly` bucket (D-T0.2)."""


@dataclass(frozen=True, slots=True)
class PlotStatus:
    plot: PlotSummary
    active_cycle: ActiveCycleSummary | None
    latest: LatestReadings
    water_balance: WaterBalanceSummary | None
    recommendation: RecommendationSummary | None
    open_alerts: list[OpenAlert]
    weather_next_3d: list[PlotWeatherDay]
    nodes: list[PlotNodeHealth]
    digital_adoption_index: DigitalAdoption | None
    """The plot's latest stored month (D-T0.13); `None` when it has none yet."""


async def build_plot_status(
    *,
    user_id: UUID,
    plot_id: UUID,
    now: datetime,
    plots: PlotRepository,
    memberships: MembershipRepository,
    cycles: CropCycleRepository,
    crops: CropRepository,
    soil: SoilProfileRepository,
    nodes: NodeRepository,
    sensors: SensorRepository,
    calibrations: CalibrationRepository,
    readings: ReadingRepository,
    water_balances: WaterBalanceRepository,
    recommendations: IrrigationRecommendationRepository,
    weather: WeatherRepository,
    alerts: AlertRepository,
    metrics: MonthlyMetricRepository,
) -> PlotStatus:
    """Compose the whole home payload for one plot.

    Raises `farms`' `PlotNotFoundError` for a plot that does not exist or is in
    another organization, exactly like every other `/plots/{plot_id}` route
    (docs/09-cuellos-de-botella.md#seguridad): the caller cannot tell the two
    cases apart, and the adapter maps it to 404.
    """
    plot, _role = await resolve_plot_access(
        user_id=user_id, plot_id=plot_id, plots=plots, memberships=memberships
    )
    today = local_today(now)
    window_start = now - LATEST_WINDOW
    yesterday = today - timedelta(days=1)

    latest = await _latest(
        plot_id=plot.id,
        org_id=plot.org_id,
        window_start=window_start,
        now=now,
        soil=soil,
        nodes=nodes,
        sensors=sensors,
        calibrations=calibrations,
        readings=readings,
    )

    balances = await query_plot_water_balance(
        user_id=user_id,
        plot_id=plot.id,
        from_day=yesterday,
        to_day=yesterday,
        now=now,
        plots=plots,
        water_balances=water_balances,
        memberships=memberships,
    )
    recommendation = await _recommendation(
        user_id=user_id,
        plot_id=plot.id,
        day=today,
        now=now,
        plots=plots,
        recommendations=recommendations,
        memberships=memberships,
    )
    forecast = await query_plot_weather(
        user_id=user_id,
        plot_id=plot.id,
        days=FORECAST_DAYS,
        now=now,
        plots=plots,
        weather=weather,
        memberships=memberships,
    )

    return PlotStatus(
        plot=PlotSummary(
            id=plot.id,
            org_id=plot.org_id,
            farm_id=plot.farm_id,
            name=plot.name,
            area_ha=plot.area_ha,
            irrigation_system=plot.irrigation_system.value,
        ),
        active_cycle=await _active_cycle(plot_id=plot.id, today=today, cycles=cycles, crops=crops),
        latest=latest,
        water_balance=None if not balances else _water_balance(balances[0]),
        recommendation=recommendation,
        open_alerts=[
            OpenAlert(
                id=alert.id,
                org_id=alert.org_id,
                rule_id=alert.rule_id,
                rule_code=alert.rule_code,
                plot_id=alert.plot_id,
                node_id=alert.node_id,
                state=alert.state.value,
                severity=alert.severity.value,
                evidence=alert.evidence,
                opened_at=alert.opened_at,
                acknowledged_at=alert.acknowledged_at,
                resolved_at=alert.resolved_at,
                escalated_at=alert.escalated_at,
                resolution_note=alert.resolution_note,
            )
            for alert in await list_open_alerts_for_plots(
                plot_ids=[plot.id], org_ids=[plot.org_id], alerts=alerts
            )
        ],
        # `days=FORECAST_DAYS` already bounds the forecast to today..+2; the
        # filter drops the observed rows the same call returns with them.
        weather_next_3d=[day for day in forecast if day.is_forecast],
        nodes=await get_plot_nodes_health(
            plot_id=plot.id, org_id=plot.org_id, nodes=nodes, now=now
        ),
        digital_adoption_index=await _digital_adoption(
            plot_id=plot.id, org_id=plot.org_id, metrics=metrics
        ),
    )


async def _active_cycle(
    *,
    plot_id: UUID,
    today: date,
    cycles: CropCycleRepository,
    crops: CropRepository,
) -> ActiveCycleSummary | None:
    """The plot's active cycle with today's stage and cycle day, or `None`
    without one (D-T0.6)."""
    cycle = await cycles.get_active_for_plot(plot_id)
    if cycle is None:
        return None
    crop = await crops.get(cycle.crop_id)
    assert crop is not None  # docs/03: `crop_cycle.crop_id` is a NOT NULL FK
    stage, day_of_cycle = crop_stage_for_day(crop.stages, cycle.sown_on, today)
    return ActiveCycleSummary(
        crop=CropSummary(id=crop.id, code=crop.code, name_es=crop.name_es),
        stage=stage,
        day_of_cycle=day_of_cycle,
    )


async def _latest(
    *,
    plot_id: UUID,
    org_id: UUID,
    window_start: datetime,
    now: datetime,
    soil: SoilProfileRepository,
    nodes: NodeRepository,
    sensors: SensorRepository,
    calibrations: CalibrationRepository,
    readings: ReadingRepository,
) -> LatestReadings:
    """`latest` (D-T0.4), with soil moisture from the representative sensor
    (D-T2.1)."""
    by_metric = await query_latest_plot_readings(
        plot_id=plot_id,
        org_id=org_id,
        metrics=LATEST_METRICS,
        now=now,
        readings=readings,
    )
    profile = await soil.get_for_plot(plot_id)
    moisture_pct, moisture_at = await _soil_moisture(
        plot_id=plot_id,
        org_id=org_id,
        root_depth_cm=None if profile is None else profile.root_depth_cm,
        window_start=window_start,
        now=now,
        nodes=nodes,
        sensors=sensors,
        calibrations=calibrations,
        readings=readings,
        fallback=by_metric["soil_moisture"],
    )
    air_temp = by_metric["air_temp"]
    air_rh = by_metric["air_rh"]

    times = [
        time
        for time in (
            moisture_at,
            None if air_temp is None else air_temp.time,
            None if air_rh is None else air_rh.time,
        )
        if time is not None
    ]
    return LatestReadings(
        soil_moisture_pct=moisture_pct,
        air_temp_c=None if air_temp is None else air_temp.value,
        air_rh_pct=None if air_rh is None else air_rh.value,
        at=max(times) if times else None,
    )


async def _soil_moisture(
    *,
    plot_id: UUID,
    org_id: UUID,
    root_depth_cm: float | None,
    window_start: datetime,
    now: datetime,
    nodes: NodeRepository,
    sensors: SensorRepository,
    calibrations: CalibrationRepository,
    readings: ReadingRepository,
    fallback: ReadingPoint | None,
) -> tuple[float | None, datetime | None]:
    """The plot's soil moisture and the time of that value (D-T2.1).

    The representative sensors of docs/06 §5 first — one near Zr/2, or the mean
    of two in the root zone — so the home screen and the recommendation read the
    same evidence as the daily balance. Without one, the newest valid reading of
    any depth answers, and a plot with neither reports `None`, never a zero.
    """
    representative = await representative_soil_moisture_sensors(
        org_id=org_id,
        plot_id=plot_id,
        root_depth_cm=root_depth_cm,
        start=window_start,
        end=now,
        nodes=nodes,
        sensors=sensors,
        calibrations=calibrations,
        readings=readings,
    )
    if not representative:
        return (None, None) if fallback is None else (fallback.value, fallback.time)

    newest: list[ReadingPoint] = []
    for sensor in representative:
        points = await readings.query_valid_raw(
            sensor.sensor_id, org_id, start=window_start, end=now
        )
        if points:  # ordered by time, so the last one is the newest
            newest.append(points[-1])
    if not newest:
        return None, None
    return (
        sum(point.value for point in newest) / len(newest),
        max(point.time for point in newest),
    )


async def _recommendation(
    *,
    user_id: UUID,
    plot_id: UUID,
    day: date,
    now: datetime,
    plots: PlotRepository,
    recommendations: IrrigationRecommendationRepository,
    memberships: MembershipRepository,
) -> RecommendationSummary | None:
    """Today's stored recommendation, or `None` when it was never calculated
    (docs/04 §Estado)."""
    try:
        stored = await query_plot_recommendation(
            user_id=user_id,
            plot_id=plot_id,
            day=day,
            now=now,
            plots=plots,
            recommendations=recommendations,
            memberships=memberships,
        )
    except RecommendationNotFoundError:
        return None
    return RecommendationSummary(
        kind=stored.kind.value,
        depth_mm=stored.depth_mm,
        duration_min=stored.duration_min,
        advice=list(stored.advice),
        rationale=stored.rationale,
    )


async def _digital_adoption(
    *,
    plot_id: UUID,
    org_id: UUID,
    metrics: MonthlyMetricRepository,
) -> DigitalAdoption | None:
    """The plot's latest stored adoption index, or `None` (D-T0.13).

    `None` in two cases docs/04 §Estado lists separately: the plot has no
    stored month, or the latest month's own index is null because none of its
    four components had evidence (D-T0.2). Neither is an index of zero, which
    would read as "adopted nothing" (docs/11-metricas.md §2).

    `get_latest_plot_month` orders by `month` descending, so a re-run of an
    older month never becomes the latest one; this only reads what the monthly
    job stored and never recomputes, so the number a farmer sees does not move
    because somebody re-ran a job.
    """
    latest = await get_latest_plot_month(org_id=org_id, plot_id=plot_id, metrics=metrics)
    if latest is None or latest.digital_adoption_index is None:
        return None
    return DigitalAdoption(value=latest.digital_adoption_index, month=latest.month)


def _water_balance(day: PlotWaterBalanceDay) -> WaterBalanceSummary:
    return WaterBalanceSummary(
        depletion_mm=day.depletion_mm,
        taw_mm=day.taw_mm,
        raw_mm=day.raw_mm,
        stress_moisture_pct=day.stress_moisture_pct,
        status=day.status.value,
    )
