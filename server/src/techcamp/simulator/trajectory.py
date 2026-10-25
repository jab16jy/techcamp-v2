"""Deterministic raw-ADC trajectories and uplink payload construction for the
node simulator (docs/06-diseno-detallado.md §10; docs/04-api.md#contrato-mqtt).

Two halves share this module. The E4 single-node publish path is a slow sine in
raw ADC. The E16 scenario half (docs/06:421-440) binds a scenario YAML's
physical values — a soil-moisture ramp and daily air bands — to the same uplink:
soil moisture is inverted through the node's own two-point calibration into the
raw ADC a real node would send (docs/04:313: "La humedad de suelo llega como ADC
crudo; la calibración se aplica en el servidor"), air channels travel as the
physical values docs/04:304's own uplink example shows."""

from __future__ import annotations

import math
import random
import zlib
from datetime import datetime, timedelta
from typing import Any

from techcamp.telemetry.domain.models import SUPPORTED_UPLINK_VERSION

DEFAULT_FIRMWARE = "sim-1.0.0"

SECONDS_PER_DAY = 86400


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


# --- E16 T2: the scenario half (docs/06-diseno-detallado.md:421-440, 484, 487-488) ---


def soil_two_point_params(node_ordinal: int) -> dict[str, float]:
    """The two-point calibration node `node_ordinal` carries (docs/06:484:
    "cada nodo simulado tiene su calibración, así que también se demuestra la
    calibración").

    Ordinal 1 is docs/03-modelo-datos.md:463-467's own example
    (`raw_dry 2900 / raw_wet 1300 / vwc 5–45`); every further node shifts its
    raw endpoints so no two nodes share a calibration while they all read the
    same physical span. This is the one source both the claim path (posting the
    calibration) and the runner (inverting it into raw ADC) consult, so a node
    can never be calibrated with coefficients its publisher did not invert."""
    if node_ordinal < 1:
        raise ValueError(f"node ordinal starts at 1, got {node_ordinal}")
    return {
        "raw_dry": 2900.0 + 60.0 * (node_ordinal - 1),
        "raw_wet": 1300.0 - 40.0 * (node_ordinal - 1),
        "vwc_dry": 5.0,
        "vwc_wet": 45.0,
    }


def invert_two_point(params: dict[str, float], value: float) -> float:
    """The raw ADC a node with `params` reads at physical `value` — the inverse
    of `telemetry.domain.models.apply_calibration`'s `two_point` branch, so the
    server's own calibration recovers the physical value the runner intended
    (H2: nothing distinguishes a simulated uplink, docs/06:396)."""
    raw_dry, raw_wet = params["raw_dry"], params["raw_wet"]
    vwc_dry, vwc_wet = params["vwc_dry"], params["vwc_wet"]
    fraction = (value - vwc_dry) / (vwc_wet - vwc_dry)
    return raw_dry + fraction * (raw_wet - raw_dry)


def scenario_noise(seed: int, stream: str, index: int, amplitude: float) -> float:
    """One seeded noise draw for `stream` at trajectory tick `index`
    (docs/06:488: "el ruido ... es configurable"). The stream names the
    scenario/node/channel, so every channel draws its own series from the one
    seed; `zlib.crc32` derives the stream's sub-seed because Python's `hash()`
    is salted per process and would break replay. An amplitude of zero is
    exactly zero noise — a missing `noise` in the YAML means noiseless
    (scenario_schema's contract), not "some default noise"."""
    if amplitude <= 0.0:
        return 0.0
    rng = random.Random(seed * 1_000_003 + zlib.crc32(stream.encode("utf-8")) + index)
    return rng.uniform(-amplitude, amplitude)


def ramp_value(block: dict[str, float], fraction: float) -> float:
    """The noiseless soil-moisture ramp at `fraction` of the backfill
    (docs/06:432,446): linear from `start` to `end`, which is what makes the
    θ_estrés crossing day computable — 14 × (22 − 15,3) / (22 − 13) ≈ 10,4."""
    return block["start"] + (block["end"] - block["start"]) * fraction


def band_value(block: dict[str, float], ts: int) -> float:
    """The noiseless daily band an air channel reads at epoch second `ts`
    (docs/06:433-434): one cosine oscillation per day between `daily_min` and
    `daily_max`, derived from the timestamp alone so the same seed replays the
    same band and the live loop's `ts = now` keeps oscillating with the day."""
    day_fraction = (ts % SECONDS_PER_DAY) / SECONDS_PER_DAY
    lift = (1.0 - math.cos(2.0 * math.pi * day_fraction)) / 2.0
    return block["daily_min"] + (block["daily_max"] - block["daily_min"]) * lift


def trajectory_key_for(sensor: dict[str, Any]) -> str:
    """The scenario trajectory block a claimed node's sensor view reads: soil
    moisture names its depth (`soil_moisture_30cm`, docs/06:432) and the air
    channels their bands (docs/06:433-434)."""
    metric = str(sensor["metric"])
    if metric == "soil_moisture":
        return f"soil_moisture_{sensor['depth_cm']}cm"
    if metric in ("air_temp", "air_rh"):
        return metric
    raise ValueError(f"no scenario trajectory for sensor metric {metric!r}")
