"""E16 T2: the scenario runner (docs/06-diseno-detallado.md:408-419, 482-488).

`publish_scenario_backfill`/`publish_scenario_live` bind a T1 scenario YAML to
MQTT uplinks through the `UplinkPublisher` port (MQTT stays the only external
I/O doubled, ADR-0002), one publisher per node because a real broker's ACL
authenticates each node as its own user (docs/04:294: `tc/v1/%u/#`).

Against the real library YAMLs, so the asserted arithmetic is the seminar's own:
scenario A's ramp crossing θ_estrés = 15,3 % on day ≈ 10,4 (docs/06:442-448),
and scenario C's `offline` fault as a publish pause whose `seq` jump and time
gap completeness observes (D-T16.8, docs/06:488)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from techcamp.simulator.runner import NodePlan, publish_scenario_backfill, publish_scenario_live
from techcamp.simulator.scenario_schema import Scenario, ScenarioFault, load_scenario
from techcamp.simulator.trajectory import (
    invert_two_point,
    ramp_value,
    scenario_noise,
    soil_two_point_params,
)
from techcamp.telemetry.domain.models import (
    Calibration,
    CalibrationKind,
    CalibrationMethod,
    apply_calibration,
    parse_uplink,
)

pytestmark = pytest.mark.anyio

_NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
_NODE_1 = UUID("00000000-0000-0000-0000-0000000000a1")
_NODE_2 = UUID("00000000-0000-0000-0000-0000000000a2")

STRESS_THRESHOLD_PCT = 15.3
"""docs/06-diseno-detallado.md:444: θ_estrés of scenario A's soil and crop."""


@dataclass
class RecordingPublisher:
    uplinks: list[dict[str, Any]] = field(default_factory=list)

    async def publish_uplink(self, node_id: UUID, payload: dict[str, Any]) -> None:
        self.uplinks.append(payload)

    async def publish_status(self, node_id: UUID, status: str) -> None:
        pass


def _sensors(node_id: UUID) -> list[dict[str, Any]]:
    """The sensor views a claimed scenario node carries: soil at the scenario's
    depth plus the two air channels (docs/04:304, docs/06:432-434)."""
    return [
        {
            "id": 1,
            "node_id": str(node_id),
            "channel_key": "sm_30",
            "metric": "soil_moisture",
            "depth_cm": 30,
            "unit": "%",
        },
        {
            "id": 2,
            "node_id": str(node_id),
            "channel_key": "at",
            "metric": "air_temp",
            "depth_cm": None,
            "unit": "°C",
        },
        {
            "id": 3,
            "node_id": str(node_id),
            "channel_key": "rh",
            "metric": "air_rh",
            "depth_cm": None,
            "unit": "%",
        },
    ]


def _plans(scenario: Scenario) -> list[NodePlan]:
    assert scenario.nodes.count == 2, "the node-pair scenarios of the library"
    return [
        NodePlan(
            node_id=node_id,
            ordinal=ordinal,
            sensors=_sensors(node_id),
            publisher=RecordingPublisher(),
        )
        for ordinal, node_id in ((1, _NODE_1), (2, _NODE_2))
    ]


def _uplinks(plans: list[NodePlan]) -> dict[UUID, list[dict[str, Any]]]:
    return {plan.node_id: plan.publisher.uplinks for plan in plans}


def _server_calibration(ordinal: int) -> Calibration:
    """The calibration the runner expects the claim path to have posted for
    node `ordinal` (docs/06:484: per-node, field kind, two-point)."""
    return Calibration(
        id=UUID(int=0),
        sensor_id=0,
        version=1,
        method=CalibrationMethod.TWO_POINT,
        kind=CalibrationKind.FIELD,
        params=dict(soil_two_point_params(ordinal)),
        rmse_pct=None,
        valid_from=_NOW,
    )


async def test_backfill_publishes_one_uplink_per_node_per_tick_with_their_own_seq() -> None:
    scenario = load_scenario("el-nino")
    plans = _plans(scenario)

    ticks = await publish_scenario_backfill(plans, scenario=scenario, days=1.0, seed=0, now=_NOW)

    assert ticks == 96  # 86400 / 900
    for node_uplinks in _uplinks(plans).values():
        assert len(node_uplinks) == 96
        assert [u["seq"] for u in node_uplinks] == list(range(1, 97))
        timestamps = [u["ts"] for u in node_uplinks]
        assert timestamps == sorted(timestamps)
        assert all(
            int((_NOW - timedelta(days=1)).timestamp()) <= ts <= int(_NOW.timestamp())
            for ts in timestamps
        )
        # every payload validates as the uplink of a real node (H2, docs/06:396)
        for payload in node_uplinks:
            parse_uplink(payload)
        assert all(u["m"].keys() == {"sm_30", "at", "rh"} for u in node_uplinks)


async def test_backfill_soil_moisture_is_raw_adc_the_server_calibrates_back() -> None:
    """H3 (docs/06:484): the uplink carries raw ADC for the node's own
    calibration, and the server's `apply_calibration` — the only calibration
    that matters (H2, docs/06:396) — recovers the noisy physical value."""
    scenario = load_scenario("el-nino")
    plans = _plans(scenario)
    block = scenario.trajectory("soil_moisture_30cm")

    ticks = await publish_scenario_backfill(plans, scenario=scenario, days=1.0, seed=0, now=_NOW)

    for plan in plans:
        params = soil_two_point_params(plan.ordinal)
        calibration = _server_calibration(plan.ordinal)
        for tick, uplink in enumerate(plan.publisher.uplinks):
            raw = uplink["m"]["sm_30"]
            assert (
                min(params["raw_wet"], params["raw_dry"])
                <= raw
                <= max(params["raw_wet"], params["raw_dry"])
            )
            noisy = ramp_value(block, tick / (ticks - 1)) + scenario_noise(
                0, f"el-nino/{plan.ordinal}/soil_moisture_30cm", tick, block["noise"]
            )
            assert apply_calibration(calibration, raw) == pytest.approx(noisy)


async def test_backfill_nodes_publish_distinct_raw_for_the_same_soil() -> None:
    """docs/06:484: each simulated node has its own calibration, so the same
    physical soil reads different raw ADC on different nodes."""
    scenario = load_scenario("el-nino")
    plans = _plans(scenario)

    await publish_scenario_backfill(plans, scenario=scenario, days=1.0, seed=0, now=_NOW)

    uplinks = _uplinks(plans)
    assert [u["m"]["sm_30"] for u in uplinks[_NODE_1]] != [
        u["m"]["sm_30"] for u in uplinks[_NODE_2]
    ]


async def test_backfill_air_channels_stay_inside_their_band_plus_noise() -> None:
    scenario = load_scenario("el-nino")
    plans = _plans(scenario)

    await publish_scenario_backfill(plans, scenario=scenario, days=2.0, seed=0, now=_NOW)

    temp = scenario.trajectory("air_temp")
    rh = scenario.trajectory("air_rh")
    for node_uplinks in _uplinks(plans).values():
        assert all(
            temp["daily_min"] - temp["noise"] <= u["m"]["at"] <= temp["daily_max"] + temp["noise"]
            for u in node_uplinks
        )
        assert all(
            rh["daily_min"] - rh["noise"] <= u["m"]["rh"] <= rh["daily_max"] + rh["noise"]
            for u in node_uplinks
        )


async def test_truth_export_crosses_stress_threshold_on_day_10_4(tmp_path: Path) -> None:
    """docs/06:442-448: the noiseless ramp falls 22 % → 13 % over the 14-day
    backfill and crosses θ_estrés = 15,3 % on day ≈ 10,4. The truth export is
    that noiseless trajectory (docs/06:487), so the crossing is read off it."""
    scenario = load_scenario("el-nino")
    plans = _plans(scenario)
    truth_path = tmp_path / "truth.json"

    await publish_scenario_backfill(
        plans, scenario=scenario, days=14.0, seed=0, now=_NOW, truth_path=truth_path
    )

    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    assert truth["scenario"] == "el-nino"
    soil = truth["nodes"][0]["channels"]["soil_moisture_30cm"]
    crossing = next(row for row in soil if row["value"] < STRESS_THRESHOLD_PCT)
    crossing_day = crossing["tick"] * scenario.interval_s / 86400
    assert 10.3 < crossing_day < 10.5
    assert soil[0]["value"] == pytest.approx(22.0)
    assert soil[-1]["value"] == pytest.approx(13.0)


async def test_same_seed_replays_identical_uplinks_and_truth_bytes(tmp_path: Path) -> None:
    """D-T16.7 (docs/06:487-488): same scenario + seed → byte-identical
    trajectories, published and exported alike."""
    scenario = load_scenario("el-nino")

    first_plans, second_plans = _plans(scenario), _plans(scenario)
    first_truth, second_truth = tmp_path / "a.json", tmp_path / "b.json"
    await publish_scenario_backfill(
        first_plans, scenario=scenario, days=1.0, seed=0, now=_NOW, truth_path=first_truth
    )
    await publish_scenario_backfill(
        second_plans, scenario=scenario, days=1.0, seed=0, now=_NOW, truth_path=second_truth
    )

    assert [json.dumps(u, sort_keys=True) for u in first_plans[0].publisher.uplinks] == [
        json.dumps(u, sort_keys=True) for u in second_plans[0].publisher.uplinks
    ]
    assert first_truth.read_bytes() == second_truth.read_bytes()


async def test_a_different_seed_changes_the_noise_but_not_the_truth(tmp_path: Path) -> None:
    """The seed drives the noise only: the exported truth is the noiseless
    reference (docs/06:487), so its channels do not move with the seed."""
    scenario = load_scenario("el-nino")

    seeded_plans, other_plans = _plans(scenario), _plans(scenario)
    seeded_truth, other_truth = tmp_path / "a.json", tmp_path / "b.json"
    await publish_scenario_backfill(
        seeded_plans, scenario=scenario, days=1.0, seed=0, now=_NOW, truth_path=seeded_truth
    )
    await publish_scenario_backfill(
        other_plans, scenario=scenario, days=1.0, seed=99, now=_NOW, truth_path=other_truth
    )

    assert [u["m"]["sm_30"] for u in seeded_plans[0].publisher.uplinks] != [
        u["m"]["sm_30"] for u in other_plans[0].publisher.uplinks
    ]
    assert (
        json.loads(seeded_truth.read_text(encoding="utf-8"))["nodes"]
        == json.loads(other_truth.read_text(encoding="utf-8"))["nodes"]
    )


async def test_offline_fault_pauses_publishing_and_seq_jumps_on_resume() -> None:
    """D-T16.8 (docs/06:488): an `offline` fault publishes nothing for its
    `hours`; on resume `seq` jumps over the skipped ticks and the time gap —
    8 h of a 900 s interval, 32 missing readings — is what completeness
    observes. The past is never rewritten: the paused node's own `ts` series
    stays strictly increasing, with no late fill-in of the paused window.

    The fault is the runner mechanic's own 8 h blip, re-narrowed over the
    library's C: since the node-down scenario must still be silent when the
    window is verified (#340's T6 follow-up), its fault covers the rest of the
    window and never resumes inside a backfill — the resume lives here."""
    scenario = replace(
        load_scenario("node-failure"),
        faults=(ScenarioFault(at_day=4, node=2, type="offline", hours=8),),
    )
    plans = _plans(scenario)

    ticks = await publish_scenario_backfill(plans, scenario=scenario, days=6.0, seed=0, now=_NOW)

    assert ticks == 576  # 6 d at 900 s
    uplinks = _uplinks(plans)
    # node 1 never paused: contiguous seq, full series
    assert [u["seq"] for u in uplinks[_NODE_1]] == list(range(1, 577))

    # node 2 skipped ticks 384..415 (elapsed [4 d, 4 d + 8 h)): 32 uplinks fewer
    node_2_seq = [u["seq"] for u in uplinks[_NODE_2]]
    assert len(node_2_seq) == 576 - 32
    jumps = [
        (prev, nxt)
        for prev, nxt in zip(node_2_seq, node_2_seq[1:], strict=False)  # ragged by design
        if nxt - prev != 1
    ]
    assert jumps == [(384, 417)]  # seq resumes 33 later: the observable gap

    # nothing was published inside the pause window, and ts never rewinds
    backfill_start = int((_NOW - timedelta(days=6.0)).timestamp())
    pause_start = backfill_start + 4 * 86400
    pause_end = pause_start + 8 * 3600
    assert all(not pause_start <= u["ts"] < pause_end for u in uplinks[_NODE_2])
    timestamps = [u["ts"] for u in uplinks[_NODE_2]]
    assert timestamps == sorted(timestamps)


async def test_truth_keeps_ticking_through_the_pause(tmp_path: Path) -> None:
    """The truth is the physical soil state, which exists whether or not the
    node published: the export covers every tick, pause included (docs/06:487),
    which is exactly what makes it a reference the balance can be measured
    against even while the sensor was down."""
    scenario = load_scenario("node-failure")
    plans = _plans(scenario)
    truth_path = tmp_path / "truth.json"

    await publish_scenario_backfill(
        plans, scenario=scenario, days=6.0, seed=0, now=_NOW, truth_path=truth_path
    )

    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    soil_node_2 = truth["nodes"][1]["channels"]["soil_moisture_30cm"]
    assert len(soil_node_2) == 576  # the full series, the 32-tick gap included


async def test_live_continues_the_scenario_trajectory_and_seq_after_backfill() -> None:
    """docs/06:415-417: after the backfill the loop publishes "la lectura
    siguiente de la trayectoria" every 5 s, continuing both the trajectory tick
    and `seq`."""
    scenario = load_scenario("el-nino")
    plans = _plans(scenario)

    ticks = await publish_scenario_backfill(plans, scenario=scenario, days=1.0, seed=0, now=_NOW)
    for plan in plans:
        plan.publisher.uplinks.clear()

    async def _no_sleep(seconds: float) -> None:
        pass

    await publish_scenario_live(
        plans, scenario=scenario, seed=0, after=ticks, iterations=2, sleep=_no_sleep
    )

    uplinks = _uplinks(plans)
    for node_uplinks in uplinks.values():
        assert [u["seq"] for u in node_uplinks] == [ticks + 1, ticks + 2]
        assert all(u["m"].keys() == {"sm_30", "at", "rh"} for u in node_uplinks)

    # the ramp continues past the backfill's end instead of restarting at 22 %:
    # the exact noisy raw of tick 96 (seeded, so equality is exact)
    block = scenario.trajectory("soil_moisture_30cm")
    params = soil_two_point_params(1)
    first_live = uplinks[_NODE_1][0]["m"]["sm_30"]
    beyond_end = invert_two_point(
        params,
        ramp_value(block, ticks / (ticks - 1))
        + scenario_noise(0, "el-nino/1/soil_moisture_30cm", ticks, block["noise"]),
    )
    assert first_live == pytest.approx(beyond_end)
