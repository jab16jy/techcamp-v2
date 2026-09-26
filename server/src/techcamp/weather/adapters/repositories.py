"""Postgres repository for weather (docs/03-modelo-datos.md:176-191).

No `org_id` filter anywhere: a cell is shared reference data, the same row for
every organization (docs/09-cuellos-de-botella.md:39).
"""

from __future__ import annotations

import datetime
import decimal
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Row, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import PlotRow
from techcamp.weather.adapters.orm import WeatherCellRow, WeatherDailyRow
from techcamp.weather.domain.models import WeatherDay

_DAILY_COLUMNS = (
    WeatherDailyRow.cell_id,
    WeatherDailyRow.day,
    WeatherDailyRow.is_forecast,
    WeatherDailyRow.et0_mm,
    WeatherDailyRow.rain_mm,
    WeatherDailyRow.tmin_c,
    WeatherDailyRow.tmax_c,
    WeatherDailyRow.rh_mean_pct,
    WeatherDailyRow.fetched_at,
)


def _day_from_row(row: Row[Any]) -> WeatherDay:
    return WeatherDay(
        day=row.day,
        is_forecast=row.is_forecast,
        et0_mm=float(row.et0_mm) if row.et0_mm is not None else None,
        rain_mm=float(row.rain_mm) if row.rain_mm is not None else None,
        tmin_c=float(row.tmin_c) if row.tmin_c is not None else None,
        tmax_c=float(row.tmax_c) if row.tmax_c is not None else None,
        rh_mean_pct=float(row.rh_mean_pct) if row.rh_mean_pct is not None else None,
        fetched_at=row.fetched_at,
    )


def _as_decimal(value: float | decimal.Decimal) -> decimal.Decimal:
    """Exact decimal for a `numeric` column. A float bound straight to
    `numeric` reaches Postgres as its full binary expansion, so
    `Decimal(10.1)` and `Decimal("10.1")` would be two different cells under
    `uq_weather_cell_lat_lon`; going through `str` keeps the grid value
    exactly as the caller meant it."""
    return value if isinstance(value, decimal.Decimal) else decimal.Decimal(str(value))


class SqlAlchemyWeatherRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create_cell(
        self, lat: float | decimal.Decimal, lon: float | decimal.Decimal
    ) -> int:
        """The id of the cell at these coordinates, creating it if needed.

        One `INSERT ... ON CONFLICT DO UPDATE` instead of a select-then-insert:
        two plots assigned to the same cell at the same time must end up on
        one row, or the cell stops being the Open-Meteo cache
        (docs/09-cuellos-de-botella.md:39). The update branch writes the
        conflicting values back, so it changes nothing and returns the
        existing id. The `Decimal` parameters are the rounded 0.1° grid
        coordinates from the domain (`cell_for`, T1b).
        """
        lat_decimal = _as_decimal(lat)
        lon_decimal = _as_decimal(lon)
        insert = pg_insert(WeatherCellRow).values(lat=lat_decimal, lon=lon_decimal)
        stmt = insert.on_conflict_do_update(
            index_elements=[WeatherCellRow.lat, WeatherCellRow.lon],
            set_={"lat": insert.excluded.lat, "lon": insert.excluded.lon},
        ).returning(WeatherCellRow.id)
        cell_id: int = (await self._session.execute(stmt)).scalar_one()
        await self._session.commit()
        return cell_id

    async def upsert_daily(self, cell_id: int, days: Sequence[WeatherDay]) -> None:
        """Store one provider response, updating the rows it already stored.

        The 3 h refresh writes the same `(cell_id, day, is_forecast)` keys over
        and over (docs/06-diseno-detallado.md §6), so the conflict branch
        updates the values and `fetched_at` in place. E16's scenario loader
        writes through the same method.
        """
        if not days:
            return
        values = [
            {
                "cell_id": cell_id,
                "day": day.day,
                "is_forecast": day.is_forecast,
                "et0_mm": _as_decimal(day.et0_mm) if day.et0_mm is not None else None,
                "rain_mm": _as_decimal(day.rain_mm) if day.rain_mm is not None else None,
                "tmin_c": _as_decimal(day.tmin_c) if day.tmin_c is not None else None,
                "tmax_c": _as_decimal(day.tmax_c) if day.tmax_c is not None else None,
                "rh_mean_pct": _as_decimal(day.rh_mean_pct)
                if day.rh_mean_pct is not None
                else None,
                "fetched_at": day.fetched_at,
            }
            for day in days
        ]
        stmt = pg_insert(WeatherDailyRow).values(values)
        excluded = stmt.excluded
        await self._session.execute(
            stmt.on_conflict_do_update(
                index_elements=[
                    WeatherDailyRow.cell_id,
                    WeatherDailyRow.day,
                    WeatherDailyRow.is_forecast,
                ],
                set_={
                    "et0_mm": excluded.et0_mm,
                    "rain_mm": excluded.rain_mm,
                    "tmin_c": excluded.tmin_c,
                    "tmax_c": excluded.tmax_c,
                    "rh_mean_pct": excluded.rh_mean_pct,
                    "fetched_at": excluded.fetched_at,
                },
            )
        )
        await self._session.commit()

    async def is_cold(self, cell_id: int) -> bool:
        """True when nothing has ever been stored for this cell.

        A plot landing on a cold cell would serve an empty forecast until the
        next 3 h run, so the plot write path defers a one-off fetch for it
        (docs/06-diseno-detallado.md §6). One indexed probe on the
        `weather_daily` primary key, which is far cheaper than the alternative
        — a provider call on every plot write.
        """
        result = await self._session.execute(
            select(WeatherDailyRow.cell_id).where(WeatherDailyRow.cell_id == cell_id).limit(1)
        )
        return result.scalar_one_or_none() is None

    async def list_daily(
        self, cell_id: int, from_day: datetime.date, to_day: datetime.date
    ) -> list[WeatherDay]:
        """The inclusive `[from_day, to_day]` window, ordered by day, then
        observed before forecast for the same day (`is_forecast` false first).

        Reads every column instead of whole rows: no ORM row is built, only the
        domain dataclass the caller asked for.
        """
        result = await self._session.execute(
            select(*_DAILY_COLUMNS)
            .where(
                WeatherDailyRow.cell_id == cell_id,
                WeatherDailyRow.day >= from_day,
                WeatherDailyRow.day <= to_day,
            )
            .order_by(WeatherDailyRow.day, WeatherDailyRow.is_forecast)
        )
        return [_day_from_row(row) for row in result]

    async def missing_observed_days(
        self, cell_id: int, from_day: datetime.date, to_day: datetime.date
    ) -> list[datetime.date]:
        """Days in the inclusive `[from_day, to_day]` window with no observed
        (`is_forecast = false`) row for this cell — what the daily
        consolidation retries alongside its target day, so a day degraded by a
        provider outage (docs/06-diseno-detallado.md §6) does not stay a
        permanent gap for E6's water balance (issue #82)."""
        result = await self._session.execute(
            select(WeatherDailyRow.day).where(
                WeatherDailyRow.cell_id == cell_id,
                WeatherDailyRow.is_forecast.is_(False),
                WeatherDailyRow.day >= from_day,
                WeatherDailyRow.day <= to_day,
            )
        )
        observed = set(result.scalars())
        total_days = (to_day - from_day).days + 1
        return [
            day
            for offset in range(total_days)
            if (day := from_day + datetime.timedelta(days=offset)) not in observed
        ]

    async def active_cell_ids(self) -> list[int]:
        """Ids of the cells at least one plot points at: the cells the 3 h
        forecast job refreshes (docs/06-diseno-detallado.md §6). A cell nobody's
        plot falls into is not worth a provider call."""
        result = await self._session.execute(
            select(WeatherCellRow.id)
            .where(WeatherCellRow.id.in_(select(PlotRow.weather_cell_id)))
            .order_by(WeatherCellRow.id)
        )
        return list(result.scalars())
