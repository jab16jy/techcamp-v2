"""E4 T8: CLI argument parsing for `python -m techcamp.simulator`
(docs/06-diseno-detallado.md §10)."""

from __future__ import annotations

from uuid import UUID

import pytest

from techcamp.simulator.__main__ import parse_args

_PLOT_ID = "00000000-0000-0000-0000-000000000003"


def test_parse_args_requires_either_claim_code_or_provision() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--phone", "+573001112233", "--plot-id", _PLOT_ID])


def test_parse_args_accepts_a_claim_code_with_sane_defaults() -> None:
    args = parse_args(
        [
            "--phone",
            "+573001112233",
            "--plot-id",
            _PLOT_ID,
            "--claim-code",
            "CODE1",
        ]
    )

    assert args.claim_code == "CODE1"
    assert args.provision is False
    assert args.plot_id == UUID(_PLOT_ID)
    assert args.backfill_days == 1.0
    assert args.interval_s == 900
    assert args.live is False
    assert args.seed == 0


def test_parse_args_accepts_provision_without_a_claim_code() -> None:
    args = parse_args(["--phone", "+573001112233", "--plot-id", _PLOT_ID, "--provision"])

    assert args.claim_code is None
    assert args.provision is True


@pytest.mark.parametrize("value", ["0", "-1", "-0.5"])
def test_parse_args_rejects_a_non_positive_backfill_days(value: str) -> None:
    """#40: `--backfill-days 0` published nothing and reported "backfilled 0
    uplink(s)", which reads like a working run instead of a typo."""

    with pytest.raises(SystemExit):
        parse_args(
            [
                "--phone",
                "+573001112233",
                "--plot-id",
                _PLOT_ID,
                "--claim-code",
                "CODE1",
                "--backfill-days",
                value,
            ]
        )


@pytest.mark.parametrize("value", ["0", "-1"])
def test_parse_args_rejects_a_non_positive_interval_s(value: str) -> None:
    with pytest.raises(SystemExit):
        parse_args(
            [
                "--phone",
                "+573001112233",
                "--plot-id",
                _PLOT_ID,
                "--claim-code",
                "CODE1",
                "--interval-s",
                value,
            ]
        )
