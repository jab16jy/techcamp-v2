"""Alerts domain logic pure unit tests (docs/06 §3, §10; ADR-0022)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from techcamp.alerts.domain import (
    BALANCE_STRESS_MAX_GAP,
    Alert,
    AlertAction,
    AlertDecision,
    AlertRule,
    AlertState,
    InvalidAlertTransitionError,
    Severity,
    balance_rule_for_stress,
    decide_alert,
    is_clear_met,
    is_condition_met,
    is_eligible_for_escalation,
    plot_rule_metric,
    resolve_threshold,
    sustained_run,
)
from techcamp.shared.ids import uuid7

_MAX_GAP = timedelta(minutes=45)
"""3 × `interval_s` of a 15 min node, the margin docs/06 §3 sets for `node_offline`."""


def _rule(
    code: str,
    *,
    metric: str | None = None,
    operator: str | None = None,
    threshold: float | None = None,
    hysteresis: float = 0.0,
    min_duration: timedelta = timedelta(0),
    severity: Severity = Severity.WARNING,
) -> AlertRule:
    """A rule value with the stored id `AlertRule` now requires."""
    return AlertRule(
        code=code,
        id=uuid7(),
        metric=metric,
        operator=operator,
        threshold=threshold,
        hysteresis=hysteresis,
        min_duration=min_duration,
        severity=severity,
    )


def _alert(state: AlertState, severity: Severity, opened_at: datetime) -> Alert:
    """A stored alert: the four identity fields every `Alert` now carries."""
    return Alert(
        state=state,
        severity=severity,
        opened_at=opened_at,
        id=uuid7(),
        org_id=uuid7(),
        rule_id=uuid7(),
        rule_code="test_rule",
    )


def _water_stress_rule() -> AlertRule:
    """The `water_stress` factory rule as the evaluator holds it (docs/06 §3): its
    threshold is the plot's θ_estrés, passed per call, and it closes at +3."""
    return _rule(
        code="water_stress",
        metric="soil_moisture",
        operator="<",
        threshold=None,
        hysteresis=3.0,
        min_duration=timedelta(minutes=360),
        severity=Severity.WARNING,
    )


def test_models_and_enums_definition():
    assert AlertState.OPEN == "open"
    assert AlertState.ACKNOWLEDGED == "acknowledged"
    assert AlertState.RESOLVED == "resolved"

    assert Severity.INFO == "info"
    assert Severity.WARNING == "warning"
    assert Severity.CRITICAL == "critical"

    rule = _rule(
        code="water_stress",
        metric="soil_moisture",
        operator="<",
        threshold=None,
        hysteresis=3.0,
        min_duration=timedelta(minutes=360),
        severity=Severity.WARNING,
    )
    assert rule.code == "water_stress"
    assert rule.min_duration == timedelta(minutes=360)

    now = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
    alert = _alert(AlertState.OPEN, Severity.WARNING, now)
    assert alert.state == AlertState.OPEN
    assert alert.opened_at == now
    assert alert.acknowledged_at is None
    assert alert.resolved_at is None
    assert alert.escalated_at is None
    assert alert.resolution_note is None

    decision = AlertDecision(action=AlertAction.OPEN, alert=alert)
    assert decision.action == AlertAction.OPEN
    assert decision.alert == alert


def test_condition_and_clear_tests_hysteresis():
    # Operator '<': condition value < threshold, clear value > threshold + hysteresis
    # threshold 15.3, hysteresis 3 -> clear > 18.3
    assert is_condition_met("<", 15.2, 15.3) is True
    assert is_condition_met("<", 15.3, 15.3) is False
    assert is_condition_met("<", 16.0, 15.3) is False

    assert is_clear_met("<", 18.4, 15.3, 3) is True
    assert is_clear_met("<", 18.3, 15.3, 3) is False
    assert is_clear_met("<", 17.0, 15.3, 3) is False

    # Between 15.3 and 18.3 is neither condition nor clear (no flapping)
    assert is_condition_met("<", 17.0, 15.3) is False
    assert is_clear_met("<", 17.0, 15.3, 3) is False

    # Operator '>': condition value > threshold, clear value < threshold - hysteresis
    # threshold 35, hysteresis 1 -> clear < 34
    assert is_condition_met(">", 35.1, 35) is True
    assert is_condition_met(">", 35.0, 35) is False
    assert is_condition_met(">", 34.5, 35) is False

    assert is_clear_met(">", 33.9, 35, 1) is True
    assert is_clear_met(">", 34.0, 35, 1) is False
    assert is_clear_met(">", 34.5, 35, 1) is False

    # Between 34.0 and 35.0 is neither
    assert is_condition_met(">", 34.5, 35) is False
    assert is_clear_met(">", 34.5, 35, 1) is False


def test_sustained_run_basic_and_reset():
    t0 = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)

    # Empty samples -> no run
    assert sustained_run([], lambda v: v > 35, t0, max_gap=_MAX_GAP) is None

    # Latest sample fails -> no run
    samples = [
        (t0, 36.0),
        (t0 + timedelta(minutes=15), 36.0),
        (t0 + timedelta(minutes=30), 34.0),  # fails
    ]
    assert (
        sustained_run(samples, lambda v: v > 35, t0 + timedelta(minutes=30), max_gap=_MAX_GAP)
        is None
    )

    # All samples satisfy -> run starts at first sample
    samples_all = [
        (t0, 36.0),
        (t0 + timedelta(minutes=15), 36.5),
        (t0 + timedelta(minutes=30), 37.0),
    ]
    assert sustained_run(
        samples_all, lambda v: v > 35, t0 + timedelta(minutes=30), max_gap=_MAX_GAP
    ) == timedelta(minutes=30)

    # A single sample that breaks the run resets it
    samples_interrupted = [
        (t0, 36.0),
        (t0 + timedelta(minutes=15), 36.0),
        (t0 + timedelta(minutes=30), 34.0),  # reset here
        (t0 + timedelta(minutes=45), 36.0),  # run starts fresh here
        (t0 + timedelta(minutes=60), 36.5),
    ]
    # Run is from 45 min to 60 min = 15 min (not 60 min)
    assert sustained_run(
        samples_interrupted, lambda v: v > 35, t0 + timedelta(minutes=60), max_gap=_MAX_GAP
    ) == timedelta(minutes=15)


def test_sustained_run_restarts_after_a_gap_wider_than_max_gap():
    """Two samples more than `max_gap` apart are not consecutive evidence: the
    3 h before the gap never count, and the run restarts at the reading after the
    gap exactly as it would after a failing one."""
    t0 = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)
    after_gap = t0 + timedelta(hours=3)
    samples = [(t0, 36.0), (after_gap, 36.0)]

    # Only the reading after the gap counts, so nothing is sustained yet.
    assert sustained_run(samples, lambda v: v > 35, after_gap, max_gap=_MAX_GAP) == timedelta(0)

    # 15 min more of evidence after the gap is a 15 min run, not a 3 h one.
    later = after_gap + timedelta(minutes=15)
    assert sustained_run(
        [*samples, (later, 36.0)], lambda v: v > 35, later, max_gap=_MAX_GAP
    ) == timedelta(minutes=15)


def test_sustained_run_needs_a_latest_sample_within_max_gap():
    """A latest reading older than `max_gap` is an offline node, not a run: the
    stored readings are not evidence of a condition that holds now."""
    t0 = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)
    samples = [(t0 + timedelta(minutes=15 * i), 36.0) for i in range(5)]
    latest = samples[-1][0]

    assert sustained_run(samples, lambda v: v > 35, latest, max_gap=_MAX_GAP) == timedelta(
        minutes=60
    )
    assert sustained_run(
        samples, lambda v: v > 35, latest + timedelta(minutes=45), max_gap=_MAX_GAP
    ) == timedelta(minutes=60)
    assert (
        sustained_run(samples, lambda v: v > 35, latest + timedelta(hours=2), max_gap=_MAX_GAP)
        is None
    )


def test_sustained_run_ignores_samples_after_the_at_cutoff():
    t0 = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)
    samples = [
        (t0, 36.0),
        (t0 + timedelta(minutes=30), 34.0),  # the latest one at 30 min: it fails
        (t0 + timedelta(minutes=45), 36.0),  # later than the cutoff
    ]

    assert (
        sustained_run(samples, lambda v: v > 35, t0 + timedelta(minutes=30), max_gap=_MAX_GAP)
        is None
    )
    assert sustained_run(
        samples, lambda v: v > 35, t0 + timedelta(minutes=45), max_gap=_MAX_GAP
    ) == timedelta(0)


def test_heat_stress_opens_after_3h_not_at_2h45():
    t0 = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)
    rule = _rule(
        code="heat_stress",
        metric="air_temp",
        operator=">",
        threshold=35.0,
        hysteresis=1.0,
        min_duration=timedelta(minutes=180),  # 3 hours
        severity=Severity.WARNING,
    )

    # Samples every 15 min above 35°C from 0 to 165 min (2 h 45 min)
    samples_2h45 = [
        (t0 + timedelta(minutes=15 * i), 36.0)
        for i in range(12)  # i=0 (0m) to i=11 (165m)
    ]
    decision_2h45 = decide_alert(
        rule,
        samples_2h45,
        t0 + timedelta(minutes=165),
        max_gap=_MAX_GAP,
    )
    assert decision_2h45.action == AlertAction.NO_ACTION
    assert decision_2h45.alert is None

    # Sample at 180 min (3 h)
    samples_3h = samples_2h45 + [(t0 + timedelta(minutes=180), 36.0)]
    decision_3h = decide_alert(
        rule,
        samples_3h,
        t0 + timedelta(minutes=180),
        max_gap=_MAX_GAP,
    )
    assert decision_3h.action == AlertAction.OPEN
    # Nothing is stored yet, so there is no alert to return: `open_alert` builds
    # it with its id, org and rule.
    assert decision_3h.alert is None
    opened = _alert(AlertState.OPEN, rule.severity, t0 + timedelta(minutes=180))
    assert opened.opened_at == t0 + timedelta(minutes=180)


def test_threshold_per_rule_code():
    rule_ws = _rule("water_stress", metric="soil_moisture", operator="<")
    assert resolve_threshold(rule_ws, stress_moisture_pct=15.3) == 15.3
    # Missing stress_moisture_pct -> None (no evaluation)
    assert resolve_threshold(rule_ws, stress_moisture_pct=None) is None

    rule_wl = _rule("waterlogging", metric="soil_moisture", operator=">")
    assert resolve_threshold(rule_wl, field_capacity_pct=23.0) == 28.0
    assert resolve_threshold(rule_wl, field_capacity_pct=None) is None

    rule_custom = _rule("custom_temp", operator=">", threshold=40.0)
    assert resolve_threshold(rule_custom) == 40.0


def test_the_balance_branch_of_water_stress_is_the_same_rule_in_daily_units():
    """D27: the balance decides on the per-day margin `(Dr / RAW) - 1`, so the
    rule it uses is `> 0`, with no hysteresis and no minimum duration.

    The 3 hysteresis points and the 6 h minimum belong to the READING series
    (moisture percentage, hourly samples); on a dimensionless daily margin the
    clear condition would sit at `< -3`, an alert that can never resolve, and a
    6 h run over one sample per day is a run that can never reach its minimum.
    The code and the severity stay, so the 48 h critical upgrade still applies."""
    stored = _rule(
        "water_stress",
        metric="soil_moisture",
        operator="<",
        threshold=None,
        hysteresis=3.0,
        min_duration=timedelta(minutes=360),
    )

    derived = balance_rule_for_stress(stored)

    assert derived.operator == ">"
    assert derived.threshold == 0.0
    assert derived.hysteresis == 0.0
    assert derived.min_duration == timedelta(0)
    assert derived.code == "water_stress"
    assert derived.severity is stored.severity
    # The rule it came from is not mutated: the reading branch still uses it.
    assert stored.operator == "<"
    assert stored.hysteresis == 3.0
    assert stored.min_duration == timedelta(minutes=360)


def test_the_daily_balance_series_tolerates_one_missed_day():
    """D27: `BALANCE_STRESS_MAX_GAP` is 3 × the daily cadence, so a day the 04:30
    job did not run is not a gap that ends the violating run — the same margin
    docs/06 §3 sets for every other series."""
    assert BALANCE_STRESS_MAX_GAP == timedelta(days=3)


def test_alert_transitions_and_errors():
    t0 = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=1)
    t2 = t0 + timedelta(hours=2)

    alert = _alert(AlertState.OPEN, Severity.WARNING, t0)

    # 1. Acknowledge: open -> acknowledged
    acked = alert.acknowledge(t1)
    assert acked.state == AlertState.ACKNOWLEDGED
    assert acked.acknowledged_at == t1

    # Acknowledging an acknowledged alert is a no-op
    acked_again = acked.acknowledge(t2)
    assert acked_again is acked
    assert acked_again.acknowledged_at == t1

    # 2. Manual resolve only from acknowledged, with optional note
    resolved_manual = acked.resolve_manually(t2, note="Revisado por técnico")
    assert resolved_manual.state == AlertState.RESOLVED
    assert resolved_manual.resolved_at == t2
    assert resolved_manual.resolution_note == "Revisado por técnico"

    # Cannot acknowledge a resolved alert
    with pytest.raises(InvalidAlertTransitionError, match="Cannot acknowledge a resolved alert"):
        resolved_manual.acknowledge(t2)

    # Manual resolve from open raises
    with pytest.raises(InvalidAlertTransitionError, match="only allowed from acknowledged state"):
        alert.resolve_manually(t1)

    # Manual resolve from resolved raises
    with pytest.raises(InvalidAlertTransitionError, match="only allowed from acknowledged state"):
        resolved_manual.resolve_manually(t2)

    # 3. Automatic resolve from open or acknowledged
    auto_resolved_from_open = alert.resolve_automatically(t1)
    assert auto_resolved_from_open.state == AlertState.RESOLVED
    assert auto_resolved_from_open.resolved_at == t1
    assert auto_resolved_from_open.resolution_note is None

    auto_resolved_from_acked = acked.resolve_automatically(t2)
    assert auto_resolved_from_acked.state == AlertState.RESOLVED
    assert auto_resolved_from_acked.resolved_at == t2

    # Automatic resolve from resolved raises
    with pytest.raises(InvalidAlertTransitionError, match="only allowed from open or acknowledged"):
        resolved_manual.resolve_automatically(t2)

    # 4. Upgrade to critical only while open or acknowledged (D5)
    assert acked.upgrade_to_critical().severity == Severity.CRITICAL
    with pytest.raises(InvalidAlertTransitionError, match="Cannot upgrade a resolved alert"):
        resolved_manual.upgrade_to_critical()


def test_upgrade_at_48h_for_water_stress():
    t0 = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)
    rule = _water_stress_rule()
    alert = _alert(AlertState.OPEN, Severity.WARNING, t0)

    # Ongoing condition: moisture still below threshold (e.g. 14.0% with threshold 15.3%)
    samples = [(t0 + timedelta(hours=47, minutes=59), 14.0)]

    # At 47 h 59 min: not yet 48 h -> no upgrade
    decision_before = decide_alert(
        rule,
        samples,
        t0 + timedelta(hours=47, minutes=59),
        max_gap=_MAX_GAP,
        current_alert=alert,
        stress_moisture_pct=15.3,
    )
    assert decision_before.action == AlertAction.NO_ACTION

    # At 48 h: upgrades to critical
    samples_48h = [(t0 + timedelta(hours=48), 14.0)]
    decision_48h = decide_alert(
        rule,
        samples_48h,
        t0 + timedelta(hours=48),
        max_gap=_MAX_GAP,
        current_alert=alert,
        stress_moisture_pct=15.3,
    )
    assert decision_48h.action == AlertAction.UPGRADE
    assert decision_48h.alert is not None
    assert decision_48h.alert.severity == Severity.CRITICAL

    # Non-water_stress rule (e.g. heat_stress) does not upgrade at 48 h
    rule_heat = _rule("heat_stress", operator=">", threshold=35.0)
    alert_heat = _alert(AlertState.OPEN, Severity.WARNING, t0)
    decision_heat = decide_alert(
        rule_heat,
        [(t0 + timedelta(hours=48), 36.0)],
        t0 + timedelta(hours=48),
        max_gap=_MAX_GAP,
        current_alert=alert_heat,
    )
    assert decision_heat.action == AlertAction.NO_ACTION


def test_a_recovering_plot_is_not_upgraded_at_48h():
    """docs/06 §3: the 48 h upgrade applies while the condition still holds ("si
    dura 48 h"). A plot whose latest reading is inside the hysteresis band, or
    clearing for less than 60 min, has no violating run left to escalate."""
    t0 = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)
    at = t0 + timedelta(hours=48)
    rule = _water_stress_rule()
    alert = _alert(AlertState.OPEN, Severity.WARNING, t0)

    # 17.0% with θ_estrés 15.3 and hysteresis 3 clears at 18.3, so 17.0 is
    # neither violating nor clearing: the plot is recovering.
    inside_band = decide_alert(
        rule,
        [(at, 17.0)],
        at,
        max_gap=_MAX_GAP,
        current_alert=alert,
        stress_moisture_pct=15.3,
    )
    assert inside_band.action == AlertAction.NO_ACTION
    assert inside_band.alert == alert

    # 18.5% clears, but for 45 min only: not yet resolved, and nothing to escalate.
    clearing = decide_alert(
        rule,
        [(at - timedelta(minutes=45), 18.5), (at, 18.5)],
        at,
        max_gap=_MAX_GAP,
        current_alert=alert,
        stress_moisture_pct=15.3,
    )
    assert clearing.action == AlertAction.NO_ACTION
    assert clearing.alert == alert


def test_an_already_critical_water_stress_is_not_upgraded_again():
    t0 = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)
    at = t0 + timedelta(hours=49)
    critical = _alert(AlertState.OPEN, Severity.CRITICAL, t0)

    decision = decide_alert(
        _water_stress_rule(),
        [(at, 14.0)],
        at,
        max_gap=_MAX_GAP,
        current_alert=critical,
        stress_moisture_pct=15.3,
    )
    assert decision.action == AlertAction.NO_ACTION
    assert decision.alert == critical


def test_an_acknowledged_alert_resolves_through_the_clear_window():
    """docs/06 §3: the automatic resolution applies to open and acknowledged alike."""
    t0 = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)
    at = t0 + timedelta(hours=49)
    acked = _alert(AlertState.OPEN, Severity.WARNING, t0).acknowledge(t0 + timedelta(hours=1))
    samples = [(at - timedelta(minutes=15 * i), 18.5) for i in reversed(range(5))]

    decision = decide_alert(
        _water_stress_rule(),
        samples,
        at,
        max_gap=_MAX_GAP,
        current_alert=acked,
        stress_moisture_pct=15.3,
    )
    assert decision.action == AlertAction.RESOLVE
    assert decision.alert is not None
    assert decision.alert.state == AlertState.RESOLVED
    assert decision.alert.resolved_at == at


def test_a_resolved_current_alert_is_decided_as_no_alert():
    """A resolved alert no longer holds the (rule, target), so the open condition is
    evaluated from scratch and may open a new alert (docs/06 §3: one non-resolved
    alert per rule and target)."""
    t0 = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)
    at = t0 + timedelta(minutes=180)
    rule = _rule(
        code="heat_stress",
        metric="air_temp",
        operator=">",
        threshold=35.0,
        hysteresis=1.0,
        min_duration=timedelta(minutes=180),
    )
    resolved = _alert(AlertState.RESOLVED, Severity.WARNING, t0)
    samples = [(t0 + timedelta(minutes=15 * i), 36.0) for i in range(13)]

    decision = decide_alert(rule, samples, at, max_gap=_MAX_GAP, current_alert=resolved)
    assert decision.action == AlertAction.OPEN
    # Nothing is stored yet, so there is no alert to return: `open_alert` builds
    # it with its id, org and rule.
    assert decision.alert is None


def test_escalation_eligibility_boundaries():
    t0 = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)
    critical_open = _alert(AlertState.OPEN, Severity.CRITICAL, t0)

    # 1 h 59 min -> not eligible
    assert is_eligible_for_escalation(critical_open, t0 + timedelta(hours=1, minutes=59)) is False

    # 2 h -> eligible
    assert is_eligible_for_escalation(critical_open, t0 + timedelta(hours=2)) is True

    # Acknowledged -> not eligible
    acked = critical_open.acknowledge(t0 + timedelta(hours=1))
    assert is_eligible_for_escalation(acked, t0 + timedelta(hours=2)) is False

    # Already escalated -> not eligible
    escalated = critical_open.escalate(t0 + timedelta(hours=2))
    assert escalated.escalated_at == t0 + timedelta(hours=2)
    assert is_eligible_for_escalation(escalated, t0 + timedelta(hours=3)) is False

    # Warning severity -> not eligible
    warning_open = _alert(AlertState.OPEN, Severity.WARNING, t0)
    assert is_eligible_for_escalation(warning_open, t0 + timedelta(hours=2)) is False

    # Trying to escalate an ineligible alert raises InvalidAlertTransitionError
    with pytest.raises(InvalidAlertTransitionError, match="not eligible for escalation"):
        warning_open.escalate(t0 + timedelta(hours=2))


def test_scenario_a_soil_moisture_linear_fall_and_resolution():
    """Scenario A (docs/06 §3, §10):

    Soil moisture falling linearly 22 -> 13% over 14 days at 900 s intervals with θ_estrés 15.3%:
    - no open before crossing + 6 h
    - open at first sample >= 6 h after it (day ≈ 10.7)
    - resolve needs > 18.3% for 60 min (a rise to 17% does not resolve).
    """
    rule = _water_stress_rule()
    theta_estres = 15.3

    t_start = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
    total_seconds = 14 * 86400  # 14 days = 1,209,600 s
    interval_s = 900  # 15 minutes
    n_samples = total_seconds // interval_s + 1

    # Generate linear trajectory: 22.0 down to 13.0
    trajectory: list[tuple[datetime, float]] = []
    for k in range(n_samples):
        t_k = t_start + timedelta(seconds=k * interval_s)
        val = 22.0 - 9.0 * (k * interval_s) / total_seconds
        trajectory.append((t_k, val))

    # Crossing: 22.0 - 9.0 * (t / 1,209,600) = 15.3 -> t_cross = 900,480 s (day 10.422)
    # Sample k=1000 is at 900,000 s -> 15.3036% (not yet < 15.3)
    # Sample k=1001 is at 900,900 s -> 15.2969% (first sample < 15.3)
    # 6 h run = 21,600 s. 900,900 + 21,600 = 922,500 s.
    # 922,500 / 900 = 1025.
    # At k=1024 (921,600 s): run is 5 h 45 min -> NO OPEN
    # At k=1025 (922,500 s): run is 6 h 00 min -> OPENS (day 10.677 ≈ 10.7)

    # Verify at k=1024: no open
    sub_1024 = trajectory[:1025]  # samples 0..1024
    dec_1024 = decide_alert(
        rule,
        sub_1024,
        sub_1024[-1][0],
        max_gap=_MAX_GAP,
        stress_moisture_pct=theta_estres,
    )
    assert dec_1024.action == AlertAction.NO_ACTION

    # Verify at k=1025: opens at first sample >= 6 h
    sub_1025 = trajectory[:1026]  # samples 0..1025
    dec_1025 = decide_alert(
        rule,
        sub_1025,
        sub_1025[-1][0],
        max_gap=_MAX_GAP,
        stress_moisture_pct=theta_estres,
    )
    assert dec_1025.action == AlertAction.OPEN
    # The alert `open_alert` would store, used below as the current one.
    assert dec_1025.alert is None
    opened_alert = _alert(AlertState.OPEN, rule.severity, sub_1025[-1][0])
    assert opened_alert.opened_at == sub_1025[-1][0]

    # Now test recovery / resolution:
    # Threshold = 15.3, hysteresis = 3.0 -> clear condition requires > 18.3%
    t_rec = opened_alert.opened_at + timedelta(days=1)

    # 1. Rise to 17.0% for 60 min -> does NOT resolve
    samples_rise_17 = [
        (t_rec + timedelta(minutes=15 * i), 17.0)
        for i in range(5)  # 0, 15, 30, 45, 60 min
    ]
    dec_rec_17 = decide_alert(
        rule,
        samples_rise_17,
        t_rec + timedelta(minutes=60),
        max_gap=_MAX_GAP,
        current_alert=opened_alert,
        stress_moisture_pct=theta_estres,
    )
    assert dec_rec_17.action == AlertAction.NO_ACTION

    # 2. Rise to 18.5% (> 18.3%) but only for 45 min -> does NOT resolve yet
    samples_rise_18_5_45m = [
        (t_rec + timedelta(minutes=15 * i), 18.5)
        for i in range(4)  # 0, 15, 30, 45 min
    ]
    dec_rec_45m = decide_alert(
        rule,
        samples_rise_18_5_45m,
        t_rec + timedelta(minutes=45),
        max_gap=_MAX_GAP,
        current_alert=opened_alert,
        stress_moisture_pct=theta_estres,
    )
    assert dec_rec_45m.action == AlertAction.NO_ACTION

    # 3. Rise to 18.5% sustained for 60 min -> RESOLVES
    samples_rise_18_5_60m = [
        (t_rec + timedelta(minutes=15 * i), 18.5)
        for i in range(5)  # 0, 15, 30, 45, 60 min
    ]
    dec_rec_60m = decide_alert(
        rule,
        samples_rise_18_5_60m,
        t_rec + timedelta(minutes=60),
        max_gap=_MAX_GAP,
        current_alert=opened_alert,
        stress_moisture_pct=theta_estres,
    )
    assert dec_rec_60m.action == AlertAction.RESOLVE
    resolved_alert = dec_rec_60m.alert
    assert resolved_alert is not None
    assert resolved_alert.state == AlertState.RESOLVED
    assert resolved_alert.resolved_at == t_rec + timedelta(minutes=60)


def test_zero_duration_rule_opens_only_on_a_violating_latest_sample():
    t0 = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)
    battery = _rule(
        code="node_battery_low",
        metric="battery_v",
        operator="<",
        threshold=3.4,
        min_duration=timedelta(0),
        severity=Severity.INFO,
    )

    assert decide_alert(battery, [], t0, max_gap=_MAX_GAP).action == AlertAction.NO_ACTION
    assert decide_alert(battery, [(t0, 3.9)], t0, max_gap=_MAX_GAP).action == AlertAction.NO_ACTION
    assert decide_alert(battery, [(t0, 3.3)], t0, max_gap=_MAX_GAP).action == AlertAction.OPEN


def test_only_the_plot_rules_of_the_ingestor_source_carry_a_metric():
    # docs/06 §3 has five sources; these six codes belong to the other four
    # (node health, forecast, model, balance), so a plot reading threshold is
    # never decided on them even with a metric of their own (D17).
    other_sources = {
        "fungal_risk": "air_rh",
        "heavy_rain_forecast": "rain",
        "flood_risk": None,
        "drought_risk": None,
        "node_offline": None,
        "node_battery_low": "battery_v",
    }
    for code, metric in other_sources.items():
        assert plot_rule_metric(_rule(code, metric=metric, operator=">")) is None

    # A plot rule keeps its metric, and `water_stress` is one of them: T10 gives
    # it the plot's `stress_moisture_pct` threshold.
    assert plot_rule_metric(_rule("heat_stress", metric="air_temp", operator=">")) == "air_temp"
    assert plot_rule_metric(_rule("waterlogging", metric="soil_moisture", operator=">")) == (
        "soil_moisture"
    )
    assert plot_rule_metric(_rule("water_stress", metric="soil_moisture", operator="<")) == (
        "soil_moisture"
    )
    assert plot_rule_metric(_rule("custom_humidity", metric="air_rh", operator=">")) == "air_rh"
