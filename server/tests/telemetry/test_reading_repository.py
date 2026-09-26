"""`ReadingRepository` raw/hourly/daily queries (T5, docs/04-api.md:92-97).

`reading_hourly`/`reading_daily` are TimescaleDB continuous aggregates
(migration `8c3983dc2dfd`): they only reflect `reading` rows after a refresh.
`CALL refresh_continuous_aggregate(...)` cannot run inside a transaction
block (verified via ctx7 against timescale/timescaledb's own docs), so the
test helper below runs it over an AUTOCOMMIT connection, outside any session
transaction — the same reasoning the migration's `autocommit_block()` uses.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.shared.db import engine
from techcamp.telemetry.adapters.orm import ReadingRow
from techcamp.telemetry.adapters.repositories import SqlAlchemyReadingRepository

from .test_repositories import _make_node, _make_org_and_plot, _make_sensor

pytestmark = pytest.mark.anyio


async def _insert_reading(
    db_session: AsyncSession,
    sensor_id: int,
    *,
    at: datetime,
    raw_value: float,
    value: float | None,
    quality: int = 0,
) -> None:
    db_session.add(
        ReadingRow(
            time=at,
            sensor_id=sensor_id,
            raw_value=raw_value,
            value=value,
            received_at=at,
            quality=quality,
        )
    )
    await db_session.commit()


async def _refresh_aggregate(view: str) -> None:
    autocommit_engine = engine.execution_options(isolation_level="AUTOCOMMIT")
    async with autocommit_engine.connect() as conn:
        await conn.execute(text(f"CALL refresh_continuous_aggregate('{view}', NULL, NULL)"))


async def test_query_raw_returns_calibrated_points_ordered_and_excludes_null_value(
    db_session: AsyncSession,
) -> None:
    org_id, plot_id = await _make_org_and_plot(db_session)
    node_id = await _make_node(db_session, org_id, plot_id)
    sensor_id = await _make_sensor(db_session, node_id)
    await _insert_reading(
        db_session, sensor_id, at=datetime(2026, 3, 1, 12, tzinfo=UTC), raw_value=10.0, value=21.5
    )
    await _insert_reading(
        db_session, sensor_id, at=datetime(2026, 3, 1, 11, tzinfo=UTC), raw_value=9.0, value=20.0
    )
    # Uncalibrated (T4 decision): stored raw, `value` null, excluded from points.
    await _insert_reading(
        db_session, sensor_id, at=datetime(2026, 3, 1, 13, tzinfo=UTC), raw_value=11.0, value=None
    )

    points = await SqlAlchemyReadingRepository(db_session).query_raw(
        sensor_id, start=datetime(2026, 3, 1, tzinfo=UTC), end=datetime(2026, 3, 2, tzinfo=UTC)
    )

    assert [p.value for p in points] == [20.0, 21.5]
    assert points[0].time < points[1].time


async def test_query_raw_range_is_from_inclusive_to_exclusive(db_session: AsyncSession) -> None:
    org_id, plot_id = await _make_org_and_plot(db_session)
    node_id = await _make_node(db_session, org_id, plot_id)
    sensor_id = await _make_sensor(db_session, node_id)
    await _insert_reading(
        db_session, sensor_id, at=datetime(2026, 3, 1, tzinfo=UTC), raw_value=1.0, value=1.0
    )
    await _insert_reading(
        db_session, sensor_id, at=datetime(2026, 3, 2, tzinfo=UTC), raw_value=2.0, value=2.0
    )

    points = await SqlAlchemyReadingRepository(db_session).query_raw(
        sensor_id, start=datetime(2026, 3, 1, tzinfo=UTC), end=datetime(2026, 3, 2, tzinfo=UTC)
    )

    assert [p.value for p in points] == [1.0]


async def test_query_hourly_returns_bucket_averages_and_excludes_all_null_buckets(
    db_session: AsyncSession,
) -> None:
    org_id, plot_id = await _make_org_and_plot(db_session)
    node_id = await _make_node(db_session, org_id, plot_id)
    sensor_id = await _make_sensor(db_session, node_id)
    await _insert_reading(
        db_session, sensor_id, at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC), raw_value=1.0, value=10.0
    )
    await _insert_reading(
        db_session,
        sensor_id,
        at=datetime(2026, 3, 1, 10, 30, tzinfo=UTC),
        raw_value=2.0,
        value=20.0,
    )
    # A bucket with only an uncalibrated reading: `avg(value)` is null, excluded.
    await _insert_reading(
        db_session, sensor_id, at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC), raw_value=3.0, value=None
    )
    await _refresh_aggregate("reading_hourly")

    points = await SqlAlchemyReadingRepository(db_session).query_hourly(
        sensor_id, start=datetime(2026, 3, 1, tzinfo=UTC), end=datetime(2026, 3, 2, tzinfo=UTC)
    )

    assert len(points) == 1
    assert points[0].time == datetime(2026, 3, 1, 10, tzinfo=UTC)
    assert points[0].value == 15.0


async def test_query_daily_returns_bucket_averages(db_session: AsyncSession) -> None:
    org_id, plot_id = await _make_org_and_plot(db_session)
    node_id = await _make_node(db_session, org_id, plot_id)
    sensor_id = await _make_sensor(db_session, node_id)
    await _insert_reading(
        db_session, sensor_id, at=datetime(2026, 3, 1, 1, tzinfo=UTC), raw_value=1.0, value=10.0
    )
    await _insert_reading(
        db_session, sensor_id, at=datetime(2026, 3, 1, 23, tzinfo=UTC), raw_value=2.0, value=30.0
    )
    await _refresh_aggregate("reading_daily")

    points = await SqlAlchemyReadingRepository(db_session).query_daily(
        sensor_id, start=datetime(2026, 3, 1, tzinfo=UTC), end=datetime(2026, 3, 2, tzinfo=UTC)
    )

    assert len(points) == 1
    assert points[0].time == datetime(2026, 3, 1, tzinfo=UTC)
    assert points[0].value == 20.0
