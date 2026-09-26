"""SQLAlchemy table mappings for weather (docs/03-modelo-datos.md:176-191).

Plain Postgres tables, no hypertable: `weather_daily` holds one row per cell
and day for a bounded 16-day forecast window, which is not a time series worth
a hypertable, and the daily water balance (E6) reads it as an ordinary
relation.

Neither table carries `org_id`: a cell is shared reference data, the same row
for every organization (docs/09-cuellos-de-botella.md:39, docs/03-modelo-datos.md:39).
"""

from __future__ import annotations

import datetime
import decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from techcamp.shared.db import Base


class WeatherCellRow(Base):
    """A 0.1° climate cell (docs/00-glosario.md:40): neighbouring plots share one
    row, and the row is the cache that keeps them to a single Open-Meteo call.

    `lat`/`lon` are the rounded grid coordinates, so `(lat, lon)` is unique:
    it is what makes `get_or_create_cell` idempotent under concurrency.
    """

    __tablename__ = "weather_cell"
    __table_args__ = (UniqueConstraint("lat", "lon", name="uq_weather_cell_lat_lon"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    lat: Mapped[decimal.Decimal] = mapped_column(Numeric, nullable=False)
    lon: Mapped[decimal.Decimal] = mapped_column(Numeric, nullable=False)


class WeatherDailyRow(Base):
    """Daily weather for a cell (docs/03-modelo-datos.md:180-191). The primary
    key is the index the daily water balance reads by
    `(cell_id, day)` (docs/03-modelo-datos.md:480)."""

    __tablename__ = "weather_daily"

    cell_id: Mapped[int] = mapped_column(ForeignKey("weather_cell.id"), primary_key=True)
    day: Mapped[datetime.date] = mapped_column(Date, primary_key=True)
    is_forecast: Mapped[bool] = mapped_column(Boolean, primary_key=True)
    et0_mm: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    rain_mm: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    tmin_c: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    tmax_c: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    rh_mean_pct: Mapped[decimal.Decimal | None] = mapped_column(Numeric, nullable=True)
    fetched_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
