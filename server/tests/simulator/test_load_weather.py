"""E16 T3: the scenario's weather fixture lands in `weather_daily`
(docs/04-api.md:263; D-T16.2; docs/06-diseno-detallado.md:490).

The recording's rows are keyed by the scenario's day offset, not by the
calendar dates the capture covered, so the loader places each row on the
scenario's day 0 plus its offset. The default anchor is the one that puts the
scenario's forecast window on today: the fixture's observed rows are the days
before it, so its forecast rows are today's and the six days after
(docs/06-diseno-detallado.md:246).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.simulator.application.load_weather import load_weather_fixture
from techcamp.simulator.scenario_library import load_fixture
from techcamp.weather.adapters.orm import WeatherCellRow, WeatherDailyRow
from techcamp.weather.adapters.repositories import SqlAlchemyWeatherRepository

pytestmark = pytest.mark.anyio

ANCHOR = date(2026, 10, 24)


async def test_the_rows_land_on_the_scenario_days(db_session: AsyncSession) -> None:
    """The fixture's day offset is what counts: the recording covered 2016,
    the scenario runs in 2026."""
    fixture = load_fixture("el-nino-caribe")
    weather = SqlAlchemyWeatherRepository(db_session)

    cell_id = await load_weather_fixture(fixture=fixture, day=ANCHOR, weather=weather)

    rows = (
        (
            await db_session.execute(
                select(WeatherDailyRow)
                .where(WeatherDailyRow.cell_id == cell_id)
                .order_by(WeatherDailyRow.day)
            )
        )
        .scalars()
        .all()
    )

    assert len(rows) == len(fixture.days)
    assert rows[0].day == ANCHOR
    assert rows[-1].day == ANCHOR + timedelta(days=len(fixture.days) - 1)
    assert [row.is_forecast for row in rows] == [day.is_forecast for day in fixture.days]


async def test_the_measures_are_the_recorded_ones(db_session: AsyncSession) -> None:
    """The row carries what the recording measured: the ET0, the rain and the
    temperature extremes the balance reads."""
    fixture = load_fixture("el-nino-caribe")
    weather = SqlAlchemyWeatherRepository(db_session)

    cell_id = await load_weather_fixture(fixture=fixture, day=ANCHOR, weather=weather)

    row = (
        await db_session.execute(
            select(WeatherDailyRow)
            .where(WeatherDailyRow.cell_id == cell_id, WeatherDailyRow.day == ANCHOR)
            .limit(1)
        )
    ).scalar_one()

    assert float(row.et0_mm) == fixture.days[0].et0_mm
    assert float(row.tmax_c) == fixture.days[0].tmax_c
    assert row.fetched_at is not None


async def test_the_cell_is_the_recorded_one(db_session: AsyncSession) -> None:
    """The rows belong to the cell the fixture names, so the parcel's cell and
    the recording's cell are the same one (docs/06-diseno-detallado.md §6)."""
    fixture = load_fixture("el-nino-caribe")
    weather = SqlAlchemyWeatherRepository(db_session)

    cell_id = await load_weather_fixture(fixture=fixture, day=ANCHOR, weather=weather)

    cell = (
        await db_session.execute(select(WeatherCellRow).where(WeatherCellRow.id == cell_id))
    ).scalar_one()

    assert float(cell.lat) == fixture.lat
    assert float(cell.lon) == fixture.lon


async def test_a_second_load_writes_the_same_rows(db_session: AsyncSession) -> None:
    """The load is idempotent per (scenario, day-anchor): the rows are written
    over, not beside."""
    fixture = load_fixture("el-nino-caribe")
    weather = SqlAlchemyWeatherRepository(db_session)

    first = await load_weather_fixture(fixture=fixture, day=ANCHOR, weather=weather)
    second = await load_weather_fixture(fixture=fixture, day=ANCHOR, weather=weather)

    assert first == second
    rows = (
        (await db_session.execute(select(WeatherDailyRow).where(WeatherDailyRow.cell_id == first)))
        .scalars()
        .all()
    )
    assert len(rows) == len(fixture.days)
