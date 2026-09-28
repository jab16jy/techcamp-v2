"""`GET /plots/{plot_id}/weather` use case (docs/04-api.md:94; docs/06 §6).

Retrieves the weather window for a plot's assigned 0.1° cell:
- Last `days` observed rows (`is_forecast=False`, `day` in `[today-days, today-1]`).
- Followed by `days` forecast rows (`is_forecast=True`, `day` in `[today, today+days-1]`).
Ordered by day, then observed before forecast.
`stale` is evaluated once for the cell's response as `is_stale(max(fetched_at), now)`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from techcamp.farms.application.manage_plots import resolve_plot_access
from techcamp.farms.application.ports import PlotRepository
from techcamp.identity.application.ports import MembershipRepository
from techcamp.shared.dates import local_today
from techcamp.weather.application.ports import WeatherRepository
from techcamp.weather.domain.models import is_stale


@dataclass(frozen=True, slots=True)
class PlotWeatherDay:
    day: date
    is_forecast: bool
    et0_mm: float | None
    rain_mm: float | None
    tmin_c: float | None
    tmax_c: float | None
    rh_mean_pct: float | None
    fetched_at: datetime
    stale: bool


async def query_plot_weather(
    *,
    user_id: UUID,
    plot_id: UUID,
    days: int = 7,
    now: datetime | None = None,
    plots: PlotRepository,
    weather: WeatherRepository,
    memberships: MembershipRepository,
) -> list[PlotWeatherDay]:
    """Retrieve plot weather observations and forecast with stale degradation.

    Unknown plot or plot belonging to another organization raises
    `PlotNotFoundError` via `resolve_plot_access` (docs/09 org isolation).
    A plot without an assigned cell or with no stored rows returns `[]`.
    """
    plot, _role = await resolve_plot_access(
        user_id=user_id, plot_id=plot_id, plots=plots, memberships=memberships
    )
    if plot.weather_cell_id is None:
        return []

    if now is None:
        now = datetime.now(UTC)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    today = local_today(now)

    observed_from = today - timedelta(days=days)
    observed_to = today - timedelta(days=1)
    forecast_from = today
    forecast_to = today + timedelta(days=days - 1)

    rows = await weather.list_daily(
        cell_id=plot.weather_cell_id,
        from_day=observed_from,
        to_day=forecast_to,
    )

    observed_rows = [
        r for r in rows if not r.is_forecast and (observed_from <= r.day <= observed_to)
    ]
    forecast_rows = [r for r in rows if r.is_forecast and (forecast_from <= r.day <= forecast_to)]

    observed_rows.sort(key=lambda r: (r.day, r.is_forecast))
    forecast_rows.sort(key=lambda r: (r.day, r.is_forecast))
    combined_rows = observed_rows + forecast_rows

    if not combined_rows:
        return []

    cell_stale = is_stale(max(r.fetched_at for r in combined_rows), now)

    return [
        PlotWeatherDay(
            day=r.day,
            is_forecast=r.is_forecast,
            et0_mm=r.et0_mm,
            rain_mm=r.rain_mm,
            tmin_c=r.tmin_c,
            tmax_c=r.tmax_c,
            rh_mean_pct=r.rh_mean_pct,
            fetched_at=r.fetched_at,
            stale=cell_stale,
        )
        for r in combined_rows
    ]
