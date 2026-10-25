"""E16 T4: the `expected` checker's pure core (docs/06-diseno-detallado.md:485-486).

`expected` is an e2e oracle: the checker judges one scenario's block against the
state the stack actually serves and names every failed assertion. The comparison
is an exact match on purpose — docs/06:473-480 defines what each scenario
demonstrates, so an alert outside the block is a broken wiring, not a bonus.

These tests are pure: no database, no clock, no sockets (the last one asserted,
the same way the scenario library's own no-socket test is). The state-gathering
adapter over the real repositories is `test_expected_checker_state.py`.
"""

from __future__ import annotations

import socket

import pytest

from techcamp.simulator.application.expected_checker import (
    ObservedScenarioState,
    check_expected,
)
from techcamp.simulator.scenario_schema import load_scenario


def _observed(
    alerts: tuple[str, ...] = (), kind: str | None = "not_needed", advice: tuple[str, ...] = ()
) -> ObservedScenarioState:
    return ObservedScenarioState(alert_codes=alerts, irrigation_kind=kind, advice=advice)


def test_a_state_that_matches_the_block_passes() -> None:
    scenario = load_scenario("el-nino")
    verdict = check_expected(
        scenario,
        _observed(alerts=("water_stress", "heat_stress"), kind="irrigate"),
    )

    assert verdict.scenario == "el-nino"
    assert verdict.passed
    assert verdict.findings == ()


def test_an_empty_block_against_an_empty_state_passes() -> None:
    """`normal` and `offline-farmer` assert silence: nothing open, nothing advised."""
    scenario = load_scenario("normal")

    assert check_expected(scenario, _observed()).passed


def test_a_missing_alert_fails_and_names_the_code() -> None:
    scenario = load_scenario("el-nino")

    verdict = check_expected(
        scenario,
        _observed(alerts=("water_stress",), kind="irrigate"),
    )

    assert not verdict.passed
    assert [(f.kind, f.expected) for f in verdict.findings] == [("missing_alert", "heat_stress")]


def test_an_unexpected_alert_fails_and_names_the_code() -> None:
    """The block is exhaustive (docs/06:473-480): an alert outside it means the
    wiring opened something the scenario never promised."""
    scenario = load_scenario("normal")

    verdict = check_expected(scenario, _observed(alerts=("fungal_risk",)))

    assert not verdict.passed
    assert [(f.kind, f.observed) for f in verdict.findings] == [("unexpected_alert", "fungal_risk")]


def test_missing_and_unexpected_alerts_are_reported_together() -> None:
    scenario = load_scenario("el-nino")

    verdict = check_expected(
        scenario,
        _observed(alerts=("water_stress", "fungal_risk"), kind="irrigate"),
    )

    kinds = sorted(f.kind for f in verdict.findings)
    assert kinds == ["missing_alert", "unexpected_alert"]


def test_a_kind_mismatch_fails() -> None:
    scenario = load_scenario("dry-spell-rainfed")

    verdict = check_expected(
        scenario,
        _observed(alerts=("water_stress",), kind="irrigate", advice=("conserve_moisture",)),
    )

    assert not verdict.passed
    assert [(f.kind, f.expected, f.observed) for f in verdict.findings] == [
        ("irrigation_kind", "rainfed", "irrigate")
    ]


def test_a_missing_recommendation_fails() -> None:
    """Every block carries `irrigation`, so no stored recommendation is a failed
    assertion, never a silent pass."""
    scenario = load_scenario("dry-spell-rainfed")

    verdict = check_expected(
        scenario, _observed(alerts=("water_stress",), kind=None, advice=("conserve_moisture",))
    )

    assert not verdict.passed
    assert [(f.kind, f.expected, f.observed) for f in verdict.findings] == [
        ("irrigation_kind", "rainfed", "no recommendation stored")
    ]


def test_a_missing_advice_code_fails() -> None:
    scenario = load_scenario("dry-spell-rainfed")

    verdict = check_expected(scenario, _observed(alerts=("water_stress",), kind="rainfed"))

    assert not verdict.passed
    assert [(f.kind, f.expected) for f in verdict.findings] == [
        ("missing_advice", "conserve_moisture")
    ]


def test_an_unexpected_advice_code_fails() -> None:
    """An irrigated plot has no advice to give (docs/06:459-462): advice on the
    observed side without the block declaring it is the same kind of wiring break."""
    scenario = load_scenario("el-nino")

    verdict = check_expected(
        scenario,
        _observed(
            alerts=("water_stress", "heat_stress"),
            kind="irrigate",
            advice=("no_action",),
        ),
    )

    assert not verdict.passed
    assert [(f.kind, f.observed) for f in verdict.findings] == [("unexpected_advice", "no_action")]


def test_every_failed_assertion_is_named_in_one_verdict() -> None:
    scenario = load_scenario("dry-spell-rainfed")

    verdict = check_expected(
        scenario, _observed(alerts=(), kind="not_needed", advice=("no_action",))
    )

    kinds = [f.kind for f in verdict.findings]
    assert kinds == ["missing_alert", "irrigation_kind", "missing_advice", "unexpected_advice"]


def test_the_verdict_serializes_to_json_safe_values() -> None:
    """T5's harness prints the verdict, so `as_dict` may only carry str/bool/list."""
    scenario = load_scenario("el-nino")
    verdict = check_expected(scenario, _observed(alerts=("water_stress",), kind="irrigate"))

    payload = verdict.as_dict()

    assert payload["scenario"] == "el-nino"
    assert payload["passed"] is False
    assert isinstance(payload["findings"], list)
    assert all(
        isinstance(finding[key], str)
        for finding in payload["findings"]
        for key in ("kind", "expected", "observed")
    )


@pytest.mark.parametrize("scenario_name", ["el-nino", "dry-spell-rainfed", "normal"])
def test_checking_opens_no_socket(scenario_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The checker judges; it never reaches out. The caller owns every I/O edge."""

    def _refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the checker opened a socket")

    monkeypatch.setattr(socket, "socket", _refuse)

    # The run itself is the assertion: a socket attempt raises, a verdict does not.
    check_expected(load_scenario(scenario_name), _observed())
