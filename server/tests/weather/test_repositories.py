"""Weather cell and daily table behavior (docs/03-modelo-datos.md:176-191).

Every test runs against real Postgres: `conftest._migrated_schema` migrates to
`head` once per session and downgrades to `base` at teardown, so a broken
upgrade or downgrade fails the whole suite, not just a dedicated test.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import OrganizationRow
from techcamp.shared.db import async_session_factory
from techcamp.shared.ids import uuid7
from techcamp.weather.adapters.repositories import SqlAlchemyWeatherRepository
from techcamp.weather.domain.models import WeatherDay

pytestmark = pytest.mark.anyio

_CELL_LAT = Decimal("10.9")
_CELL_LON = Decimal("-74.1")
_OTHER_CELL_LON = Decimal("-74.2")

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)


@pytest.fixture
async def db_session(db_session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """Also clears the weather tables: they carry no `org_id`, so the shared
    `db_session` teardown (which truncates `organization ... CASCADE`) never
    reaches them and cells would leak between tests."""
    yield db_session
    # A test that expected an IntegrityError leaves the session mid-rollback;
    # clear it so the truncate below runs instead of raising PendingRollbackError.
    await db_session.rollback()
    await db_session.execute(text("TRUNCATE weather_daily, weather_cell CASCADE"))
    await db_session.commit()


async def _make_plot(db_session: AsyncSession, *, weather_cell_id: int | None) -> UUID:
    org_id = uuid7()
    db_session.add(OrganizationRow(id=org_id, name="Finca", kind="individual"))
    await db_session.commit()
    farm_id = uuid7()
    db_session.add(
        FarmRow(id=farm_id, org_id=org_id, name="Finca", municipality_code="47001", location=_POINT)
    )
    await db_session.commit()
    plot_id = uuid7()
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=_BOUNDARY,
            irrigation_system="none",
            weather_cell_id=weather_cell_id,
        )
    )
    await db_session.commit()
    return plot_id


def _day(day: date, *, is_forecast: bool, et0_mm: float, fetched_at: datetime) -> WeatherDay:
    return WeatherDay(
        day=day,
        is_forecast=is_forecast,
        et0_mm=et0_mm,
        rain_mm=0.0,
        tmin_c=25.0,
        tmax_c=31.0,
        rh_mean_pct=80.0,
        fetched_at=fetched_at,
    )


async def test_get_or_create_cell_reuses_the_cell_for_the_same_coordinates(
    db_session: AsyncSession,
) -> None:
    repository = SqlAlchemyWeatherRepository(db_session)

    first = await repository.get_or_create_cell(_CELL_LAT, _CELL_LON)
    second = await repository.get_or_create_cell(_CELL_LAT, _CELL_LON)

    assert first == second


async def test_get_or_create_cell_keeps_distinct_coordinates_apart(
    db_session: AsyncSession,
) -> None:
    repository = SqlAlchemyWeatherRepository(db_session)

    first = await repository.get_or_create_cell(_CELL_LAT, _CELL_LON)
    second = await repository.get_or_create_cell(_CELL_LAT, _OTHER_CELL_LON)

    assert first != second


async def test_get_or_create_cell_is_idempotent_under_concurrency(
    db_session: AsyncSession,
) -> None:
    """Two cells assigned at the same time must share one row: the cell is
    the Open-Meteo cache (docs/09-cuellos-de-botella.md:39), so a duplicate
    would mean two provider calls for the same 0.1° square."""
    async with async_session_factory() as first, async_session_factory() as second:
        ids = await asyncio.gather(
            SqlAlchemyWeatherRepository(first).get_or_create_cell(_CELL_LAT, _CELL_LON),
            SqlAlchemyWeatherRepository(second).get_or_create_cell(_CELL_LAT, _CELL_LON),
        )

    assert ids[0] == ids[1]
    cells = await db_session.execute(text("SELECT count(*) FROM weather_cell"))
    assert cells.scalar_one() == 1


async def test_plot_rejects_a_weather_cell_that_does_not_exist(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(IntegrityError):
        await _make_plot(db_session, weather_cell_id=999_999)


async def test_upsert_daily_inserts_and_then_updates_the_same_row(
    db_session: AsyncSession,
) -> None:
    """A 3 h refresh writes the same `(cell_id, day, is_forecast)` again
    (docs/06-diseno-detallado.md §6): the row is updated, never duplicated."""
    repository = SqlAlchemyWeatherRepository(db_session)
    cell_id = await repository.get_or_create_cell(_CELL_LAT, _CELL_LON)
    day = date(2026, 9, 26)

    await repository.upsert_daily(
        cell_id,
        [_day(day, is_forecast=True, et0_mm=4.0, fetched_at=datetime(2026, 9, 26, 6, tzinfo=UTC))],
    )
    await repository.upsert_daily(
        cell_id,
        [_day(day, is_forecast=True, et0_mm=5.5, fetched_at=datetime(2026, 9, 26, 9, tzinfo=UTC))],
    )

    stored = await repository.list_daily(cell_id, day, day)
    assert len(stored) == 1
    assert stored[0].et0_mm == pytest.approx(5.5)
    assert stored[0].fetched_at == datetime(2026, 9, 26, 9, tzinfo=UTC)


async def test_upsert_daily_keeps_observed_and_forecast_rows_of_one_day_apart(
    db_session: AsyncSession,
) -> None:
    """`is_forecast` is part of the primary key (docs/03-modelo-datos.md:180):
    the consolidated observed row and the forecast row for the same day are
    two different rows, not an update of one another."""
    repository = SqlAlchemyWeatherRepository(db_session)
    cell_id = await repository.get_or_create_cell(_CELL_LAT, _CELL_LON)
    day = date(2026, 9, 26)
    fetched_at = datetime(2026, 9, 26, 6, tzinfo=UTC)

    await repository.upsert_daily(
        cell_id,
        [
            _day(day, is_forecast=True, et0_mm=4.0, fetched_at=fetched_at),
            _day(day, is_forecast=False, et0_mm=3.9, fetched_at=fetched_at),
        ],
    )

    stored = await repository.list_daily(cell_id, day, day)
    assert [(row.is_forecast, row.et0_mm) for row in stored] == [(False, 3.9), (True, 4.0)]


async def test_list_daily_returns_only_the_requested_window_ordered_by_day(
    db_session: AsyncSession,
) -> None:
    repository = SqlAlchemyWeatherRepository(db_session)
    cell_id = await repository.get_or_create_cell(_CELL_LAT, _CELL_LON)
    first_day = date(2026, 9, 26)
    fetched_at = datetime(2026, 9, 26, 6, tzinfo=UTC)
    await repository.upsert_daily(
        cell_id,
        [
            _day(first_day, is_forecast=True, et0_mm=1.0, fetched_at=fetched_at),
            _day(
                first_day + timedelta(days=2), is_forecast=True, et0_mm=3.0, fetched_at=fetched_at
            ),
            _day(
                first_day + timedelta(days=1), is_forecast=True, et0_mm=2.0, fetched_at=fetched_at
            ),
        ],
    )

    window = await repository.list_daily(cell_id, first_day, first_day + timedelta(days=1))

    assert [row.day for row in window] == [first_day, first_day + timedelta(days=1)]
    assert all(isinstance(row, WeatherDay) for row in window)


async def test_active_cell_ids_returns_only_cells_a_plot_references(
    db_session: AsyncSession,
) -> None:
    """Active cells drive the 3 h refresh job (docs/06-diseno-detallado.md §6):
    a cell nobody's plot falls into is not worth a provider call."""
    repository = SqlAlchemyWeatherRepository(db_session)
    referenced = await repository.get_or_create_cell(_CELL_LAT, _CELL_LON)
    await repository.get_or_create_cell(_CELL_LAT, _OTHER_CELL_LON)
    await _make_plot(db_session, weather_cell_id=referenced)

    assert await repository.active_cell_ids() == [referenced]
