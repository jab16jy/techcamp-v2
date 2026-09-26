"""E4 T8: deterministic raw-ADC trajectories and uplink payload construction
(docs/06-diseno-detallado.md §10, docs/04-api.md#contrato-mqtt)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from techcamp.simulator.trajectory import (
    backfill_timestamps,
    build_uplink,
    raw_trajectory,
    raw_value_at,
)
from techcamp.telemetry.domain.models import SUPPORTED_UPLINK_VERSION, parse_uplink


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
