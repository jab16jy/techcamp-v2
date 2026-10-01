"""Tests for telemetry home facade read queries (E9 T1a; docs/04; D-T0.4, D-T0.5)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import PlotRow
from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.orm import NodeRow, ReadingRow, SensorRow
from techcamp.telemetry.adapters.repositories import (
    SqlAlchemyNodeRepository,
    SqlAlchemyReadingRepository,
)
from techcamp.telemetry.application import (
    get_plot_nodes_health,
    query_latest_plot_readings,
)
from techcamp.telemetry.domain.models import Node, NodeStatus, NodeTransport, ReadingQuality

from .test_repositories import _make_node, _make_org_and_plot, _make_sensor

pytestmark = pytest.mark.anyio

_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)


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


async def test_query_latest_plot_readings_success_and_missing_is_none(
    db_session: AsyncSession,
) -> None:
    org_id, plot_id = await _make_org_and_plot(db_session, "Finca Telemetry 1")
    node_id = await _make_node(db_session, org_id, plot_id)
    sensor_sm = await _make_sensor(db_session, node_id, channel_key="sm_10")

    # Sensor for air_temp
    sensor_temp = SensorRow(
        node_id=cast_uuid(node_id),
        channel_key="temp_air",
        metric="air_temp",
        depth_cm=None,
        unit="c",
    )
    db_session.add(sensor_temp)
    await db_session.commit()
    await db_session.refresh(sensor_temp)

    now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)

    # Insert older and newer readings for soil_moisture within 24h
    await _insert_reading(
        db_session,
        sensor_sm,
        at=now - timedelta(hours=2),
        raw_value=25.0,
        value=25.0,
    )
    await _insert_reading(
        db_session,
        sensor_sm,
        at=now - timedelta(hours=1),
        raw_value=28.5,
        value=28.5,
    )

    repo = SqlAlchemyReadingRepository(db_session)
    res = await query_latest_plot_readings(
        plot_id=cast_uuid(plot_id),
        org_id=cast_uuid(org_id),
        metrics=["soil_moisture", "air_temp", "air_rh"],
        now=now,
        readings=repo,
    )

    # soil_moisture returns newest reading within 24h
    assert res["soil_moisture"] is not None
    assert res["soil_moisture"].value == 28.5
    assert res["soil_moisture"].time == now - timedelta(hours=1)

    # air_temp has no reading -> None (never 0)
    assert res["air_temp"] is None

    # air_rh has no sensor -> None (never 0)
    assert res["air_rh"] is None


async def test_query_latest_plot_readings_filters_out_of_range_and_older_than_24h(
    db_session: AsyncSession,
) -> None:
    org_id, plot_id = await _make_org_and_plot(db_session, "Finca Telemetry 2")
    node_id = await _make_node(db_session, org_id, plot_id)
    sensor_sm = await _make_sensor(db_session, node_id, channel_key="sm_10")

    now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)

    # Valid reading at now - 10 hours
    await _insert_reading(
        db_session,
        sensor_sm,
        at=now - timedelta(hours=10),
        raw_value=22.0,
        value=22.0,
        quality=0,
    )

    # Newer reading at now - 1 hour, but OUT OF RANGE (bit 2) -> must be ignored!
    await _insert_reading(
        db_session,
        sensor_sm,
        at=now - timedelta(hours=1),
        raw_value=999.0,
        value=999.0,
        quality=int(ReadingQuality.OUT_OF_RANGE),
    )

    # Even older reading at now - 25 hours (> 24h) -> must be ignored!
    await _insert_reading(
        db_session,
        sensor_sm,
        at=now - timedelta(hours=25),
        raw_value=30.0,
        value=30.0,
        quality=0,
    )

    repo = SqlAlchemyReadingRepository(db_session)
    res = await query_latest_plot_readings(
        plot_id=cast_uuid(plot_id),
        org_id=cast_uuid(org_id),
        metrics=["soil_moisture"],
        now=now,
        readings=repo,
    )

    # Must return the valid reading at now - 10h, NOT the out-of-range or >24h one
    assert res["soil_moisture"] is not None
    assert res["soil_moisture"].value == 22.0
    assert res["soil_moisture"].time == now - timedelta(hours=10)


async def test_query_latest_plot_readings_org_isolation(
    db_session: AsyncSession,
) -> None:
    org_a, plot_a = await _make_org_and_plot(db_session, "Finca A")
    org_b, plot_b = await _make_org_and_plot(db_session, "Finca B")

    node_a = await _make_node(db_session, org_a, plot_a)
    sensor_a = await _make_sensor(db_session, node_a)

    now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    await _insert_reading(
        db_session,
        sensor_a,
        at=now - timedelta(hours=1),
        raw_value=25.0,
        value=25.0,
    )

    repo = SqlAlchemyReadingRepository(db_session)

    # Querying plot_a with org_b must return None (org isolation)
    res_b = await query_latest_plot_readings(
        plot_id=cast_uuid(plot_a),
        org_id=cast_uuid(org_b),
        metrics=["soil_moisture"],
        now=now,
        readings=repo,
    )
    assert res_b["soil_moisture"] is None

    # Querying plot_a with org_a returns the reading
    res_a = await query_latest_plot_readings(
        plot_id=cast_uuid(plot_a),
        org_id=cast_uuid(org_a),
        metrics=["soil_moisture"],
        now=now,
        readings=repo,
    )
    assert res_a["soil_moisture"] is not None
    assert res_a["soil_moisture"].value == 25.0


async def test_query_latest_plot_readings_deterministic_tiebreak_at_same_timestamp(
    db_session: AsyncSession,
) -> None:
    org_id, plot_id = await _make_org_and_plot(db_session, "Finca Tiebreak")
    node_id = await _make_node(db_session, org_id, plot_id)
    sensor_1 = await _make_sensor(db_session, node_id, channel_key="sm_10")
    sensor_2 = await _make_sensor(db_session, node_id, channel_key="sm_20")
    assert sensor_1 < sensor_2

    now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    same_time = now - timedelta(hours=1)

    # Insert readings for both sensors at the exact same timestamp
    await _insert_reading(
        db_session,
        sensor_2,
        at=same_time,
        raw_value=20.0,
        value=20.0,
    )
    await _insert_reading(
        db_session,
        sensor_1,
        at=same_time,
        raw_value=10.0,
        value=10.0,
    )

    repo = SqlAlchemyReadingRepository(db_session)

    # DISTINCT ON (SensorRow.metric) with tiebreak by SensorRow.id picks sensor_1 deterministically
    for _ in range(2):
        res = await query_latest_plot_readings(
            plot_id=cast_uuid(plot_id),
            org_id=cast_uuid(org_id),
            metrics=["soil_moisture"],
            now=now,
            readings=repo,
        )
        assert res["soil_moisture"] is not None
        assert res["soil_moisture"].value == 10.0


async def test_get_plot_nodes_health_filters_by_plot_and_org(
    db_session: AsyncSession,
) -> None:
    org_a, plot_a1 = await _make_org_and_plot(db_session, "Finca Health A")
    _org_b, plot_b = await _make_org_and_plot(db_session, "Finca Health B")

    # Add a second plot in org_a
    plot_a1_row = await db_session.get(PlotRow, cast_uuid(plot_a1))
    assert plot_a1_row is not None
    plot_a2 = uuid7()
    db_session.add(
        PlotRow(
            id=plot_a2,
            org_id=cast_uuid(org_a),
            farm_id=plot_a1_row.farm_id,
            name="Lote A2",
            boundary=_BOUNDARY,
            irrigation_system="none",
        )
    )
    await db_session.commit()

    # Node on plot_a1
    now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
    node_a1 = await _make_node(db_session, org_a, plot_a1, claim_code="CODE-A1")
    await db_session.execute(
        update(NodeRow)
        .where(NodeRow.id == node_a1)
        .values(
            last_seen_at=now - timedelta(minutes=5),
            status=NodeStatus.ONLINE.value,
            interval_s=300,
        )
    )
    await db_session.commit()

    # Node on plot_a2
    node_a2 = await _make_node(db_session, org_a, plot_a2, claim_code="CODE-A2")
    # Node on plot_b
    node_b = await _make_node(db_session, _org_b, plot_b, claim_code="CODE-B")

    repo = SqlAlchemyNodeRepository(db_session)

    # Health for plot_a1 with org_a
    nodes_health = await get_plot_nodes_health(
        plot_id=cast_uuid(plot_a1),
        org_id=cast_uuid(org_a),
        nodes=repo,
        now=now,
    )

    # Must contain node_a1 and NOT node_a2 or node_b
    node_ids = [n.node_id for n in nodes_health]
    assert node_ids == [node_a1]
    assert node_a2 not in node_ids
    assert node_b not in node_ids

    nh = nodes_health[0]
    assert nh.node_id == node_a1
    assert nh.status == NodeStatus.ONLINE
    assert nh.last_seen_at == now - timedelta(minutes=5)
    # Completeness with 0 readings received is 0.0
    assert nh.completeness_24h == 0.0

    # Negative check: querying plot_a1 under org_b returns empty list
    empty_for_other_org = await get_plot_nodes_health(
        plot_id=cast_uuid(plot_a1),
        org_id=cast_uuid(_org_b),
        nodes=repo,
        now=now,
    )
    assert empty_for_other_org == []


class _FakePagingNodeRepo:
    def __init__(self, all_nodes: list[Node]) -> None:
        self.all_nodes = sorted(all_nodes, key=lambda n: n.id)
        self.calls: list[UUID | None] = []

    async def list_for_org(
        self,
        org_id: UUID,
        *,
        plot_id: UUID | None = None,
        status: NodeStatus | None = None,
        limit: int = 50,
        cursor: UUID | None = None,
    ) -> list[Node]:
        self.calls.append(cursor)
        candidates = [n for n in self.all_nodes if cursor is None or n.id > cursor]
        # Real repos enforce page limit (max 50)
        return candidates[: min(limit, 50)]

    async def count_readings_since(self, node_id: UUID, org_id: UUID, since: datetime) -> int:
        return 5


async def test_get_plot_nodes_health_no_silent_cap_pages_through_all_nodes() -> None:
    org_id = uuid7()
    plot_id = uuid7()
    nodes_data = [
        Node(
            id=uuid7(),
            org_id=org_id,
            plot_id=plot_id,
            transport=NodeTransport.WIFI,
            dev_eui=f"{i:016x}",
            claim_code=f"CODE-{i}",
            credential_hash="hash",
            firmware="v1",
            interval_s=300,
            claimed_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
            status=NodeStatus.ONLINE,
        )
        for i in range(60)
    ]
    repo = _FakePagingNodeRepo(nodes_data)
    result = await get_plot_nodes_health(
        plot_id=plot_id,
        org_id=org_id,
        nodes=repo,  # type: ignore[arg-type]
    )
    assert len(repo.calls) > 1
    assert len(result) == 60


def cast_uuid(val: object) -> UUID:
    if isinstance(val, UUID):
        return val
    return UUID(str(val))
