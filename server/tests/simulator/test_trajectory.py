"""E4 T8 + E16 T2: deterministic raw-ADC trajectories and uplink payload
construction (docs/06-diseno-detallado.md §10, docs/04-api.md#contrato-mqtt).

The scenario half (E16 T2, docs/06:421-440, 484, 487-488) covers the pure
arithmetic the runner binds to a scenario YAML: the soil ramp and air bands,
the seeded noise, and the per-node two-point calibration inverted into the raw
ADC a real node would publish (docs/04:313: "La humedad de suelo llega como ADC
crudo; la calibración se aplica en el servidor")."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from techcamp.simulator.trajectory import (
    backfill_timestamps,
    band_value,
    build_uplink,
    invert_two_point,
    ramp_value,
    raw_trajectory,
    raw_value_at,
    scenario_noise,
    soil_two_point_params,
    trajectory_key_for,
)
from techcamp.telemetry.domain.models import (
    SUPPORTED_UPLINK_VERSION,
    Calibration,
    CalibrationKind,
    CalibrationMethod,
    apply_calibration,
    parse_uplink,
)


def test_build_uplink_matches_the_documented_contract_and_the_domain_validator() -> None:
    payload = build_uplink(seq=18234, ts=1760000400, channels={"sm_10": 2310.0}, firmware="1.0.3")

    assert payload == {
        "v": SUPPORTED_UPLINK_VERSION,
        "seq": 18234,
        "ts": 1760000400,
        "fw": "1.0.3",
        "m": {"sm_10": 2310.0},
    }
    uplink = parse_uplink(payload)
    assert uplink.seq == 18234
    assert uplink.channels == {"sm_10": 2310.0}


def test_build_uplink_allows_a_missing_ts_like_a_node_without_ntp() -> None:
    payload = build_uplink(seq=1, ts=None, channels={"sm_10": 2000.0})

    uplink = parse_uplink(payload)

    assert uplink.ts is None


def test_backfill_timestamps_span_the_requested_days_at_the_given_interval() -> None:
    now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)

    timestamps = backfill_timestamps(days=1, interval_s=900, now=now)

    assert len(timestamps) == 96  # 86400 / 900
    assert timestamps == sorted(timestamps)
    assert timestamps[0] == int((now - timedelta(days=1)).timestamp())
    assert timestamps[1] - timestamps[0] == 900


def test_backfill_timestamps_is_empty_for_zero_days() -> None:
    assert backfill_timestamps(days=0, interval_s=900, now=datetime(2026, 9, 25, tzinfo=UTC)) == []


def test_raw_trajectory_is_deterministic_for_the_same_seed() -> None:
    a = raw_trajectory(seed=42, count=10)
    b = raw_trajectory(seed=42, count=10)

    assert a == b


def test_raw_trajectory_differs_for_a_different_seed() -> None:
    a = raw_trajectory(seed=1, count=10)
    b = raw_trajectory(seed=2, count=10)

    assert a != b


def test_raw_value_at_matches_the_batch_trajectory_at_the_same_index() -> None:
    trajectory = raw_trajectory(seed=7, count=5)

    assert [raw_value_at(i, seed=7) for i in range(5)] == trajectory


def test_ramp_value_interpolates_linearly_from_start_to_end() -> None:
    """docs/06:432,446: soil moisture falls linearly across the backfill, which
    is what makes the θ_estrés crossing day computable (14 × (22 − 15,3) / (22 − 13)
    ≈ 10,4)."""

    block = {"start": 22.0, "end": 13.0, "noise": 0.8}

    assert ramp_value(block, 0.0) == 22.0
    assert ramp_value(block, 1.0) == 13.0
    assert ramp_value(block, 0.5) == 17.5


def test_band_value_oscillates_daily_between_min_and_max() -> None:
    """docs/06:433-434: an air channel is a daily band, deterministic from the
    timestamp alone so the same seed replays the same band."""

    block = {"daily_min": 26.0, "daily_max": 37.0, "noise": 0.5}

    midnight = 1760000000 - (1760000000 % 86400)  # a UTC day boundary
    values = [band_value(block, midnight + 900 * i) for i in range(96)]  # one day

    assert all(26.0 <= v <= 37.0 for v in values)
    assert values[0] == pytest.approx(26.0)  # the band opens the day at its minimum
    assert max(values) == pytest.approx(37.0, abs=0.2)  # and crosses its maximum once
    # one period later the same phase reads the same value
    assert band_value(block, midnight + 43_200) == pytest.approx(
        band_value(block, midnight + 43_200 + 86400)
    )


def test_scenario_noise_is_seeded_per_stream_and_index() -> None:
    """docs/06:488: noise is configuration, so it must be reproducible from
    (seed, stream, index) — the runner names streams per scenario/node/channel —
    and exactly zero when the scenario declares none (scenario_schema: a
    missing `noise` means noiseless)."""

    assert scenario_noise(7, "el-nino/1/soil_moisture_30cm", 5, 0.8) == scenario_noise(
        7, "el-nino/1/soil_moisture_30cm", 5, 0.8
    )
    assert abs(scenario_noise(7, "el-nino/1/soil_moisture_30cm", 5, 0.8)) <= 0.8
    # another node, another channel, another tick: another draw
    assert scenario_noise(7, "el-nino/1/soil_moisture_30cm", 5, 0.8) != scenario_noise(
        7, "el-nino/2/soil_moisture_30cm", 5, 0.8
    )
    assert scenario_noise(7, "el-nino/1/soil_moisture_30cm", 5, 0.8) != scenario_noise(
        7, "el-nino/1/soil_moisture_30cm", 6, 0.8
    )
    # a different seed changes the noise, not its bound
    assert scenario_noise(7, "s", 5, 0.8) != scenario_noise(8, "s", 5, 0.8)
    assert scenario_noise(7, "s", 5, 0.0) == 0.0


def test_soil_two_point_params_are_per_node_and_node_one_is_the_docs_example() -> None:
    """docs/06:484: "cada nodo simulado tiene su calibración". Node 1 carries
    docs/03-modelo-datos.md:463-467's own two-point example; every further node
    shifts its raw endpoints so no two nodes share a calibration."""

    assert soil_two_point_params(1) == {
        "raw_dry": 2900.0,
        "raw_wet": 1300.0,
        "vwc_dry": 5.0,
        "vwc_wet": 45.0,
    }
    second = soil_two_point_params(2)
    assert second["raw_dry"] != soil_two_point_params(1)["raw_dry"]
    assert second["raw_wet"] != soil_two_point_params(1)["raw_wet"]
    # same physical span, so both nodes calibrate the same soil
    assert second["vwc_dry"] == 5.0 and second["vwc_wet"] == 45.0


def test_invert_two_point_round_trips_through_the_servers_calibration() -> None:
    """H2/H3 (docs/06:396,484): the uplink is raw ADC and the SERVER applies the
    calibration, so the inverse is proven against the server's own
    `apply_calibration`, not against a restatement of the formula."""

    for ordinal in (1, 2, 3):
        params = soil_two_point_params(ordinal)
        for value in (5.0, 13.0, 15.3, 22.0, 45.0):
            raw = invert_two_point(params, value)
            calibration = Calibration(
                id=UUID(int=0),
                sensor_id=0,
                version=1,
                method=CalibrationMethod.TWO_POINT,
                kind=CalibrationKind.FIELD,
                params=dict(params),
                rmse_pct=None,
                valid_from=datetime(2026, 1, 1, tzinfo=UTC),
            )
            assert apply_calibration(calibration, raw) == pytest.approx(value)


def test_trajectory_key_for_maps_a_sensor_view_to_its_scenario_block() -> None:
    """The claimed node's sensor views (metric + depth) name the trajectory the
    runner must follow: `soil_moisture` at 30 cm reads the `soil_moisture_30cm`
    ramp, the air channels their bands (docs/06:432-434)."""

    assert (
        trajectory_key_for(
            {"channel_key": "sm_30", "metric": "soil_moisture", "depth_cm": 30, "unit": "%"}
        )
        == "soil_moisture_30cm"
    )
    assert trajectory_key_for({"channel_key": "at", "metric": "air_temp", "depth_cm": None}) == (
        "air_temp"
    )
    assert trajectory_key_for({"channel_key": "rh", "metric": "air_rh", "depth_cm": None}) == (
        "air_rh"
    )
