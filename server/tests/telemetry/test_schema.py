"""Hypertable/compression/continuous-aggregate schema behavior (docs/03-modelo-datos.md:376-385).

Upgrade and downgrade are exercised by every test run: `conftest._migrated_schema`
migrates to `head` once per session and downgrades to `base` at teardown, so a
broken downgrade fails the whole suite, not just a dedicated test.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import OrganizationRow
from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.orm import NodeRow, SensorRow

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)


async def _make_sensor(db_session: AsyncSession) -> int:
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
        )
    )
    await db_session.commit()
    node_id = uuid7()
    db_session.add(
        NodeRow(
            id=node_id,
            org_id=org_id,
            plot_id=plot_id,
            transport="wifi",
            dev_eui=None,
            claim_code="CODE1",
            credential_hash="hash",
            firmware=None,
            interval_s=300,
            claimed_at=None,
            last_seen_at=None,
            status="provisioned",
        )
    )
    await db_session.commit()
    sensor = SensorRow(node_id=node_id, channel_key="sm_10", metric="soil_moisture", unit="pct")
    db_session.add(sensor)
    await db_session.commit()
    await db_session.refresh(sensor)
    return sensor.id


async def test_reading_is_a_timescaledb_hypertable(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT hypertable_name FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'reading'"
        )
    )
    assert result.scalar_one_or_none() == "reading"


async def test_reading_hypertable_has_compression_enabled(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT compression_enabled FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'reading'"
        )
    )
    assert result.scalar_one() is True


async def test_reading_hourly_and_daily_continuous_aggregates_exist(
    db_session: AsyncSession,
) -> None:
    result = await db_session.execute(
        text(
            "SELECT view_name FROM timescaledb_information.continuous_aggregates ORDER BY view_name"
        )
    )
    assert result.scalars().all() == ["reading_daily", "reading_hourly"]


async def test_duplicate_reading_insert_with_on_conflict_do_nothing_keeps_one_row(
    db_session: AsyncSession,
) -> None:
    """MQTT QoS 1 delivers at least once; `ON CONFLICT DO NOTHING` on
    `UNIQUE (sensor_id, time)` is what makes ingest idempotent
    (docs/03-modelo-datos.md:382)."""
    sensor_id = await _make_sensor(db_session)
    at = datetime(2026, 1, 1, tzinfo=UTC)
    insert = text(
        "INSERT INTO reading (time, sensor_id, raw_value, value, received_at, quality) "
        "VALUES (:time, :sensor_id, :raw_value, :value, :received_at, :quality) "
        "ON CONFLICT (sensor_id, time) DO NOTHING"
    )
    params = {
        "time": at,
        "sensor_id": sensor_id,
        "raw_value": 2900.0,
        "value": 12.5,
        "received_at": at,
        "quality": 0,
    }
    await db_session.execute(insert, params)
    await db_session.execute(insert, {**params, "raw_value": 3100.0, "value": 15.0})
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT count(*), min(raw_value) FROM reading WHERE sensor_id = :sensor_id"),
        {"sensor_id": sensor_id},
    )
    count, raw_value = result.one()
    assert count == 1
    assert raw_value == pytest.approx(2900.0)


async def test_reading_quality_check_rejects_an_out_of_range_value(
    db_session: AsyncSession,
) -> None:
    sensor_id = await _make_sensor(db_session)
    at = datetime(2026, 1, 1, tzinfo=UTC)

    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO reading (time, sensor_id, raw_value, value, received_at, quality) "
                "VALUES (:time, :sensor_id, 1.0, 1.0, :time, 3)"
            ),
            {"time": at, "sensor_id": sensor_id},
        )
        await db_session.commit()


async def test_node_status_check_rejects_an_unknown_value(db_session: AsyncSession) -> None:
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
        )
    )
    await db_session.commit()
    db_session.add(
        NodeRow(
            id=uuid7(),
            org_id=org_id,
            plot_id=plot_id,
            transport="wifi",
            dev_eui=None,
            claim_code="CODE2",
            credential_hash="hash",
            firmware=None,
            interval_s=300,
            claimed_at=None,
            last_seen_at=None,
            status="not-a-status",
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()
