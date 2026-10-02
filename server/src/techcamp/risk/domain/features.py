"""Flood-risk (M2) features, shared by training and serving.

One module, imported by the dataset builder in `ml/` and by the daily serving job
in `server/`, because parity is a governance rule: docs/08-ml.md §M2 "Features"
and §Reglas de gobierno "Paridad de features", docs/06-diseno-detallado.md §8.
It computes nothing that needs I/O — the caller supplies the daily series — so
both sides feed it the same Open-Meteo archive source (`models=era5`, D-T0.1,
D-T0.6).

Two rules shape every function here:

* **Horizon.** The prediction for month M is issued with data through the last
  day of M-1 (docs/08 §M2 "Horizonte"), so every window ends there and no day of
  M or later is ever read.
* **Missing evidence is a third state.** A window with a missing or `None` day
  yields `None`, never `0`: a 0 mm accumulation is a claim about the weather that
  the data does not make.
"""

from __future__ import annotations

import math
from calendar import monthrange
from collections.abc import Container, Mapping
from dataclasses import dataclass
from datetime import date, timedelta

DailySeries = Mapping[date, float | None]
"""A daily series keyed by day; `None`, or an absent day, means "not reported"."""

PRECIPITATION_WINDOWS_MONTHS = (1, 2, 3, 4, 5, 6)
"""Rainfall accumulations over 1 to 6 calendar months before M (docs/08 §M2
"Features"). Six is also the longest window, which is why the split needs a gap of
at least six months (docs/08 §M2 "Particion")."""

ANOMALY_WINDOWS_MONTHS = (1, 3, 6)
"""Windows, in months, whose accumulation is compared against the climatology
(docs/08 §M2 "Features": anomalies against the train climatology)."""

FEATURE_NAMES: tuple[str, ...] = (
    *(f"precip_sum_{months}m" for months in PRECIPITATION_WINDOWS_MONTHS),
    *(f"precip_anomaly_{months}m" for months in ANOMALY_WINDOWS_MONTHS),
    "soil_moisture_mean_1m",
    "elevation_m",
    "slope_deg",
    "month_sin",
    "month_cos",
)
"""The ordered public contract: the column order both sides build vectors from
(docs/08 §M2 "Features")."""


@dataclass(frozen=True, slots=True)
class Neighbours:
    """Elevation (m) at the centre's four neighbours, `spacing_m` metres apart.

    The centre's own elevation is the `elevation_m` feature; the slope comes from
    the four neighbours alone (docs/08 §Fuentes de datos de M2: finite differences
    over 4 neighbours ~1 km apart).
    """

    east: float
    west: float
    north: float
    south: float
    spacing_m: float


def seasonality(month: int) -> tuple[float, float]:
    """`(sin, cos)` of the calendar month, January at the phase origin.

    Two features instead of a twelve-level category, so a model reads December as
    adjacent to January instead of as its furthest point.
    """
    angle = 2 * math.pi * (month - 1) / 12
    return math.sin(angle), math.cos(angle)


def slope_degrees(neighbours: Neighbours) -> float:
    """Slope in degrees from central differences over `2 x spacing_m`:
    `atan(hypot(dz/dx, dz/dy))` with
    `dz/dx = (east - west) / (2 x spacing_m)` and
    `dz/dy = (north - south) / (2 x spacing_m)`.
    """
    if neighbours.spacing_m <= 0:
        raise ValueError(f"spacing_m must be positive, got {neighbours.spacing_m}")
    dz_dx = (neighbours.east - neighbours.west) / (2 * neighbours.spacing_m)
    dz_dy = (neighbours.north - neighbours.south) / (2 * neighbours.spacing_m)
    return math.degrees(math.atan(math.hypot(dz_dx, dz_dy)))


def monthly_climatology(series: DailySeries, *, years: Container[int]) -> dict[int, float]:
    """Mean monthly total per calendar month, from the given years only.

    Anomalies are taken against the climatology of **train** (docs/08 §M2
    "Features"), so `years` is that split's year set: a day outside it cannot move
    the result, which is what keeps a validation or test anomaly from carrying its
    own season's weather.

    Each calendar month contributes the mean of its yearly **totals**, never the
    mean of its daily values: February of a dry year is 0 mm of rain, not a
    smaller share of it. A year whose month has a missing day contributes no
    total for that month, and a calendar month with no complete year is absent
    from the mapping rather than present as `0.0`.
    """
    days_by_month: dict[tuple[int, int], dict[int, float | None]] = {}
    for day, value in series.items():
        if day.year in years:
            days_by_month.setdefault((day.year, day.month), {})[day.day] = value

    totals: dict[int, list[float]] = {}
    for (year, month), values in days_by_month.items():
        length = monthrange(year, month)[1]
        if any(values.get(number) is None for number in range(1, length + 1)):
            continue
        total = sum(value for value in values.values() if value is not None)
        totals.setdefault(month, []).append(total)

    return {month: sum(month_totals) / len(month_totals) for month, month_totals in totals.items()}


def precip_sum(series: DailySeries, *, issue_month: date, months: int) -> float | None:
    """Rainfall (mm) over the `months` calendar months ending on the last day of
    M-1, or `None` when any day of the window is missing.

    `issue_month` is read as its calendar month, so the day-of-month it carries
    never moves the window's end.
    """
    return _window_sum(series, issue_month=issue_month, months=months)


def soil_moisture_mean(series: DailySeries, *, issue_month: date) -> float | None:
    """Mean volumetric soil moisture (m³/m³) over M-1, or `None` when any day of
    that month is missing.

    `issue_month` is read as its calendar month, like every other window here.
    """
    total = _window_sum(series, issue_month=issue_month, months=1)
    if total is None:
        return None
    first, last = _window_bounds(issue_month, months=1)
    return total / ((last - first).days + 1)


def precip_anomaly(
    series: DailySeries,
    *,
    issue_month: date,
    months: int,
    climatology: Mapping[int, float],
) -> float | None:
    """Accumulation minus the climatology of the same calendar months, or `None`
    when the accumulation is missing or the climatology does not cover every
    calendar month of the window: an unknown expected total is not a zero one.

    `issue_month` is read as its calendar month, like every other window here.
    """
    total = precip_sum(series, issue_month=issue_month, months=months)
    if total is None:
        return None
    expected = 0.0
    for month in _window_months(issue_month, months):
        if month not in climatology:
            return None
        expected += climatology[month]
    return total - expected


def build_features(
    *,
    issue_month: date,
    precipitation: DailySeries,
    soil_moisture: DailySeries,
    elevation_m: float | None = None,
    neighbours: Neighbours | None = None,
    climatology: Mapping[int, float] | None = None,
) -> dict[str, float | None]:
    """The M2 feature vector for one municipality and month, keyed and ordered by
    `FEATURE_NAMES`.

    `issue_month` is M: only its calendar month identifies the prediction, so any
    day of M names the same one. `elevation_m`, `neighbours` and `climatology` are
    optional, so their features are `None` when the caller has no answer: the
    elevation pair until Open-Meteo answers for the centroid, and the climatology
    when no train window covers the cell yet. Serving does compute one, with
    `monthly_climatology` over the served version's train years (docs/06 §8), and
    reading anomalies without it would break the parity `ml/` and `server/` owe.
    """
    features: dict[str, float | None] = {
        f"precip_sum_{months}m": precip_sum(precipitation, issue_month=issue_month, months=months)
        for months in PRECIPITATION_WINDOWS_MONTHS
    }
    for months in ANOMALY_WINDOWS_MONTHS:
        features[f"precip_anomaly_{months}m"] = (
            None
            if climatology is None
            else precip_anomaly(
                precipitation, issue_month=issue_month, months=months, climatology=climatology
            )
        )
    month_sin, month_cos = seasonality(issue_month.month)
    return {
        **features,
        "soil_moisture_mean_1m": soil_moisture_mean(soil_moisture, issue_month=issue_month),
        "elevation_m": elevation_m,
        "slope_deg": None if neighbours is None else slope_degrees(neighbours),
        "month_sin": month_sin,
        "month_cos": month_cos,
    }


def _month_start(year: int, month: int, *, offset: int) -> date:
    """First day of the month `offset` months before (negative) or after
    (positive) `year`/`month`."""
    index = year * 12 + (month - 1) + offset
    return date(index // 12, index % 12 + 1, 1)


def _window_bounds(issue_month: date, *, months: int) -> tuple[date, date]:
    """First and last day of the window: `months` calendar months ending on the
    last day of M-1. `issue_month` is read as its calendar month, so a caller that
    passes the day the job ran on gets the same window as one that passes the
    first of M, and never reads a day of M.
    """
    first_of_month = _month_start(issue_month.year, issue_month.month, offset=0)
    return (
        _month_start(issue_month.year, issue_month.month, offset=-months),
        first_of_month - timedelta(days=1),
    )


def _window_sum(series: DailySeries, *, issue_month: date, months: int) -> float | None:
    """Total of the window, whatever the series measures, or `None` when any day
    of it is missing."""
    first, last = _window_bounds(issue_month, months=months)
    total = 0.0
    day = first
    while day <= last:
        value = series.get(day)
        if value is None:
            return None
        total += value
        day += timedelta(days=1)
    return total


def _window_months(issue_month: date, months: int) -> list[int]:
    """The calendar months (1-12) of the window that ends on the last day of M-1,
    in chronological order."""
    first, _ = _window_bounds(issue_month, months=months)
    return [(first.month - 1 + step) % 12 + 1 for step in range(months)]
