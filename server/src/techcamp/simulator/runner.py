"""Backfill and live publish loops for the node simulator
(docs/06-diseno-detallado.md §10): one uplink per timestamp, one raw
trajectory point per sensor channel, through the `UplinkPublisher` port.

Two publish paths share the port. The E4 single-node path (`publish_backfill`/
`publish_live`) walks its own sine trajectory. The E16 scenario path
(`publish_scenario_backfill`/`publish_scenario_live`, docs/06:408-419) binds a
T1 scenario to as many nodes as it provisions: each node keeps its own `seq`
counter over the shared tick grid, an `offline` fault pauses a node's
publishing without touching its counter (D-T16.8: on resume `seq` jumps and
completeness observes the gap), and the noiseless series is exported as the
docs/11 §3 reference (D-T16.7), separate from the noisy values published."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from techcamp.simulator.publisher import UplinkPublisher
from techcamp.simulator.scenario_schema import Scenario
from techcamp.simulator.trajectory import (
    DEFAULT_FIRMWARE,
    SECONDS_PER_DAY,
    backfill_timestamps,
    band_value,
    build_uplink,
    invert_two_point,
    ramp_value,
    raw_value_at,
    scenario_noise,
    soil_two_point_params,
    trajectory_key_for,
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
    firmware: str = DEFAULT_FIRMWARE,
) -> int:
    """Publishes one uplink per backfill timestamp (docs/06 §10: "backfill...
    con ts en el pasado"). Returns how many trajectory points it published, so a
    following `publish_live` continues exactly where this one stopped.

    One uplink per point means trajectory point `i` is always `seq = i + 1`
    (docs/04-api.md:217: `seq` detects gaps), so the point count is the only
    state the two loops have to share."""
    timestamps = backfill_timestamps(days=days, interval_s=interval_s, now=now)
    for i, ts in enumerate(timestamps):
        channels = {
            sensor["channel_key"]: raw_value_at(i, seed=seed + j)
            for j, sensor in enumerate(sensors)
        }
        payload = build_uplink(seq=i + 1, ts=ts, channels=channels, firmware=firmware)
        await publisher.publish_uplink(node_id, payload)
    return len(timestamps)


async def publish_live(
    publisher: UplinkPublisher,
    node_id: UUID,
    *,
    sensors: list[dict[str, Any]],
    seed: int,
    after: int = 0,
    interval_s: int = LIVE_INTERVAL_S,
    iterations: int | None = None,
    firmware: str = DEFAULT_FIRMWARE,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    """Publishes the trajectory's next point every `interval_s` (default 5s,
    docs/06 §10), with `ts = now`. `after` is the number of trajectory points a
    preceding `publish_backfill` published: the live loop continues both the
    trajectory (docs/06 §10: "la lectura siguiente de la trayectoria") and the
    `seq` counter from that one number, so a caller cannot align them wrongly.
    `iterations=None` (the CLI default) runs until cancelled (Ctrl+C); a finite
    count makes this testable without a real wait."""
    iteration = 0
    while iterations is None or iteration < iterations:
        channels = {
            sensor["channel_key"]: raw_value_at(after + iteration, seed=seed + j)
            for j, sensor in enumerate(sensors)
        }
        payload = build_uplink(
            seq=after + 1 + iteration,
            ts=int(now().timestamp()),
            channels=channels,
            firmware=firmware,
        )
        await publisher.publish_uplink(node_id, payload)
        iteration += 1
        await sleep(interval_s)


# --- E16 T2: the scenario publish path (docs/06-diseno-detallado.md:408-419, 482-488) ---


@dataclass(frozen=True, slots=True)
class NodePlan:
    """One provisioned node bound to its own publisher: the broker's ACL
    authenticates each node as its own user allowed only `tc/v1/%u/#`
    (docs/04:294), so a multi-node run is one connection per node, never one
    connection publishing for all of them (H2: same contract as a real node)."""

    node_id: UUID
    ordinal: int
    """The node's 1-based position in the scenario's `nodes.count`: it names
    the fault that targets it (`faults[].node`) and picks its per-node
    calibration (`soil_two_point_params`)."""

    sensors: list[dict[str, Any]]
    """The claimed node's `SensorView` JSON objects, exactly as
    `node_client.claim_node` returns them."""

    publisher: UplinkPublisher


def _fault_windows(scenario: Scenario) -> dict[int, tuple[tuple[int, int], ...]]:
    """`faults[].node` → the `[at_day, at_day + hours)` elapsed-second windows
    its publish pauses cover. Nothing else exists to compute: D-T16.8 fixes an
    `offline` fault as a pause, so a fault is only ever *when* and *how long*
    (scenario_schema's own contract)."""
    windows: dict[int, list[tuple[int, int]]] = {}
    for fault in scenario.faults:
        start = fault.at_day * SECONDS_PER_DAY
        windows.setdefault(fault.node, []).append((start, start + fault.hours * 3600))
    return {ordinal: tuple(pair) for ordinal, pair in windows.items()}


def _is_paused(
    windows: dict[int, tuple[tuple[int, int], ...]], ordinal: int, tick: int, *, interval_s: int
) -> bool:
    """Whether tick `tick` of node `ordinal` falls inside one of its pauses.
    Membership is by elapsed time, so the same windows govern the live loop's
    ticks without a second definition of the pause."""
    return any(start <= tick * interval_s < end for start, end in windows.get(ordinal, ()))


def _uplink_channels(
    scenario: Scenario,
    plan: NodePlan,
    *,
    tick: int,
    fraction: float,
    ts: int,
    seed: int,
) -> dict[str, float]:
    """The `m` map of one tick: soil moisture inverted into the node's own raw
    ADC (docs/06:484), air channels as the physical values a real node sends
    (docs/04:304), both carrying the scenario's seeded noise (docs/06:488)."""
    channels: dict[str, float] = {}
    for sensor in plan.sensors:
        key = trajectory_key_for(sensor)
        block = scenario.trajectory(key)
        noise = scenario_noise(
            seed, f"{scenario.name}/{plan.ordinal}/{key}", tick, block.get("noise", 0.0)
        )
        if key.startswith("soil_moisture"):
            physical = ramp_value(block, fraction) + noise
            channels[sensor["channel_key"]] = invert_two_point(
                soil_two_point_params(plan.ordinal), physical
            )
        else:
            channels[sensor["channel_key"]] = band_value(block, ts) + noise
    return channels


def _truth_document(
    scenario: Scenario,
    plans: list[NodePlan],
    *,
    timestamps: list[int],
    seed: int,
    days: float,
) -> dict[str, Any]:
    """The noiseless trajectory (docs/06:487): every channel of every node at
    every tick, pauses included — the physical soil exists whether or not the
    node published, which is what makes this a reference for the balance's
    error even through a fault (docs/11 §3)."""
    ticks = len(timestamps)
    fractions = [tick / max(ticks - 1, 1) for tick in range(ticks)]
    nodes: list[dict[str, Any]] = []
    for plan in plans:
        channels: dict[str, list[dict[str, Any]]] = {}
        for sensor in plan.sensors:
            key = trajectory_key_for(sensor)
            block = scenario.trajectory(key)
            if key.startswith("soil_moisture"):
                channels[key] = [
                    {"tick": tick, "ts": ts, "value": ramp_value(block, fractions[tick])}
                    for tick, ts in enumerate(timestamps)
                ]
            else:
                channels[key] = [
                    {"tick": tick, "ts": ts, "value": band_value(block, ts)}
                    for tick, ts in enumerate(timestamps)
                ]
        nodes.append({"node": plan.ordinal, "channels": channels})
    return {
        "version": 1,
        "scenario": scenario.name,
        "seed": seed,
        "interval_s": scenario.interval_s,
        "backfill_days": days,
        "start_ts": timestamps[0] if timestamps else None,
        "nodes": nodes,
    }


def _write_truth(path: Path, document: dict[str, Any]) -> None:
    """Byte-deterministic (D-T16.7): sorted keys, compact separators, no
    generation timestamp, so the same scenario + seed + clock replays the same
    file and the same `now` diff-clean."""
    path.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


async def _publish_tick(
    plans: list[NodePlan],
    *,
    scenario: Scenario,
    windows: dict[int, tuple[tuple[int, int], ...]],
    tick: int,
    fraction: float,
    ts: int,
    seed: int,
) -> None:
    """One tick across every node: paused nodes publish nothing (D-T16.8),
    online nodes publish `seq = tick + 1` — the counter follows the tick grid,
    not the uplink count, so a pause leaves a `seq` jump on resume."""
    for plan in plans:
        if _is_paused(windows, plan.ordinal, tick, interval_s=scenario.interval_s):
            continue
        payload = build_uplink(
            seq=tick + 1,
            ts=ts,
            channels=_uplink_channels(
                scenario, plan, tick=tick, fraction=fraction, ts=ts, seed=seed
            ),
            firmware=DEFAULT_FIRMWARE,
        )
        await plan.publisher.publish_uplink(plan.node_id, payload)


async def publish_scenario_backfill(
    plans: list[NodePlan],
    *,
    scenario: Scenario,
    days: float,
    seed: int,
    now: datetime,
    truth_path: Path | None = None,
) -> int:
    """Publishes one uplink per node per backfill tick, each node over its own
    publisher (docs/06:411: "backfill: N días de lecturas con ts en el
    pasado"). Returns the tick count, the single number a following
    `publish_scenario_live` continues both the trajectory and every `seq` from.

    `seq` counts ticks, not published uplinks (D-T16.8): a node paused by an
    `offline` fault skips its ticks' uplinks while its counter marches on, so
    on resume `seq` jumps over exactly the pause and the readings before it are
    never rewritten. `truth_path`, when given, receives the noiseless series
    (docs/06:487) written separately from these noisy values."""
    timestamps = backfill_timestamps(days=days, interval_s=scenario.interval_s, now=now)
    ticks = len(timestamps)
    windows = _fault_windows(scenario)
    denominator = max(ticks - 1, 1)
    for tick, ts in enumerate(timestamps):
        await _publish_tick(
            plans,
            scenario=scenario,
            windows=windows,
            tick=tick,
            fraction=tick / denominator,
            ts=ts,
            seed=seed,
        )
    if truth_path is not None and plans:
        _write_truth(
            truth_path,
            _truth_document(scenario, plans, timestamps=timestamps, seed=seed, days=days),
        )
    return ticks


async def publish_scenario_live(
    plans: list[NodePlan],
    *,
    scenario: Scenario,
    seed: int,
    after: int,
    interval_s: int = LIVE_INTERVAL_S,
    iterations: int | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    """Publishes each node's next trajectory tick every `interval_s` (default
    5 s, docs/06:415-417), with `ts = now`. `after` is the tick count the
    backfill consumed: every node's trajectory position and `seq` continue from
    it, the ramp extrapolating past the backfill's end instead of restarting —
    the same single-continuation-number rule as the single-node `publish_live`.
    `iterations=None` (the CLI default) runs until cancelled (Ctrl+C)."""
    windows = _fault_windows(scenario)
    denominator = max(after - 1, 1)
    iteration = 0
    while iterations is None or iteration < iterations:
        tick = after + iteration
        await _publish_tick(
            plans,
            scenario=scenario,
            windows=windows,
            tick=tick,
            fraction=tick / denominator,
            ts=int(now().timestamp()),
            seed=seed,
        )
        iteration += 1
        await sleep(interval_s)
