"""E16 T4: the water-stress domain test (H5, docs/06-diseno-detallado.md:486).

With each scenario's soil and crop parameters, `water_stress` opens when
`Dr > RAW` and not before — the property the whole demo rests on, checked
against the domain's own decisions and not against a restatement of the math:

- scenario A (docs/06:442-448) crosses θ_estrés = 15,3 % on day 10,4 of its
  14-day ramp, and the seeded reading rule (360 min) opens six hours later;
- scenario E (docs/06:465-471) is the node-less rainfed plot: the daily balance
  passes RAW = 77 mm on day 16 and the alert is critical 48 h later (day 18).

The numbers the docs pin (84/46,2/15,3 for A; 140/77/16/18 for E) are
assertions here, not inputs: the test feeds the domain the scenario's own
parameters and lets FAO-56's equations produce them. The library's arithmetic
is `test_scenario_library.py`'s subject (the YAML and the fixture); this file
is the domain answering that same arithmetic. Pure: no database, no clock.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any

import pytest

from techcamp.alerts.adapters.seed import FACTORY_RULES
from techcamp.alerts.domain import (
    BALANCE_STRESS_MAX_GAP,
    Alert,
    AlertAction,
    AlertRule,
    AlertState,
    Severity,
    balance_rule_for_stress,
    decide_alert,
)
from techcamp.farms.domain.models import CropStage
from techcamp.irrigation.domain.models import (
    RainfedAdvice,
    RecommendationKind,
    compute_adjusted_p,
    compute_kc_for_cycle_day,
    compute_model_depletion,
    compute_observed_depletion,
    compute_raw,
    compute_stress_moisture,
    compute_taw,
    decide_recommendation,
    stage_for_cycle_day,
)
from techcamp.shared.dates import BOGOTA_TZ
from techcamp.simulator.scenario_library import load_fixture
from techcamp.simulator.scenario_schema import Scenario, load_scenario

_MAIZE_STAGES = (
    CropStage(stage="initial", length_days=18, kc=0.30, depletion_fraction_p=0.55),
    CropStage(stage="development", length_days=27, kc=0.75, depletion_fraction_p=0.55),
    CropStage(stage="mid", length_days=31, kc=1.20, depletion_fraction_p=0.55),
    CropStage(stage="late", length_days=14, kc=0.35, depletion_fraction_p=0.55),
)
"""The maize the crop catalog seeds (migration `67cf2dd1f13e`, FAO-56 Table 12):
the same stages the balance job reads for every scenario plot."""

_SCENARIO_A = load_scenario("el-nino")
_SCENARIO_E = load_scenario("dry-spell-rainfed")

_ETC_A_MM = 5.0
"""docs/06:442 — "el fixture da ETc ≈ 5 mm/día", so p = 0,55 + 0,04 × (5 − 5)."""

_BACKFILL_DAYS_A = 14
_FIRST_SAMPLE = datetime(2026, 9, 1, tzinfo=UTC)
"""An arbitrary anchor: the domain orders instants, it never reads a calendar."""


def _seed_rule(code: str) -> AlertRule:
    """The factory rule as the evaluators read it, built from the same seed the
    scenario schema validates `expected` against (`_parse_expected`)."""
    raw: dict[str, Any] = next(rule for rule in FACTORY_RULES if rule["code"] == code)
    return AlertRule(
        code=raw["code"],
        id=raw["id"],
        metric=raw["metric"],
        operator=raw["operator"],
        threshold=float(raw["threshold"]) if raw["threshold"] is not None else None,
        hysteresis=float(raw["hysteresis"]),
        min_duration=timedelta(minutes=raw["min_duration_min"]),
        severity=Severity(raw["severity"]),
    )


def _at(day: float) -> datetime:
    """An instant `day` fractional days into the backfill."""
    return _FIRST_SAMPLE + timedelta(days=day)


def _ramp_samples(scenario: Scenario, *, through_day: float) -> list[tuple[datetime, float]]:
    """The soil ramp at the tick the runner publishes (interval_s apart), the
    series the reading branch decides on (docs/06:446: 22 % → 13 % in 14 days)."""
    ramp = scenario.trajectory(f"soil_moisture_{scenario.nodes.soil_moisture_depth_cm}cm")
    total_seconds = _BACKFILL_DAYS_A * 24 * 3600
    ticks = int(through_day * 24 * 3600 // scenario.interval_s) + 1
    return [
        (
            _at(tick * scenario.interval_s / 24 / 3600),
            ramp["start"]
            + (ramp["end"] - ramp["start"]) * (tick * scenario.interval_s / total_seconds),
        )
        for tick in range(ticks)
    ]


def _end_of_local_day(day_offset: int) -> datetime:
    """The instant local day `day_offset` ended (E6's D2 anchor): local midnight
    of the next day, the instant the balance row for that day describes."""
    local_midnight_next = datetime.combine(
        _FIRST_SAMPLE.date() + timedelta(days=day_offset + 1), time(), tzinfo=BOGOTA_TZ
    )
    return local_midnight_next.astimezone(UTC)


# --- scenario A: the reading branch on a field-calibrated sensor (docs/06:442-448) ---


def test_scenario_a_parameters_reproduce_the_documented_thresholds() -> None:
    fc = _SCENARIO_A.soil.field_capacity_pct / 100
    wp = _SCENARIO_A.soil.wilting_point_pct / 100
    zr_m = _SCENARIO_A.nodes.soil_moisture_depth_cm * 2 / 100  # sensor at Zr/2 (docs/06:445)

    p = compute_adjusted_p(0.55, _ETC_A_MM)
    taw = compute_taw(fc, wp, zr_m)
    raw = compute_raw(p, taw)
    theta_stress = _SCENARIO_A.soil.stress_threshold_pct_with(p)

    assert p == pytest.approx(0.55)
    assert zr_m == pytest.approx(0.6)
    assert taw == pytest.approx(84.0), "TAW = 1000 × 0,14 × 0,6 (docs/06:445)"
    assert raw == pytest.approx(46.2), "RAW = 0,55 × 84 (docs/06:445)"
    assert theta_stress == pytest.approx(15.3), "θ_estrés = 15,3 % (docs/06:444)"


def test_water_stress_opens_six_hours_after_the_crossing_and_never_before() -> None:
    """docs/06:446: the ramp crosses 15,3 % on day 10,4 and the alert opens six
    hours later. Between the crossing and the 360-minute run the answer is NO —
    the negative half of H5, in the hours where a loose threshold would lie."""
    rule = _seed_rule("water_stress")
    threshold = _SCENARIO_A.soil.stress_threshold_pct_with(0.55)
    samples = _ramp_samples(_SCENARIO_A, through_day=11)
    max_gap = timedelta(seconds=3 * _SCENARIO_A.interval_s)

    day_10 = decide_alert(rule, samples, _at(10.0), max_gap=max_gap, stress_moisture_pct=threshold)
    assert day_10.action is AlertAction.NO_ACTION, "θ = 15,57 % is still above θ_estrés"

    day_10_5 = decide_alert(
        rule, samples, _at(10.5), max_gap=max_gap, stress_moisture_pct=threshold
    )
    assert day_10_5.action is AlertAction.NO_ACTION, (
        "θ crossed 10,4 days in, but the 6-hour run is not there yet"
    )

    day_10_75 = decide_alert(
        rule, samples, _at(10.75), max_gap=max_gap, stress_moisture_pct=threshold
    )
    assert day_10_75.action is AlertAction.OPEN, "≈ day 10,7: the documented opening"


def test_the_moisture_threshold_is_strict_at_theta_stress_itself() -> None:
    """H5's iff at the edge: θ == θ_estrés is NOT a violation (operator `<`),
    the same strictness ADR-0022 gives `Dr == RAW`."""
    rule = _seed_rule("water_stress")
    threshold = 15.3
    flat = [(_at(i * 15 / 24 / 60), threshold) for i in range(50 * 24 * 4)]  # 50 h at θ_estrés
    max_gap = timedelta(minutes=45)

    for at in (_at(0.25), _at(2.0), _at(49.9)):
        decision = decide_alert(rule, flat, at, max_gap=max_gap, stress_moisture_pct=threshold)
        assert decision.action is AlertAction.NO_ACTION, f"θ == θ_estrés at day {at}"


def test_the_moisture_and_depletion_thresholds_are_the_same_line() -> None:
    """The iff in both units (docs/06:486): θ < θ_estrés ⇔ Dr(Zr) > RAW — the
    sensor branch and the balance branch read one threshold, not two. The
    domain speaks fractions; the scenario YAML speaks percentages, and the two
    helpers answer the same line in their own unit."""
    fc, wp, zr_m = 0.23, 0.09, 0.6
    taw = compute_taw(fc, wp, zr_m)
    raw = compute_raw(0.55, taw)
    theta_stress = compute_stress_moisture(fc, wp, 0.55)

    for theta in (0.13, theta_stress - 0.0001, theta_stress + 0.0001, 0.22):
        dr = compute_observed_depletion(fc, theta, zr_m, taw)
        assert (dr > raw) is (theta < theta_stress), f"θ = {theta}"
    assert compute_observed_depletion(fc, theta_stress, zr_m, taw) == pytest.approx(raw), (
        "θ == θ_estrés is exactly Dr == RAW"
    )
    assert _SCENARIO_A.soil.stress_threshold_pct_with(0.55) == pytest.approx(theta_stress * 100), (
        "the YAML helper's percentage is the domain threshold, not a second one"
    )


def test_scenario_a_recommends_irrigating_the_observed_depletion() -> None:
    """docs/06:447: θobs = 13 % → Dr_obs = 60 mm > RAW 46,2 mm → `irrigate`.
    The domain reads θ as a fraction, the YAML publishes percentages."""
    dr = compute_observed_depletion(0.23, 0.13, 0.6, 84.0)
    assert dr == pytest.approx(60.0, abs=0.6)

    recommendation = decide_recommendation(
        has_active_cycle=True,
        is_rainfed=False,
        kc_source="fao56",
        dr=dr,
        raw=compute_raw(0.55, 84.0),
        irrigation_efficiency=None,
        area_m2=1.5 * 10_000,
        system_flow_lph=None,
        forecast_rain_48h_mm=0.0,
        forecast_rain_7d_mm=0.0,
        forecast_et0_7d_mm=7 * _ETC_A_MM,
        stage="mid",
    )

    assert recommendation is not None
    assert recommendation.kind is RecommendationKind.IRRIGATE
    assert recommendation.depth_mm is not None and recommendation.depth_mm > 0
    assert recommendation.advice == (), "an irrigated plot has no advice (docs/06:459-462)"


# --- scenario E: the balance branch on a node-less rainfed plot (docs/06:465-471) ---


def test_scenario_e_parameters_reproduce_the_documented_thresholds() -> None:
    fixture = load_fixture("veranillo-caribe")
    observed = fixture.observed
    assert len(observed) == 21, "the veranillo is 21 observed days (docs/06:458)"

    day_of_cycle = _SCENARIO_E.plot.sown_days_ago + 1
    assert stage_for_cycle_day(_MAIZE_STAGES, day_of_cycle) == "mid", (
        "sown 55 days ago is flowering, the mid stage (docs/06:455, #340)"
    )
    kc = compute_kc_for_cycle_day(_MAIZE_STAGES, day_of_cycle)
    assert kc == pytest.approx(1.20), "mid-stage Kc (docs/06:465)"

    et0 = sum(day.et0_mm for day in observed) / len(observed)
    etc = kc * et0
    assert etc == pytest.approx(5.0, abs=0.5), "ETc ≈ 5 mm/día (docs/06:467)"
    assert all(day.effective_rain_mm == 0.0 for day in fixture.days), "Pe = 0 (docs/06:469)"

    p = compute_adjusted_p(0.55, etc)
    taw = compute_taw(0.23, 0.09, 1.0)  # Zr = 1,0 m at flowering (docs/06:468)
    raw = compute_raw(p, taw)

    assert taw == pytest.approx(140.0), "TAW = 1000 × 0,14 × 1,0 (docs/06:468)"
    assert raw == pytest.approx(77.0, abs=0.5), "RAW = 77 mm (docs/06:468)"


def _balance_rows(*, etc_mm: float, raw_mm: float, taw_mm: float) -> list[tuple[datetime, float]]:
    """The daily balances as the balance branch reads them: one row per day for
    21 days, Dr = 5 × day (no rain, no irrigation, docs/06:469-470), each
    anchored at its local day's end, as the margin `(Dr / RAW) − 1` the
    evaluator decides on."""
    dr = 0.0
    rows: list[tuple[datetime, float]] = []
    for day_offset in range(21):
        rows.append((_end_of_local_day(day_offset), dr / raw_mm - 1.0))
        dr = compute_model_depletion(dr, etc_mm, 0.0, 0.0, taw_mm)
    return rows


def test_the_balance_opens_water_stress_on_day_16_and_never_before() -> None:
    """docs/06:470: day 15 is 75 ≤ RAW 77, day 16 is 80 > RAW — the balance
    branch opens there and the daily series before it never opens anything."""
    fixture = load_fixture("veranillo-caribe")
    etc = 1.20 * sum(day.et0_mm for day in fixture.observed) / len(fixture.observed)
    raw = compute_raw(compute_adjusted_p(0.55, etc), 140.0)
    taw = 140.0
    rule = balance_rule_for_stress(_seed_rule("water_stress"))
    rows = _balance_rows(etc_mm=etc, raw_mm=raw, taw_mm=taw)

    day_15 = decide_alert(
        rule, rows[:16], rows[15][0], max_gap=BALANCE_STRESS_MAX_GAP, threshold=0.0
    )
    assert day_15.action is AlertAction.NO_ACTION, "75 mm ≤ RAW 77 mm on day 15"

    day_16 = decide_alert(
        rule, rows[:17], rows[16][0], max_gap=BALANCE_STRESS_MAX_GAP, threshold=0.0
    )
    assert day_16.action is AlertAction.OPEN, "80 mm > RAW 77 mm on day 16"


def test_the_balance_upgrades_the_open_alert_to_critical_on_day_18() -> None:
    """docs/06:470: the alert is still open 48 h later, and `water_stress` at
    48 h goes critical (docs/06 §3)."""
    fixture = load_fixture("veranillo-caribe")
    etc = 1.20 * sum(day.et0_mm for day in fixture.observed) / len(fixture.observed)
    raw = compute_raw(compute_adjusted_p(0.55, etc), 140.0)
    rule = balance_rule_for_stress(_seed_rule("water_stress"))
    rows = _balance_rows(etc_mm=etc, raw_mm=raw, taw_mm=140.0)

    opened_at = rows[16][0]
    alert = Alert(
        state=AlertState.OPEN,
        severity=Severity.WARNING,
        opened_at=opened_at,
        id=_seed_rule("water_stress").id,
        org_id=None,
        rule_id=_seed_rule("water_stress").id,
        rule_code="water_stress",
    )
    decision = decide_alert(
        rule,
        rows[:19],
        rows[18][0],
        max_gap=BALANCE_STRESS_MAX_GAP,
        threshold=0.0,
        current_alert=alert,
    )

    assert decision.action is AlertAction.UPGRADE
    assert decision.alert is not None and decision.alert.severity is Severity.CRITICAL


def test_the_balance_threshold_is_strict_at_dr_equal_to_raw() -> None:
    """ADR-0022's edge on the balance side: `Dr == RAW` is `watch`, never a
    crossing — `> 0` on the margin, and 0 is not above 0."""
    rule = balance_rule_for_stress(_seed_rule("water_stress"))
    raw = 77.0
    flat = [
        (_end_of_local_day(day_offset), raw / raw - 1.0)  # margin exactly 0: Dr == RAW
        for day_offset in range(10)
    ]

    decision = decide_alert(rule, flat, flat[-1][0], max_gap=BALANCE_STRESS_MAX_GAP, threshold=0.0)
    assert decision.action is AlertAction.NO_ACTION


def test_scenario_e_recommends_rainfed_conserve_moisture_and_never_harvest() -> None:
    """docs/06:471: Dr ≥ RAW with no forecast rain advises `conserve_moisture`;
    mid is not `late`, so `prioritize_harvest` never appears."""
    fixture = load_fixture("veranillo-caribe")
    et0 = sum(day.et0_mm for day in fixture.observed) / len(fixture.observed)
    etc = 1.20 * et0
    raw = compute_raw(compute_adjusted_p(0.55, etc), 140.0)
    dr = etc * 21
    assert dr < 140.0, "day 21 is below TAW — wilting never arrives (docs/06:471)"

    recommendation = decide_recommendation(
        has_active_cycle=True,
        is_rainfed=True,
        kc_source="fao56",
        dr=dr,
        raw=raw,
        irrigation_efficiency=None,
        area_m2=2 * 10_000,
        system_flow_lph=None,
        forecast_rain_48h_mm=0.0,
        forecast_rain_7d_mm=0.0,
        forecast_et0_7d_mm=7 * et0,
        stage="mid",
    )

    assert recommendation is not None
    assert recommendation.kind is RecommendationKind.RAINFED
    assert recommendation.depth_mm is None and recommendation.duration_min is None
    assert recommendation.advice == (RainfedAdvice.CONSERVE_MOISTURE,)
