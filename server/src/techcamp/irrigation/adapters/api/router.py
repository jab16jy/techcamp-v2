"""Irrigation REST endpoints (docs/04-api.md:105-109; docs/06 §5)."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from techcamp.farms.adapters.api.deps import PlotRepoDep
from techcamp.farms.domain.errors import PlotNotFoundError
from techcamp.identity.adapters.api.deps import CurrentUserId, MembershipRepoDep
from techcamp.irrigation.adapters.api.deps import NowDep, RecommendationRepoDep, WaterBalanceRepoDep
from techcamp.irrigation.application.query_irrigation import (
    query_plot_recommendation,
    query_plot_water_balance,
)
from techcamp.irrigation.domain.errors import InvalidDateRangeError, RecommendationNotFoundError
from techcamp.shared.errors import ProblemError

router = APIRouter(tags=["irrigation"])


class IrrigationRecommendationView(BaseModel):
    """Irrigation recommendation for a plot on a specific day."""

    plot_id: UUID
    day: date
    kind: str
    depth_mm: float | None = None
    duration_min: int | None = None
    advice: list[str] = Field(default_factory=list)
    rationale: dict[str, Any] = Field(default_factory=dict)


class WaterBalanceDayView(BaseModel):
    """Daily root zone water balance observation and status."""

    day: date
    etc_mm: float
    effective_rain_mm: float
    irrigation_mm: float
    taw_mm: float
    raw_mm: float
    depletion_model_mm: float
    depletion_mm: float
    soil_moisture_obs_pct: float | None = None
    assimilation_k: float
    stress_moisture_pct: float
    status: str


@router.get(
    "/plots/{plot_id}/irrigation/recommendation",
    response_model=IrrigationRecommendationView,
)
async def get_plot_irrigation_recommendation(
    plot_id: UUID,
    user_id: CurrentUserId,
    plots: PlotRepoDep,
    recommendations: RecommendationRepoDep,
    memberships: MembershipRepoDep,
    now: NowDep,
    day: Annotated[date | None, Query()] = None,
) -> IrrigationRecommendationView:
    """docs/04-api.md:108: `GET /plots/{plot_id}/irrigation/recommendation?day=`.

    Returns the recommendation for the given day (defaults to local today in America/Bogota).
    Unknown plot or another org's plot answers 404 "Plot not found".
    Missing recommendation for the day answers 404 "Recommendation not found".
    """
    try:
        rec = await query_plot_recommendation(
            user_id=user_id,
            plot_id=plot_id,
            day=day,
            now=now,
            plots=plots,
            recommendations=recommendations,
            memberships=memberships,
        )
    except PlotNotFoundError as exc:
        raise ProblemError(status=404, title="Plot not found") from exc
    except RecommendationNotFoundError as exc:
        raise ProblemError(status=404, title="Recommendation not found") from exc

    return IrrigationRecommendationView(
        plot_id=rec.plot_id,
        day=rec.day,
        kind=rec.kind.value,
        depth_mm=rec.depth_mm,
        duration_min=rec.duration_min,
        advice=[a.value for a in rec.advice],
        rationale=dict(rec.rationale) if rec.rationale else {},
    )


@router.get(
    "/plots/{plot_id}/water-balance",
    response_model=list[WaterBalanceDayView],
)
async def get_plot_water_balance(
    plot_id: UUID,
    user_id: CurrentUserId,
    plots: PlotRepoDep,
    water_balances: WaterBalanceRepoDep,
    memberships: MembershipRepoDep,
    now: NowDep,
    from_day: Annotated[date | None, Query(alias="from")] = None,
    to_day: Annotated[date | None, Query(alias="to")] = None,
) -> list[WaterBalanceDayView]:
    """docs/04-api.md:109: `GET /plots/{plot_id}/water-balance?from=&to=`.

    Returns daily water balance history ordered by day ascending with computed status.
    Defaults: `to` = local yesterday in America/Bogota, `from` = `to` - 29 days.
    `from > to` or range > 366 days answers 422.
    Unknown plot or another org's plot answers 404 "Plot not found".
    No rows in the range answers `[]`.
    """
    try:
        items = await query_plot_water_balance(
            user_id=user_id,
            plot_id=plot_id,
            from_day=from_day,
            to_day=to_day,
            now=now,
            plots=plots,
            water_balances=water_balances,
            memberships=memberships,
        )
    except PlotNotFoundError as exc:
        raise ProblemError(status=404, title="Plot not found") from exc
    except InvalidDateRangeError as exc:
        raise ProblemError(status=422, title="Invalid date range", detail=str(exc)) from exc

    return [
        WaterBalanceDayView(
            day=item.day,
            etc_mm=item.etc_mm,
            effective_rain_mm=item.effective_rain_mm,
            irrigation_mm=item.irrigation_mm,
            taw_mm=item.taw_mm,
            raw_mm=item.raw_mm,
            depletion_model_mm=item.depletion_model_mm,
            depletion_mm=item.depletion_mm,
            soil_moisture_obs_pct=item.soil_moisture_obs_pct,
            assimilation_k=item.assimilation_k,
            stress_moisture_pct=item.stress_moisture_pct,
            status=item.status.value if hasattr(item.status, "value") else str(item.status),
        )
        for item in items
    ]
