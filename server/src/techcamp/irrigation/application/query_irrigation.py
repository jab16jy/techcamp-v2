"""Query use cases for irrigation recommendations and water balance.

docs/04-api.md:105-109; docs/06 §5.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from techcamp.farms.application.manage_plots import resolve_plot_access
from techcamp.farms.application.ports import PlotRepository
from techcamp.farms.domain.models import IrrigationSystem
from techcamp.identity.application.ports import MembershipRepository
from techcamp.irrigation.application.ports import (
    IrrigationRecommendationRepository,
    WaterBalanceRepository,
)
from techcamp.irrigation.domain.errors import InvalidDateRangeError, RecommendationNotFoundError
from techcamp.irrigation.domain.models import (
    IrrigationRecommendation,
    WaterBalanceStatus,
    compute_water_balance_status,
)

BOGOTA_TZ = ZoneInfo("America/Bogota")


@dataclass(frozen=True, slots=True)
class PlotWaterBalanceDay:
    """A day in the plot's water balance history with computed status."""

    day: date
    etc_mm: float
    effective_rain_mm: float
    irrigation_mm: float
    taw_mm: float
    raw_mm: float
    depletion_model_mm: float
    depletion_mm: float
    soil_moisture_obs_pct: float | None
    assimilation_k: float
    stress_moisture_pct: float
    status: WaterBalanceStatus


async def query_plot_recommendation(
    *,
    user_id: UUID,
    plot_id: UUID,
    day: date | None = None,
    now: datetime | None = None,
    plots: PlotRepository,
    recommendations: IrrigationRecommendationRepository,
    memberships: MembershipRepository,
) -> IrrigationRecommendation:
    """Retrieve the irrigation recommendation for a plot on a given day.

    Defaults `day` to local today in America/Bogota.
    Cross-org or non-existent plot raises `PlotNotFoundError` via `resolve_plot_access`.
    Missing recommendation for the day raises `RecommendationNotFoundError`.
    """
    plot, _role = await resolve_plot_access(
        user_id=user_id, plot_id=plot_id, plots=plots, memberships=memberships
    )

    if day is None:
        if now is None:
            now = datetime.now(UTC)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        day = now.astimezone(BOGOTA_TZ).date()

    rec = await recommendations.get_for_plot(plot_id=plot.id, day=day, org_id=plot.org_id)
    if rec is None:
        raise RecommendationNotFoundError(plot_id=plot.id, day=day)
    return rec


async def query_plot_water_balance(
    *,
    user_id: UUID,
    plot_id: UUID,
    from_day: date | None = None,
    to_day: date | None = None,
    now: datetime | None = None,
    plots: PlotRepository,
    water_balances: WaterBalanceRepository,
    memberships: MembershipRepository,
) -> list[PlotWaterBalanceDay]:
    """Retrieve daily water balance history for a plot in [from_day, to_day].

    Defaults: `to` = local yesterday in America/Bogota, `from` = `to` - 29 days (30 days total).
    `from_day > to_day` or range > 366 days raises `InvalidDateRangeError`.
    Cross-org or non-existent plot raises `PlotNotFoundError` via `resolve_plot_access`.
    Returns items ordered by day ascending with `status` computed per row.
    """
    plot, _role = await resolve_plot_access(
        user_id=user_id, plot_id=plot_id, plots=plots, memberships=memberships
    )

    if now is None:
        now = datetime.now(UTC)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    local_today = now.astimezone(BOGOTA_TZ).date()
    local_yesterday = local_today - timedelta(days=1)

    effective_to = to_day if to_day is not None else local_yesterday
    effective_from = from_day if from_day is not None else (effective_to - timedelta(days=29))

    if effective_from > effective_to:
        raise InvalidDateRangeError("`from` date must be on or before `to` date")
    if (effective_to - effective_from).days > 366:
        raise InvalidDateRangeError("date range cannot exceed 366 days")

    rows = await water_balances.list_for_plot(
        plot_id=plot.id, org_id=plot.org_id, from_day=effective_from, to_day=effective_to
    )

    is_rainfed = (
        plot.irrigation_system == IrrigationSystem.NONE
        or str(plot.irrigation_system).lower() == "none"
    )

    return [
        PlotWaterBalanceDay(
            day=r.day,
            etc_mm=r.etc_mm,
            effective_rain_mm=r.effective_rain_mm,
            irrigation_mm=r.irrigation_mm,
            taw_mm=r.taw_mm,
            raw_mm=r.raw_mm,
            depletion_model_mm=r.depletion_model_mm,
            depletion_mm=r.depletion_mm,
            soil_moisture_obs_pct=r.soil_moisture_obs_pct,
            assimilation_k=r.assimilation_k,
            stress_moisture_pct=r.stress_moisture_pct,
            status=compute_water_balance_status(
                dr=r.depletion_mm, raw=r.raw_mm, is_rainfed=is_rainfed
            ),
        )
        for r in rows
    ]
