"""Ingest pipeline core (docs/06-diseno-detallado.md §1): validate → map
`channel_key` → calibrate → quality → batch insert → node status → NOTIFY.
Tested against real Postgres, no broker (T4 task instruction): messages are
handed in already "received" by a fake MQTT layer."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.repositories import SqlAlchemyPlotRepository
from techcamp.shared.config import database_url
from techcamp.telemetry.adapters.repositories import (
    SqlAlchemyCalibrationRepository,
    SqlAlchemyNodeRepository,
    SqlAlchemyPlotEventsNotifier,
    SqlAlchemyReadingRepository,
    SqlAlchemySensorRepository,
)
from techcamp.telemetry.application.ingest_uplinks import (
    RawStatusMessage,
    RawUplink,
    ingest_status_messages,
    ingest_uplinks,
)
from techcamp.telemetry.domain.models import CalibrationKind, CalibrationMethod, NodeStatus

from .test_repositories import _make_node, _make_org_and_plot, _make_sensor

pytestmark = pytest.mark.anyio

_RECEIVED_AT = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _uplink_payload(**overrides: object) -> bytes:
    payload: dict[str, object] = {
        "v": 1,
        "seq": 1,
        "ts": int(_RECEIVED_AT.timestamp()),
        "fw": "1.0.3",
        "m": {"sm_10": 2310.0},
    }
    payload.update(overrides)
    return json.dumps(payload).encode()


async def _ports(db_session: AsyncSession) -> dict[str, object]:
    return {
        "nodes": SqlAlchemyNodeRepository(db_session),
        "sensors": SqlAlchemySensorRepository(db_session),
        "calibrations": SqlAlchemyCalibrationRepository(db_session),
        "readings": SqlAlchemyReadingRepository(db_session),
        "plots": SqlAlchemyPlotRepository(db_session),
        "events": SqlAlchemyPlotEventsNotifier(db_session),
    }


async def _claimed_node_with_sensor(
    db_session: AsyncSession, *, channel_key: str = "sm_10", unit: str = "%"
) -> tuple[object, object, object, int]:
    org_id, plot_id = await _make_org_and_plot(db_session)
    node_id = await _make_node(db_session, org_id, plot_id)
    sensor_id = await _make_sensor(db_session, node_id, channel_key=channel_key)
    from techcamp.telemetry.adapters.orm import SensorRow

    await db_session.execute(
        SensorRow.__table__.update().where(SensorRow.id == sensor_id).values(unit=unit)
    )
    await db_session.commit()
    return org_id, plot_id, node_id, sensor_id


async def _add_calibration(
    db_session: AsyncSession, *, org_id: object, sensor_id: int, valid_from: datetime
) -> None:
    await SqlAlchemyCalibrationRepository(db_session).add_version(
        org_id=org_id,
        sensor_id=sensor_id,
        version=1,
        method=CalibrationMethod.LINEAR,
        kind=CalibrationKind.FIELD,
        params={"scale": 0.02, "offset": 0.0},
        rmse_pct=None,
        valid_from=valid_from,
    )


# -- valid message → calibrated reading row --


async def test_ingest_uplinks_stores_a_calibrated_reading(db_session: AsyncSession) -> None:
    org_id, plot_id, node_id, sensor_id = await _claimed_node_with_sensor(db_session)
    await _add_calibration(
        db_session, org_id=org_id, sensor_id=sensor_id, valid_from=datetime(2026, 1, 1, tzinfo=UTC)
    )
    ports = await _ports(db_session)

    stats = await ingest_uplinks(
        [RawUplink(node_id=node_id, payload=_uplink_payload(), received_at=_RECEIVED_AT)], **ports
    )

    assert stats.inserted == 1
    result = await db_session.execute(text("SELECT raw_value, value, quality FROM reading"))
    row = result.one()
    assert row.raw_value == 2310.0
    assert row.value == pytest.approx(46.2)  # 0.02 * 2310 + 0
    assert row.quality == 0


# -- duplicate message → one row --


async def test_ingest_uplinks_is_idempotent_across_two_flushes(db_session: AsyncSession) -> None:
    org_id, plot_id, node_id, sensor_id = await _claimed_node_with_sensor(db_session)
    await _add_calibration(
        db_session, org_id=org_id, sensor_id=sensor_id, valid_from=datetime(2026, 1, 1, tzinfo=UTC)
    )
    ports = await _ports(db_session)
    message = RawUplink(node_id=node_id, payload=_uplink_payload(), received_at=_RECEIVED_AT)

    notifications: list[str] = []

    def _on_notify(_connection: object, _pid: int, _channel: str, payload: str) -> None:
        notifications.append(payload)

    listener = await asyncpg.connect(dsn=_dsn())
    await listener.add_listener("plot_events", _on_notify)
    try:
        first = await ingest_uplinks([message], **ports)
        second = await ingest_uplinks([message], **ports)
        await asyncio.sleep(0.2)
    finally:
        await listener.close()

    assert first.inserted == 1
    assert second.inserted == 0
    # GitHub #36: the redelivered flush inserted nothing, so it must not
    # re-notify the SSE fan-out with a duplicate `reading` event.
    reading_notifications = [p for p in notifications if json.loads(p)["type"] == "reading"]
    assert len(reading_notifications) == 1


# -- GitHub #36: one unusable `ts` is classified, not fatal to the batch --


async def test_ingest_uplinks_keeps_the_batch_when_one_ts_is_unusable(
    db_session: AsyncSession,
) -> None:
    org_id, plot_id, node_id, sensor_id = await _claimed_node_with_sensor(db_session)
    await _add_calibration(
        db_session, org_id=org_id, sensor_id=sensor_id, valid_from=datetime(2026, 1, 1, tzinfo=UTC)
    )
    ports = await _ports(db_session)

    stats = await ingest_uplinks(
        [
            RawUplink(
                node_id=node_id, payload=_uplink_payload(ts=10**20), received_at=_RECEIVED_AT
            ),
            RawUplink(node_id=node_id, payload=_uplink_payload(seq=2), received_at=_RECEIVED_AT),
        ],
        **ports,
    )

    assert stats.counts["malformed_payload"] == 1
    assert stats.inserted == 1  # the good message of the same batch still landed


# -- future/missing ts → received_at + quality 1 --


async def test_ingest_uplinks_falls_back_to_received_at_for_a_far_future_ts(
    db_session: AsyncSession,
) -> None:
    org_id, plot_id, node_id, sensor_id = await _claimed_node_with_sensor(db_session)
    await _add_calibration(
        db_session, org_id=org_id, sensor_id=sensor_id, valid_from=datetime(2026, 1, 1, tzinfo=UTC)
    )
    ports = await _ports(db_session)
    far_future_ts = int(_RECEIVED_AT.timestamp()) + 3600

    stats = await ingest_uplinks(
        [
            RawUplink(
                node_id=node_id, payload=_uplink_payload(ts=far_future_ts), received_at=_RECEIVED_AT
            )
        ],
        **ports,
    )

    assert stats.inserted == 1
    result = await db_session.execute(text("SELECT time, quality FROM reading"))
    row = result.one()
    assert row.time == _RECEIVED_AT
    assert row.quality == 1


async def test_ingest_uplinks_falls_back_to_received_at_for_a_missing_ts(
    db_session: AsyncSession,
) -> None:
    org_id, plot_id, node_id, sensor_id = await _claimed_node_with_sensor(db_session)
    await _add_calibration(
        db_session, org_id=org_id, sensor_id=sensor_id, valid_from=datetime(2026, 1, 1, tzinfo=UTC)
    )
    ports = await _ports(db_session)

    stats = await ingest_uplinks(
        [RawUplink(node_id=node_id, payload=_uplink_payload(ts=None), received_at=_RECEIVED_AT)],
        **ports,
    )

    assert stats.inserted == 1
    result = await db_session.execute(text("SELECT time, quality FROM reading"))
    row = result.one()
    assert row.time == _RECEIVED_AT
    assert row.quality == 1


# -- >30-day ts discarded outright --


async def test_ingest_uplinks_discards_a_reading_older_than_30_days(
    db_session: AsyncSession,
) -> None:
    org_id, plot_id, node_id, sensor_id = await _claimed_node_with_sensor(db_session)
    await _add_calibration(
        db_session, org_id=org_id, sensor_id=sensor_id, valid_from=datetime(2026, 1, 1, tzinfo=UTC)
    )
    ports = await _ports(db_session)
    old_ts = int((_RECEIVED_AT - timedelta(days=31)).timestamp())

    stats = await ingest_uplinks(
        [RawUplink(node_id=node_id, payload=_uplink_payload(ts=old_ts), received_at=_RECEIVED_AT)],
        **ports,
    )

    assert stats.inserted == 0
    assert stats.counts["too_old"] == 1


# -- unknown v / malformed JSON / unknown node / unclaimed node discarded without raising --


async def test_ingest_uplinks_discards_an_unsupported_version_without_raising(
    db_session: AsyncSession,
) -> None:
    org_id, plot_id, node_id, sensor_id = await _claimed_node_with_sensor(db_session)
    ports = await _ports(db_session)

    stats = await ingest_uplinks(
        [RawUplink(node_id=node_id, payload=_uplink_payload(v=2), received_at=_RECEIVED_AT)],
        **ports,
    )

    assert stats.inserted == 0
    assert stats.counts["unsupported_version"] == 1


async def test_ingest_uplinks_discards_malformed_json_without_raising(
    db_session: AsyncSession,
) -> None:
    org_id, plot_id, node_id, sensor_id = await _claimed_node_with_sensor(db_session)
    ports = await _ports(db_session)

    stats = await ingest_uplinks(
        [RawUplink(node_id=node_id, payload=b"{not json", received_at=_RECEIVED_AT)], **ports
    )

    assert stats.inserted == 0
    assert stats.counts["malformed_json"] == 1


async def test_ingest_uplinks_discards_an_unknown_node_without_raising(
    db_session: AsyncSession,
) -> None:
    from uuid import uuid4

    ports = await _ports(db_session)

    stats = await ingest_uplinks(
        [RawUplink(node_id=uuid4(), payload=_uplink_payload(), received_at=_RECEIVED_AT)], **ports
    )

    assert stats.inserted == 0
    assert stats.counts["unclaimed_or_unknown_node"] == 1


async def test_ingest_uplinks_discards_an_unclaimed_node_without_raising(
    db_session: AsyncSession,
) -> None:
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
            claim_code="UNCLAIMED-INGEST",
            credential_hash="hash",
            firmware=None,
            interval_s=300,
            claimed_at=None,
            last_seen_at=None,
            status="provisioned",
        )
    )
    await db_session.commit()
    ports = await _ports(db_session)

    stats = await ingest_uplinks(
        [RawUplink(node_id=node_id, payload=_uplink_payload(), received_at=_RECEIVED_AT)], **ports
    )

    assert stats.inserted == 0
    assert stats.counts["unclaimed_or_unknown_node"] == 1


# -- unknown channel skipped, other channels of the same message still land --


async def test_ingest_uplinks_skips_an_unknown_channel(db_session: AsyncSession) -> None:
    org_id, plot_id, node_id, sensor_id = await _claimed_node_with_sensor(db_session)
    await _add_calibration(
        db_session, org_id=org_id, sensor_id=sensor_id, valid_from=datetime(2026, 1, 1, tzinfo=UTC)
    )
    ports = await _ports(db_session)

    stats = await ingest_uplinks(
        [
            RawUplink(
                node_id=node_id,
                payload=_uplink_payload(m={"sm_10": 2310.0, "ghost_channel": 5.0}),
                received_at=_RECEIVED_AT,
            )
        ],
        **ports,
    )

    assert stats.inserted == 1  # sm_10 lands
    assert stats.counts["unknown_channel"] == 1  # ghost_channel is skipped, not fatal


# -- last_seen/status updated --


async def test_ingest_uplinks_marks_the_node_online_and_seen(db_session: AsyncSession) -> None:
    org_id, plot_id, node_id, sensor_id = await _claimed_node_with_sensor(db_session)
    await _add_calibration(
        db_session, org_id=org_id, sensor_id=sensor_id, valid_from=datetime(2026, 1, 1, tzinfo=UTC)
    )
    ports = await _ports(db_session)

    await ingest_uplinks(
        [RawUplink(node_id=node_id, payload=_uplink_payload(), received_at=_RECEIVED_AT)], **ports
    )

    node = await SqlAlchemyNodeRepository(db_session).get(node_id, org_id)
    assert node is not None
    assert node.status is NodeStatus.ONLINE
    assert node.last_seen_at == _RECEIVED_AT


# -- no calibration valid at reading time: stored raw, value null (T4 decision) --


async def test_ingest_uplinks_stores_raw_value_when_uncalibrated(db_session: AsyncSession) -> None:
    _org_id, _plot_id, node_id, _sensor_id = await _claimed_node_with_sensor(db_session)
    ports = await _ports(db_session)

    stats = await ingest_uplinks(
        [RawUplink(node_id=node_id, payload=_uplink_payload(), received_at=_RECEIVED_AT)], **ports
    )

    assert stats.inserted == 1
    result = await db_session.execute(text("SELECT raw_value, value FROM reading"))
    row = result.one()
    assert row.raw_value == 2310.0
    assert row.value is None


# -- NOTIFY: a reading event and a node.status event land on `plot_events` --


def _dsn() -> str:
    return database_url().replace("postgresql+asyncpg://", "postgresql://")


async def test_ingest_uplinks_publishes_reading_and_status_notifications(
    db_session: AsyncSession,
) -> None:
    org_id, plot_id, node_id, sensor_id = await _claimed_node_with_sensor(db_session)
    await _add_calibration(
        db_session, org_id=org_id, sensor_id=sensor_id, valid_from=datetime(2026, 1, 1, tzinfo=UTC)
    )
    ports = await _ports(db_session)

    notifications: list[str] = []

    def _on_notify(_connection: object, _pid: int, _channel: str, payload: str) -> None:
        notifications.append(payload)

    listener = await asyncpg.connect(dsn=_dsn())
    await listener.add_listener("plot_events", _on_notify)
    try:
        await ingest_uplinks(
            [RawUplink(node_id=node_id, payload=_uplink_payload(), received_at=_RECEIVED_AT)],
            **ports,
        )
        await asyncio.sleep(0.2)
    finally:
        await listener.close()

    types = {json.loads(p)["type"] for p in notifications}
    assert types == {"reading", "node.status"}
    reading = next(json.loads(p) for p in notifications if json.loads(p)["type"] == "reading")
    assert reading["plot_id"] == str(plot_id)
    assert reading["farm_id"]


# -- status topic: online/offline updates node.status/last_seen_at, emits node.status --


async def test_ingest_status_messages_updates_node_and_publishes_event(
    db_session: AsyncSession,
) -> None:
    org_id, plot_id, node_id, _sensor_id = await _claimed_node_with_sensor(db_session)
    plots = SqlAlchemyPlotRepository(db_session)
    events = SqlAlchemyPlotEventsNotifier(db_session)
    nodes = SqlAlchemyNodeRepository(db_session)

    notifications: list[str] = []

    def _on_notify(_connection: object, _pid: int, _channel: str, payload: str) -> None:
        notifications.append(payload)

    listener = await asyncpg.connect(dsn=_dsn())
    await listener.add_listener("plot_events", _on_notify)
    try:
        stats = await ingest_status_messages(
            [RawStatusMessage(node_id=node_id, payload=b"offline", received_at=_RECEIVED_AT)],
            nodes=nodes,
            plots=plots,
            events=events,
        )
        await asyncio.sleep(0.2)
    finally:
        await listener.close()

    assert stats.counts.get("unclaimed_or_unknown_node", 0) == 0
    node = await nodes.get(node_id, org_id)
    assert node is not None
    assert node.status is NodeStatus.OFFLINE
    assert len(notifications) == 1
    payload = json.loads(notifications[0])
    assert payload["type"] == "node.status"
    assert payload["status"] == "offline"


async def test_an_uplink_older_than_a_last_will_offline_publishes_no_status_event(
    db_session: AsyncSession,
) -> None:
    """GitHub #36: the two batchers flush independently, so an uplink received
    before a Last Will `offline` can be written after it. Neither the stored
    node nor the SSE fan-out may go back to `online` with the older time."""
    org_id, plot_id, node_id, sensor_id = await _claimed_node_with_sensor(db_session)
    ports = await _ports(db_session)
    nodes = SqlAlchemyNodeRepository(db_session)
    uplink = RawUplink(
        node_id=node_id,
        payload=_uplink_payload(),
        received_at=_RECEIVED_AT - timedelta(seconds=60),
    )

    notifications: list[str] = []

    def _on_notify(_connection: object, _pid: int, _channel: str, payload: str) -> None:
        notifications.append(payload)

    listener = await asyncpg.connect(dsn=_dsn())
    await listener.add_listener("plot_events", _on_notify)
    try:
        await ingest_status_messages(
            [RawStatusMessage(node_id=node_id, payload=b"offline", received_at=_RECEIVED_AT)],
            nodes=nodes,
            plots=ports["plots"],
            events=ports["events"],
        )
        await ingest_uplinks([uplink], **ports)
        await asyncio.sleep(0.2)
    finally:
        await listener.close()

    node = await nodes.get(node_id, org_id)
    assert node is not None
    assert node.status is NodeStatus.OFFLINE
    assert node.last_seen_at == _RECEIVED_AT
    statuses = [json.loads(p) for p in notifications]
    assert [p["status"] for p in statuses if p["type"] == "node.status"] == ["offline"]


async def test_a_last_will_offline_older_than_a_newer_uplink_publishes_only_the_uplink_status(
    db_session: AsyncSession,
) -> None:
    """GitHub #58: the inverse ordering of the test above, the one the uplink
    batcher produces when it flushes first and the broker's retained Last Will
    arrives behind it. The uplink's newer `last_seen_at` is already stored, so
    the `offline` is stale: the node stays `online` and the fan-out carries only
    the uplink's own status (a rejected update must never publish a
    `node.status` that moves every SSE client's state backwards)."""
    org_id, _plot_id, node_id, _sensor_id = await _claimed_node_with_sensor(db_session)
    ports = await _ports(db_session)
    nodes = SqlAlchemyNodeRepository(db_session)

    notifications: list[str] = []

    def _on_notify(_connection: object, _pid: int, _channel: str, payload: str) -> None:
        notifications.append(payload)

    listener = await asyncpg.connect(dsn=_dsn())
    await listener.add_listener("plot_events", _on_notify)
    try:
        await ingest_uplinks(
            [RawUplink(node_id=node_id, payload=_uplink_payload(), received_at=_RECEIVED_AT)],
            **ports,
        )
        await ingest_status_messages(
            [
                RawStatusMessage(
                    node_id=node_id,
                    payload=b"offline",
                    received_at=_RECEIVED_AT - timedelta(seconds=60),
                )
            ],
            nodes=nodes,
            plots=ports["plots"],
            events=ports["events"],
        )
        await asyncio.sleep(0.2)
    finally:
        await listener.close()

    node = await nodes.get(node_id, org_id)
    assert node is not None
    assert node.status is NodeStatus.ONLINE
    assert node.last_seen_at == _RECEIVED_AT
    statuses = [json.loads(p) for p in notifications]
    assert [p["status"] for p in statuses if p["type"] == "node.status"] == ["online"]


async def test_ingest_status_messages_discards_malformed_status_without_raising(
    db_session: AsyncSession,
) -> None:
    _org_id, _plot_id, node_id, _sensor_id = await _claimed_node_with_sensor(db_session)
    plots = SqlAlchemyPlotRepository(db_session)
    events = SqlAlchemyPlotEventsNotifier(db_session)
    nodes = SqlAlchemyNodeRepository(db_session)

    stats = await ingest_status_messages(
        [RawStatusMessage(node_id=node_id, payload=b"sideways", received_at=_RECEIVED_AT)],
        nodes=nodes,
        plots=plots,
        events=events,
    )

    assert stats.counts["malformed_status"] == 1
