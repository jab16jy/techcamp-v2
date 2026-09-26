"""E4 T8: orchestrating backfill/live publishing through the `UplinkPublisher`
port with an injected double — MQTT is external I/O (ADR-0002)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from techcamp.simulator.runner import publish_backfill, publish_live

pytestmark = pytest.mark.anyio

_NODE_ID = UUID("00000000-0000-0000-0000-000000000002")
_SENSORS = [{"id": 1, "channel_key": "sm_10", "unit": "%"}]


@dataclass
class _RecordingPublisher:
    uplinks: list[dict[str, Any]] = field(default_factory=list)

    async def publish_uplink(self, node_id: UUID, payload: dict[str, Any]) -> None:
        self.uplinks.append(payload)

    async def publish_status(self, node_id: UUID, status: str) -> None:
        pass


async def test_publish_backfill_publishes_one_uplink_per_timestamp_with_increasing_seq() -> None:
    publisher = _RecordingPublisher()
    now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)

    next_seq = await publish_backfill(
        publisher, _NODE_ID, sensors=_SENSORS, days=1, interval_s=900, seed=1, now=now, start_seq=1
    )

    assert len(publisher.uplinks) == 96  # 86400 / 900
    assert [u["seq"] for u in publisher.uplinks] == list(range(1, 97))
    assert next_seq == 97
    assert all(u["m"].keys() == {"sm_10"} for u in publisher.uplinks)
    assert publisher.uplinks[0]["ts"] < publisher.uplinks[-1]["ts"]


async def test_publish_backfill_is_a_no_op_for_zero_days() -> None:
    publisher = _RecordingPublisher()
    now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)

    next_seq = await publish_backfill(
        publisher, _NODE_ID, sensors=_SENSORS, days=0, interval_s=900, seed=1, now=now, start_seq=5
    )

    assert publisher.uplinks == []
    assert next_seq == 5


async def test_publish_live_publishes_one_uplink_per_iteration_sleeping_between_each() -> None:
    publisher = _RecordingPublisher()
    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    await publish_live(
        publisher,
        _NODE_ID,
        sensors=_SENSORS,
        seed=1,
        start_seq=97,
        interval_s=5,
        iterations=3,
        sleep=_fake_sleep,
    )

    assert [u["seq"] for u in publisher.uplinks] == [97, 98, 99]
    assert sleeps == [5, 5, 5]
