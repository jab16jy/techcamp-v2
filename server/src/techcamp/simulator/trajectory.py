"""Deterministic raw-ADC trajectories and uplink payload construction for the
node simulator (docs/06-diseno-detallado.md §10; docs/04-api.md#contrato-mqtt).

T8 scope: the single-node publish path only. Scenario YAML, weather fixtures,
faults and `expected` checks are E16 (docs/06 §10)."""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta
from typing import Any

from techcamp.telemetry.domain.models import SUPPORTED_UPLINK_VERSION

DEFAULT_FIRMWARE = "sim-1.0.0"


def raw_value_at(
    index: int,
    *,
    seed: int,
    base: float = 2100.0,
    amplitude: float = 400.0,
    period_readings: int = 96,
    noise: float = 20.0,
) -> float:
    """One raw ADC point: a slow sine trajectory plus small seeded noise
    (docs/06 §10: "el simulador envía lecturas en ADC crudo"). Deterministic
    per `(seed, index)`, so the live loop can ask for one point at a time
    without recomputing the whole series."""
    rng = random.Random(seed * 1_000_003 + index)
    return (
        base
        + amplitude * math.sin(2 * math.pi * index / period_readings)
        + rng.uniform(-noise, noise)
    )


def raw_trajectory(
    *,
    seed: int,
    count: int,
    base: float = 2100.0,
    amplitude: float = 400.0,
    period_readings: int = 96,
    noise: float = 20.0,
) -> list[float]:
    """A batch of `count` consecutive `raw_value_at` points, for the backfill
    path."""
    return [
        raw_value_at(
            i,
            seed=seed,
            base=base,
            amplitude=amplitude,
            period_readings=period_readings,
            noise=noise,
        )
        for i in range(count)
    ]


def backfill_timestamps(*, days: float, interval_s: int, now: datetime) -> list[int]:
    """Past `ts` values spaced `interval_s` apart, ending just before `now`
    (docs/06 §10: "backfill: N días de lecturas con ts en el pasado";
    docs/04-api.md:218 defines `ts` as an epoch-seconds integer)."""
    if days <= 0 or interval_s <= 0:
        return []
    start = now - timedelta(days=days)
    count = int(days * 86400 / interval_s)
    return [int((start + timedelta(seconds=interval_s * i)).timestamp()) for i in range(count)]


def build_uplink(
    *, seq: int, ts: int | None, channels: dict[str, float], firmware: str = DEFAULT_FIRMWARE
) -> dict[str, Any]:
    """One `tc/v1/{node_id}/up` payload (docs/04-api.md:202-220), the same
    shape `techcamp.telemetry.domain.models.parse_uplink` validates — reused
    here rather than redefined."""
    return {
        "v": SUPPORTED_UPLINK_VERSION,
        "seq": seq,
        "ts": ts,
        "fw": firmware,
        "m": dict(channels),
    }
