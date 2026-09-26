"""E4 T8: orchestrating backfill/live publishing through the `UplinkPublisher`
port with an injected double — MQTT is external I/O (ADR-0002)."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from techcamp.simulator.runner import publish_backfill, publish_live
from techcamp.simulator.trajectory import raw_value_at

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

    published = await publish_backfill(
        publisher, _NODE_ID, sensors=_SENSORS, days=1, interval_s=900, seed=1, now=now
    )

    assert len(publisher.uplinks) == 96  # 86400 / 900
    assert [u["seq"] for u in publisher.uplinks] == list(range(1, 97))
    assert published == 96
    assert all(u["m"].keys() == {"sm_10"} for u in publisher.uplinks)
    assert publisher.uplinks[0]["ts"] < publisher.uplinks[-1]["ts"]


async def test_publish_backfill_is_a_no_op_for_zero_days() -> None:
    publisher = _RecordingPublisher()
    now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)

    published = await publish_backfill(
        publisher, _NODE_ID, sensors=_SENSORS, days=0, interval_s=900, seed=1, now=now
    )

    assert publisher.uplinks == []
    assert published == 0


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
        after=96,  # 96 points already published
        interval_s=5,
        iterations=3,
        sleep=_fake_sleep,
    )

    assert [u["seq"] for u in publisher.uplinks] == [97, 98, 99]
    assert sleeps == [5, 5, 5]


async def test_publish_live_continues_the_trajectory_after_the_backfill() -> None:
    """#40: the live loop used to restart at trajectory index 0, so the first
    live uplink replayed the backfill's first point (same sine phase, same
    seeded noise) — a jump in the chart right where the backfill ends. One
    `after` count drives both the trajectory and `seq` (#62), and the backfill's
    own return value is that count."""

    async def _fake_sleep(seconds: float) -> None:
        pass

    publisher = _RecordingPublisher()
    backfilled = await publish_backfill(
        publisher, _NODE_ID, sensors=_SENSORS, days=1, interval_s=900, seed=1, now=datetime.now(UTC)
    )
    publisher.uplinks.clear()

    await publish_live(
        publisher,
        _NODE_ID,
        sensors=_SENSORS,
        seed=1,
        after=backfilled,
        interval_s=5,
        iterations=2,
        sleep=_fake_sleep,
    )

    assert [u["m"]["sm_10"] for u in publisher.uplinks] == [
        raw_value_at(96, seed=1),
        raw_value_at(97, seed=1),
    ]
    # the old trajectory's first point, the one the live loop used to repeat
    assert publisher.uplinks[0]["m"]["sm_10"] != raw_value_at(0, seed=1)
    assert [u["seq"] for u in publisher.uplinks] == [97, 98]


def test_publish_live_derives_seq_and_trajectory_from_one_continuation_count() -> None:
    """#62 R2-coupled-trajectory-counters: `start_seq` and `start_index` were two
    knobs the caller had to keep aligned by hand, and a wrong pair silently
    replayed or skipped trajectory points while `seq` marched on. `after` is the
    single continuation count both are derived from, so the misalignment is not
    expressible."""
    parameters = inspect.signature(publish_live).parameters

    assert "start_seq" not in parameters
    assert "start_index" not in parameters
    assert parameters["after"].default == 0
