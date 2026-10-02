"""Application ports and data structures for the weather module.

Hexagonal layering per ADR-0002 and ADR-0003: application defines the
interfaces it needs, adapters implement them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from techcamp.weather.domain.models import WeatherDay


@dataclass(frozen=True, slots=True)
class DailyWeatherRow:
    """Application dataclass representing one day of daily weather data.

    Defined in application (not domain; lane A owns domain — parent reconciles
    later per E5 T3 brief).
    """

    day: date
    et0_mm: float | None
    rain_mm: float | None
    tmin_c: float | None
    tmax_c: float | None
    rh_mean_pct: float | None


class WeatherUnavailableError(Exception):
    """Base error raised when external weather data cannot be fetched."""

    def __init__(self, detail: str, *, upstream_status: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.upstream_status = upstream_status


@runtime_checkable
class WeatherForecastPort(Protocol):
    """External I/O port for fetching daily weather data (past + forecast).

    ADR-0002: external I/O needing a test double.
    """

    async def fetch_daily(
        self,
        lat: float,
        lon: float,
        *,
        past_days: int = 0,
        forecast_days: int = 7,
    ) -> list[DailyWeatherRow]: ...


@dataclass(frozen=True, slots=True)
class WeatherCellPoint:
    """One 0.1° cell with the centre every provider is asked about
    (docs/06-diseno-detallado.md §6: la celda de una parcela es la del centroide
    de su polígono, redondeado a 0,1°).

    `lat`/`lon` are floats, not the `Decimal` the column stores: a coordinate that
    leaves the process is a plain number, and rounding it to `Decimal` again on the
    way in would invite a call with a different point than the one stored.
    """

    id: int
    lat: float
    lon: float


class WeatherRepository(Protocol):
    async def list_daily(self, cell_id: int, from_day: date, to_day: date) -> list[WeatherDay]: ...

    async def active_cells(self) -> list[WeatherCellPoint]: ...
