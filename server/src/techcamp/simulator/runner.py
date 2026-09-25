"""Backfill and live publish loops for the node simulator
(docs/06-diseno-detallado.md §10): one uplink per timestamp, one raw
trajectory point per sensor channel, through the `UplinkPublisher` port."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from techcamp.simulator.publisher import UplinkPublisher
from techcamp.simulator.trajectory import (
    DEFAULT_FIRMWARE,
    backfill_timestamps,
    build_uplink,
    raw_value_at,
)

LIVE_INTERVAL_S = 5
"""docs/06 §10: "loop en vivo cada 5 s"."""


async def publish_backfill(
    publisher: UplinkPublisher,
    node_id: UUID,
    *,
    sensors: list[dict[str, Any]],
    days: float,
    interval_s: int,
    seed: int,
    now: datetime,
    start_seq: int = 1,
    firmware: str = DEFAULT_FIRMWARE,
) -> int:
    """Publishes one uplink per backfill timestamp (docs/06 §10: "backfill...
    con ts en el pasado"). Returns the next unused `seq`, so a following
    `publish_live` call continues the same monotonic counter
    (docs/04-api.md:217: `seq` detects gaps)."""
    timestamps = backfill_timestamps(days=days, interval_s=interval_s, now=now)
    for i, ts in enumerate(timestamps):
        channels = {
            sensor["channel_key"]: raw_value_at(i, seed=seed + j)
            for j, sensor in enumerate(sensors)
        }
        payload = build_uplink(seq=start_seq + i, ts=ts, channels=channels, firmware=firmware)
        await publisher.publish_uplink(node_id, payload)
    return start_seq + len(timestamps)


async def publish_live(
    publisher: UplinkPublisher,
    node_id: UUID,
    *,
    sensors: list[dict[str, Any]],
    seed: int,
    start_seq: int,
    interval_s: int = LIVE_INTERVAL_S,
    iterations: int | None = None,
    firmware: str = DEFAULT_FIRMWARE,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    """Publishes the trajectory's next point every `interval_s` (default 5s,
    docs/06 §10), with `ts = now`. `iterations=None` (the CLI default) runs
    until cancelled (Ctrl+C); a finite count makes this testable without a
    real wait."""
    index = 0
    while iterations is None or index < iterations:
        channels = {
            sensor["channel_key"]: raw_value_at(index, seed=seed + j)
            for j, sensor in enumerate(sensors)
        }
        payload = build_uplink(
            seq=start_seq + index, ts=int(now().timestamp()), channels=channels, firmware=firmware
        )
        await publisher.publish_uplink(node_id, payload)
        index += 1
        await sleep(interval_s)
