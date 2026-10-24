"""E16 T3: the scenario's weather fixture lands in `weather_daily`
(docs/04-api.md:263; D-T16.2; docs/06-diseno-detallado.md:490).

The fixture's rows are keyed by the scenario's day offset (docs/06:490), not by
the calendar dates the capture covered: the loader places each row on the
scenario's day 0 plus its offset, so the same recording serves any day the
scenario is loaded on.

The rows are written through the weather repository's own `upsert_daily` — the
method its docstring names as the one E16's scenario loader writes through
(weather `adapters/repositories.py`) — so the 3-hourly refresh and the loader
write the same keys the same way.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from techcamp.weather.domain.models import WeatherDay

from ..scenario_library import WeatherFixture


class WeatherFixtureWriter(Protocol):
    """The weather module's write path, as the loader needs it.

    The weather module's own `WeatherRepository` protocol declares only the
    reads (docs/06 §6), so the loader declares the write it needs here rather
    than reaching past the module's facade. The concrete repository satisfies
    it structurally.
    """

    async def get_or_create_cell(self, lat: float, lon: float) -> int: ...

    async def upsert_daily(self, cell_id: int, days: Sequence[WeatherDay]) -> None: ...


async def load_weather_fixture(
    *,
    fixture: WeatherFixture,
    day: date,
    weather: WeatherFixtureWriter,
    now: datetime | None = None,
) -> int:
    """Land the recorded rows on the scenario's days; answer the cell's id.

    The rows are fetched now — the loader is the fetch that put them in
    `weather_daily` — so `fetched_at` is the load's clock, not the capture's:
    the recorded date stays in the fixture's envelope, where the provenance
    belongs, and the balance reads fresh rows (docs/06-diseno-detallado.md:250
    skips a day whose weather is older than a day).
    """
    cell_id = await weather.get_or_create_cell(fixture.lat, fixture.lon)
    fetched_at = now if now is not None else datetime.now(tz=UTC)
    await weather.upsert_daily(
        cell_id,
        [
            WeatherDay(
                day=day + timedelta(days=row.day_offset),
                is_forecast=row.is_forecast,
                et0_mm=row.et0_mm,
                rain_mm=row.rain_mm,
                tmin_c=row.tmin_c,
                tmax_c=row.tmax_c,
                rh_mean_pct=row.rh_mean_pct,
                fetched_at=fetched_at,
            )
            for row in fixture.days
        ],
    )
    return cell_id
