"""Weather domain data and rules (docs/03-modelo-datos.md:176-191; docs/06-diseno-detallado.md §6).

Pure data and pure functions, no I/O: `WeatherDay` is what the Open-Meteo
adapter produces and what the irrigation math (E6) consumes, and `cell_for` /
`is_stale` are the two rules every consumer shares, with no knowledge of HTTP,
Postgres or the clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal


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


CELL_DEGREE = Decimal("0.1")
"""Weather cell grid step: 0.1°, about 11 km (docs/00-glosario.md:40)."""

STALE_AFTER = timedelta(hours=6)
"""How old a cell's data may get before it is served as `stale`: two missed
3 h refreshes (docs/06-diseno-detallado.md §6)."""


def _to_grid(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(CELL_DEGREE, rounding=ROUND_HALF_UP)


def cell_for(lat: float | Decimal, lon: float | Decimal) -> tuple[Decimal, Decimal]:
    """The `(lat, lon)` grid coordinates of the 0.1° cell a point falls in
    (docs/00-glosario.md:40), which is what identifies a cell for
    `get_or_create_cell`.

    `Decimal`, never `float`: the coordinates are stored in `numeric` columns
    under `UNIQUE(lat, lon)`, and a float bound to `numeric` reaches Postgres
    as its full binary expansion, so the same square resolved from a float and
    from a `Decimal` would become two different cells — the cache row the whole
    design rests on (docs/09-cuellos-de-botella.md:39) would split in two.

    Ties round away from zero (`ROUND_HALF_UP`) so a point exactly on a cell
    border always lands on the same side in both hemispheres, instead of
    depending on `round()`'s banker's rounding over binary floats.
    """
    return (_to_grid(lat), _to_grid(lon))


def is_stale(fetched_at: datetime, now: datetime) -> bool:
    """True when the cell's last successful fetch is older than `STALE_AFTER`.

    `now` is an argument, not a clock read, so the degradation rule is decided
    by the values under test. The mark is exactly `STALE_AFTER` old, not
    older: the degradation is for data that missed a refresh, not one sitting
    precisely on the limit (docs/06-diseno-detallado.md §6).
    """
    return now - fetched_at > STALE_AFTER
