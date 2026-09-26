"""New T4 adapters: `get_by_id`/`mark_seen_batch` on the node repository, the
reading repository's batched idempotent insert, and the `plot_events`
notifier (docs/06-diseno-detallado.md §1)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.shared.config import database_url
from techcamp.telemetry.adapters.repositories import (
    SqlAlchemyNodeRepository,
    SqlAlchemyPlotEventsNotifier,
    SqlAlchemyReadingRepository,
)
from techcamp.telemetry.domain.models import (
    NodeSeenUpdate,
    NodeStatus,
    NodeStatusEvent,
    ReadingEvent,
    ReadingQuality,
    ReadingRecord,
)

from .test_repositories import _make_node, _make_org_and_plot, _make_sensor

pytestmark = pytest.mark.anyio


async def test_get_by_id_finds_a_claimed_node_without_an_org_filter(
    db_session: AsyncSession,
) -> None:
    org_id, plot_id = await _make_org_and_plot(db_session)
    node_id = await _make_node(db_session, org_id, plot_id)

    node = await SqlAlchemyNodeRepository(db_session).get_by_id(node_id)

    assert node is not None
    assert node.org_id == org_id


async def test_get_by_id_finds_an_unclaimed_node(db_session: AsyncSession) -> None:
    """GitHub #36: the ingestor looks a node up before it knows any org, so
    `get_by_id` must find a node that nobody has claimed yet (org_id/plot_id
    are all null before `POST /nodes:claim`)."""
    from uuid import uuid4

    from techcamp.shared.ids import uuid7
    from techcamp.telemetry.adapters.orm import NodeRow

    node_id = uuid7()
    db_session.add(
        NodeRow(
            id=node_id,
            org_id=None,
            plot_id=None,
            transport="wifi",
            dev_eui=None,
            claim_code="UNCLAIMED-LOOKUP",
            credential_hash="hash",
            firmware=None,
            interval_s=300,
            claimed_at=None,
            last_seen_at=None,
            status="provisioned",
        )
    )
    await db_session.commit()
    repo = SqlAlchemyNodeRepository(db_session)

    unclaimed = await repo.get_by_id(node_id)
    assert unclaimed is not None
    assert unclaimed.org_id is None
    assert unclaimed.plot_id is None

    assert await repo.get_by_id(uuid4()) is None


async def test_mark_seen_batch_sets_last_seen_and_status(db_session: AsyncSession) -> None:
    org_id, plot_id = await _make_org_and_plot(db_session)
    node_id = await _make_node(db_session, org_id, plot_id)
    seen_at = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

    await SqlAlchemyNodeRepository(db_session).mark_seen_batch(
        [
            NodeSeenUpdate(
                node_id=node_id,
                org_id=org_id,
                plot_id=plot_id,
                last_seen_at=seen_at,
                status=NodeStatus.ONLINE,
            )
        ]
    )

    node = await SqlAlchemyNodeRepository(db_session).get(node_id, org_id)
    assert node is not None
    assert node.last_seen_at == seen_at
    assert node.status is NodeStatus.ONLINE


async def test_mark_seen_batch_ignores_an_update_older_than_the_stored_last_seen(
    db_session: AsyncSession,
) -> None:
    """GitHub #36: the uplink and status batchers flush independently, so an
    uplink received before a Last Will `offline` can be written after it.
    `last_seen_at`/`status` must never move backwards."""
    org_id, plot_id = await _make_org_and_plot(db_session)
    node_id = await _make_node(db_session, org_id, plot_id)
    newer = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    repo = SqlAlchemyNodeRepository(db_session)

    first = await repo.mark_seen_batch(
        [
            NodeSeenUpdate(
                node_id=node_id,
                org_id=org_id,
                plot_id=plot_id,
                last_seen_at=newer,
                status=NodeStatus.OFFLINE,
            )
        ]
    )
    second = await repo.mark_seen_batch(
        [
            NodeSeenUpdate(
                node_id=node_id,
                org_id=org_id,
                plot_id=plot_id,
                last_seen_at=newer - timedelta(seconds=60),
                status=NodeStatus.ONLINE,
            )
        ]
    )

    assert first == {node_id}
    assert second == set()  # nothing written: the caller publishes no event for it

    node = await repo.get(node_id, org_id)
    assert node is not None
    assert node.last_seen_at == newer
    assert node.status is NodeStatus.OFFLINE


async def test_reading_repository_insert_batch_is_idempotent(db_session: AsyncSession) -> None:
    org_id, plot_id = await _make_org_and_plot(db_session)
    node_id = await _make_node(db_session, org_id, plot_id)
    sensor_id = await _make_sensor(db_session, node_id)
    at = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    record = ReadingRecord(
        sensor_id=sensor_id,
        time=at,
        raw_value=2310.0,
        value=42.5,
        received_at=at,
        quality=ReadingQuality.OK,
    )

    repo = SqlAlchemyReadingRepository(db_session)
    first = await repo.insert_batch([record])
    second = await repo.insert_batch([record])  # duplicate: same (sensor_id, time)

    assert first == {(sensor_id, at)}
    assert second == set()  # ON CONFLICT DO NOTHING: no second row, nothing to notify


async def test_reading_repository_insert_batch_handles_an_empty_list(
    db_session: AsyncSession,
) -> None:
    assert await SqlAlchemyReadingRepository(db_session).insert_batch([]) == set()


def _dsn() -> str:
    return database_url().replace("postgresql+asyncpg://", "postgresql://")


async def test_notifier_publishes_reading_and_status_events(db_session: AsyncSession) -> None:
    org_id, plot_id = await _make_org_and_plot(db_session)
    node_id = await _make_node(db_session, org_id, plot_id)
    farm_id = plot_id  # any UUID works for this notifier-only test

    notifications: list[str] = []

    def _on_notify(_connection: object, _pid: int, _channel: str, payload: str) -> None:
        notifications.append(payload)

    listener = await asyncpg.connect(dsn=_dsn())
    await listener.add_listener("plot_events", _on_notify)
    try:
        at = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
        await SqlAlchemyPlotEventsNotifier(db_session).publish(
            readings=[
                ReadingEvent(
                    org_id=org_id,
                    farm_id=farm_id,
                    plot_id=plot_id,
                    metric="soil_moisture",
                    value=42.5,
                    at=at,
                )
            ],
            statuses=[
                NodeStatusEvent(
                    org_id=org_id, farm_id=farm_id, node_id=node_id, status=NodeStatus.ONLINE, at=at
                )
            ],
        )
        await asyncio.sleep(0.2)  # let the listener connection process the NOTIFY
    finally:
        await listener.close()

    assert len(notifications) == 2
    payloads = [json.loads(p) for p in notifications]
    reading_payload = next(p for p in payloads if p["type"] == "reading")
    status_payload = next(p for p in payloads if p["type"] == "node.status")
    assert reading_payload["plot_id"] == str(plot_id)
    assert reading_payload["metric"] == "soil_moisture"
    assert reading_payload["value"] == 42.5
    assert status_payload["node_id"] == str(node_id)
    assert status_payload["status"] == "online"


async def test_notifier_publish_with_no_events_is_a_no_op(db_session: AsyncSession) -> None:
    await SqlAlchemyPlotEventsNotifier(db_session).publish(readings=[], statuses=[])
