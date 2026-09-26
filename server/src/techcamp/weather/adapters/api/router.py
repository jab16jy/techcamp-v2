"""Weather REST endpoints (docs/04-api.md:94; docs/06 §6)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel

from techcamp.farms.adapters.api.deps import PlotRepoDep
from techcamp.farms.domain.errors import PlotNotFoundError
from techcamp.identity.adapters.api.deps import CurrentUserId, MembershipRepoDep
from techcamp.shared.errors import ProblemError
from techcamp.weather.adapters.api.deps import NowDep, WeatherRepoDep
from techcamp.weather.application.query_weather import query_plot_weather

router = APIRouter(tags=["weather"])


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


@router.get("/plots/{plot_id}/weather", response_model=list[WeatherDayView])
async def get_plot_weather(
    plot_id: UUID,
    user_id: CurrentUserId,
    plots: PlotRepoDep,
    weather: WeatherRepoDep,
    memberships: MembershipRepoDep,
    now: NowDep,
    days: Annotated[int, Query(ge=1, le=16)] = 7,
) -> list[WeatherDayView]:
    """docs/04-api.md:94: `GET /plots/{plot_id}/weather?days=7 → WeatherDay[]`.

    Returns the last `days` observed rows followed by `days` forecast rows,
    with `stale` degradation flag. Unknown plot or another org's plot answers 404.
    """
    try:
        items = await query_plot_weather(
            user_id=user_id,
            plot_id=plot_id,
            days=days,
            now=now,
            plots=plots,
            weather=weather,
            memberships=memberships,
        )
    except PlotNotFoundError as exc:
        raise ProblemError(status=404, title="Plot not found") from exc

    return [
        WeatherDayView(
            day=item.day,
            is_forecast=item.is_forecast,
            et0_mm=item.et0_mm,
            rain_mm=item.rain_mm,
            tmin_c=item.tmin_c,
            tmax_c=item.tmax_c,
            rh_mean_pct=item.rh_mean_pct,
            fetched_at=item.fetched_at,
            stale=item.stale,
        )
        for item in items
    ]
