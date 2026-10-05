"""`GET /plots/{plot_id}/status` (docs/04-api.md §Estado de la parcela).

The HTTP shape only: pydantic out, `PlotNotFoundError` mapped to
`problem+json`, and `home.application.build_plot_status` decides every field's
rule (D-T0.1). Every repository comes from the module that owns it, through
that module's own `deps` — the wiring that already `weather`, `irrigation`,
`telemetry` and `logbook` do, and that `metrics` does too since it ships its
own `MonthlyMetricRepoDep`. Nothing is built twice here, and no metrics SQL
adapter crosses a module boundary from this file.

Each dep is annotated with the adapter class its own module returns, as every
dep in this file is. The boundary `home` actually owns is one layer down:
`build_plot_status` takes `metrics.application.adoption.MonthlyMetricRepository`,
the port, so `home` still reaches `metrics` only through its use case and
never through its SQL.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field

from techcamp.alerts.adapters.api.deps import AlertRepoDep
from techcamp.farms.adapters.api.deps import (
    CropCycleRepoDep,
    CropRepoDep,
    FarmRepoDep,
    PlotRepoDep,
    SoilProfileRepoDep,
)
from techcamp.farms.domain.errors import PlotNotFoundError
from techcamp.home.application import (
    PlotStatus,
    TechnicianTrayItem,
    build_plot_status,
    build_technician_tray,
)
from techcamp.identity.adapters.api.deps import CurrentUserId, MembershipRepoDep
from techcamp.irrigation.adapters.api.deps import (
    NowDep,
    RecommendationRepoDep,
    WaterBalanceRepoDep,
)
from techcamp.logbook.adapters.api.deps import VisitRepoDep
from techcamp.metrics.adapters.api.deps import MonthlyMetricRepoDep
from techcamp.shared.errors import ProblemError
from techcamp.telemetry.adapters.api.deps import (
    CalibrationRepoDep,
    NodeRepoDep,
    ReadingRepoDep,
    SensorRepoDep,
)
from techcamp.weather.adapters.api.deps import WeatherRepoDep

router = APIRouter(tags=["home"])


class PlotSummaryView(BaseModel):
    """The plot's own identity and its irrigation system, which is what the
    home screen renders (D-T0.7's active plot, ADR-0023's rainfed variant)."""

    id: UUID
    org_id: UUID
    farm_id: UUID
    name: str
    area_ha: float
    irrigation_system: str


class CycleCropView(BaseModel):
    id: int
    code: str
    name_es: str


class ActiveCycleView(BaseModel):
    crop: CycleCropView
    stage: str | None
    """`initial|development|mid|late`, or null when the crop has no Kc stages
    or the sowing is later than today (D-T0.6). The web maps the key."""
    day_of_cycle: int | None


class LatestView(BaseModel):
    """A metric without a valid reading in the last 24 h is `null`, never 0;
    `at` is the time of the newest value returned (D-T0.4)."""

    soil_moisture_pct: float | None
    air_temp_c: float | None
    air_rh_pct: float | None
    at: datetime | None


class WaterBalanceView(BaseModel):
    depletion_mm: float
    taw_mm: float
    raw_mm: float
    stress_moisture_pct: float
    status: str


class RecommendationView(BaseModel):
    kind: str
    depth_mm: float | None
    duration_min: int | None
    advice: list[str] = Field(default_factory=list)
    rationale: dict[str, Any] = Field(default_factory=dict)
    """The stored calculation's object, unchanged (D-T0.11): it carries
    `forecast_rain_7d_mm` and the rest of the evidence the web writes the
    "why" from."""


class OpenAlertView(BaseModel):
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


class WeatherDayView(BaseModel):
    day: date
    is_forecast: bool
    et0_mm: float | None
    rain_mm: float | None
    tmin_c: float | None
    tmax_c: float | None
    rh_mean_pct: float | None
    fetched_at: datetime
    stale: bool


class PlotNodeHealthView(BaseModel):
    """`battery_v` and `rssi` stay out until a node reports them (D-T0.5)."""

    node_id: UUID
    status: str
    last_seen_at: datetime | None
    completeness_24h: float | None


class DigitalAdoptionIndexView(BaseModel):
    """The stored index with the month it belongs to (D-T0.13).

    `value` is a float, not the stored `Decimal`: this is the display number
    docs/07 §Inicio renders as "Adopción digital: 72 · septiembre", and
    Pydantic would refuse to serialize a `Decimal` into a float field without
    the cast done at the boundary. `month` is the bucket's first day, an ISO
    date on the wire, which is what the web formats as the month name.
    """

    value: float
    month: date


class PlotStatusView(BaseModel):
    plot: PlotSummaryView
    active_cycle: ActiveCycleView | None
    latest: LatestView
    water_balance: WaterBalanceView | None
    recommendation: RecommendationView | None
    open_alerts: list[OpenAlertView]
    weather_next_3d: list[WeatherDayView]
    nodes: list[PlotNodeHealthView]
    digital_adoption_index: DigitalAdoptionIndexView | None
    """`None` until the plot has a stored month, and `None` again when that
    month's own index is null (D-T0.2, docs/04 §Estado). Never a zero."""


def _view(status: PlotStatus) -> PlotStatusView:
    return PlotStatusView(
        plot=PlotSummaryView(
            id=status.plot.id,
            org_id=status.plot.org_id,
            farm_id=status.plot.farm_id,
            name=status.plot.name,
            area_ha=status.plot.area_ha,
            irrigation_system=status.plot.irrigation_system,
        ),
        active_cycle=None
        if status.active_cycle is None
        else ActiveCycleView(
            crop=CycleCropView(
                id=status.active_cycle.crop.id,
                code=status.active_cycle.crop.code,
                name_es=status.active_cycle.crop.name_es,
            ),
            stage=status.active_cycle.stage,
            day_of_cycle=status.active_cycle.day_of_cycle,
        ),
        latest=LatestView(
            soil_moisture_pct=status.latest.soil_moisture_pct,
            air_temp_c=status.latest.air_temp_c,
            air_rh_pct=status.latest.air_rh_pct,
            at=status.latest.at,
        ),
        water_balance=None
        if status.water_balance is None
        else WaterBalanceView(
            depletion_mm=status.water_balance.depletion_mm,
            taw_mm=status.water_balance.taw_mm,
            raw_mm=status.water_balance.raw_mm,
            stress_moisture_pct=status.water_balance.stress_moisture_pct,
            status=status.water_balance.status,
        ),
        recommendation=None
        if status.recommendation is None
        else RecommendationView(
            kind=status.recommendation.kind,
            depth_mm=status.recommendation.depth_mm,
            duration_min=status.recommendation.duration_min,
            advice=list(status.recommendation.advice),
            rationale=status.recommendation.rationale,
        ),
        open_alerts=[
            OpenAlertView(
                id=alert.id,
                org_id=alert.org_id,
                rule_id=alert.rule_id,
                plot_id=alert.plot_id,
                node_id=alert.node_id,
                rule_code=alert.rule_code,
                state=alert.state,
                severity=alert.severity,
                evidence=alert.evidence,
                opened_at=alert.opened_at,
                acknowledged_at=alert.acknowledged_at,
                resolved_at=alert.resolved_at,
                escalated_at=alert.escalated_at,
                resolution_note=alert.resolution_note,
            )
            for alert in status.open_alerts
        ],
        weather_next_3d=[
            WeatherDayView(
                day=day.day,
                is_forecast=day.is_forecast,
                et0_mm=day.et0_mm,
                rain_mm=day.rain_mm,
                tmin_c=day.tmin_c,
                tmax_c=day.tmax_c,
                rh_mean_pct=day.rh_mean_pct,
                fetched_at=day.fetched_at,
                stale=day.stale,
            )
            for day in status.weather_next_3d
        ],
        nodes=[
            PlotNodeHealthView(
                node_id=node.node_id,
                status=node.status.value,
                last_seen_at=node.last_seen_at,
                completeness_24h=node.completeness_24h,
            )
            for node in status.nodes
        ],
        digital_adoption_index=None
        if status.digital_adoption_index is None
        else DigitalAdoptionIndexView(
            value=float(status.digital_adoption_index.value),
            month=status.digital_adoption_index.month,
        ),
    )


@router.get("/plots/{plot_id}/status", response_model=PlotStatusView)
async def get_plot_status(
    plot_id: UUID,
    user_id: CurrentUserId,
    plots: PlotRepoDep,
    memberships: MembershipRepoDep,
    cycles: CropCycleRepoDep,
    crops: CropRepoDep,
    soil: SoilProfileRepoDep,
    nodes: NodeRepoDep,
    sensors: SensorRepoDep,
    calibrations: CalibrationRepoDep,
    readings: ReadingRepoDep,
    water_balances: WaterBalanceRepoDep,
    recommendations: RecommendationRepoDep,
    weather: WeatherRepoDep,
    alerts: AlertRepoDep,
    metrics: MonthlyMetricRepoDep,
    now: NowDep,
) -> PlotStatusView:
    """docs/04 §Estado: `GET /plots/{plot_id}/status`, the whole home screen in
    one request. A plot that does not exist or belongs to another organization
    answers 404 "Plot not found" (docs/09-cuellos-de-botella.md#seguridad);
    everything missing inside a plot the caller may see is `null`, never a zero.
    """
    try:
        status = await build_plot_status(
            user_id=user_id,
            plot_id=plot_id,
            now=now,
            plots=plots,
            memberships=memberships,
            cycles=cycles,
            crops=crops,
            soil=soil,
            nodes=nodes,
            sensors=sensors,
            calibrations=calibrations,
            readings=readings,
            water_balances=water_balances,
            recommendations=recommendations,
            weather=weather,
            alerts=alerts,
            metrics=metrics,
        )
    except PlotNotFoundError as exc:
        raise ProblemError(status=404, title="Plot not found") from exc

    return _view(status)


class FarmSummaryView(BaseModel):
    """The docs/04 `farm` field in `/me/tray`: compact summary with no geometry."""

    id: UUID
    org_id: UUID
    name: str
    municipality_code: str


class TrayItemView(BaseModel):
    """One item in the technician tray response (docs/04 §Visitas; D-T0.8)."""

    farm: FarmSummaryView
    open_alerts: list[OpenAlertView]
    last_visit_on: date | None


def _tray_item_view(item: TechnicianTrayItem) -> TrayItemView:
    return TrayItemView(
        farm=FarmSummaryView(
            id=item.farm.id,
            org_id=item.farm.org_id,
            name=item.farm.name,
            municipality_code=item.farm.municipality_code,
        ),
        open_alerts=[
            OpenAlertView(
                id=alert.id,
                org_id=alert.org_id,
                rule_id=alert.rule_id,
                plot_id=alert.plot_id,
                node_id=alert.node_id,
                rule_code=alert.rule_code,
                state=alert.state,
                severity=alert.severity,
                evidence=alert.evidence,
                opened_at=alert.opened_at,
                acknowledged_at=alert.acknowledged_at,
                resolved_at=alert.resolved_at,
                escalated_at=alert.escalated_at,
                resolution_note=alert.resolution_note,
            )
            for alert in item.open_alerts
        ],
        last_visit_on=item.last_visit_on,
    )


@router.get("/me/tray", response_model=list[TrayItemView])
async def get_technician_tray(
    user_id: CurrentUserId,
    memberships: MembershipRepoDep,
    farms: FarmRepoDep,
    plots: PlotRepoDep,
    alerts: AlertRepoDep,
    visits: VisitRepoDep,
) -> list[TrayItemView]:
    """docs/04 §Visitas de extensión y bandeja del técnico: `GET /me/tray`.

    Returns farms assigned to the caller across all memberships (D-T0.8).
    Ordered: critical alerts desc, open alerts desc, last_visit_on asc (null first),
    farm name, farm id. Returns [] for any caller with no assigned farms (200, never 403).
    """
    items = await build_technician_tray(
        user_id=user_id,
        memberships=memberships,
        farms=farms,
        plots=plots,
        alerts=alerts,
        visits=visits,
    )
    return [_tray_item_view(item) for item in items]
