"""Weather domain data (docs/03-modelo-datos.md:176-191; docs/06-diseno-detallado.md §6).

Pure data, no I/O: `WeatherDay` is what the Open-Meteo adapter produces and what
the irrigation math (E6) consumes, with no knowledge of HTTP or Postgres.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class WeatherDay:
    """One `weather_daily` row (docs/03-modelo-datos.md:180-191).

    `is_forecast` separates a forecast row from the consolidated observed one
    for the same day (docs/06-diseno-detallado.md §6), and it is part of the
    primary key. The measures are `float | None` because Open-Meteo reports
    `null` for a day it has no value for; `fetched_at` is the provider fetch
    that produced the row, the clock the `stale` degradation rule reads.
    """

    day: date
    is_forecast: bool
    et0_mm: float | None
    rain_mm: float | None
    tmin_c: float | None
    tmax_c: float | None
    rh_mean_pct: float | None
    fetched_at: datetime
