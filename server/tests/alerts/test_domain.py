"""Alerts domain logic pure unit tests (docs/06 §3, §10; ADR-0022)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from techcamp.alerts.domain import (
    Alert,
    AlertAction,
    AlertDecision,
    AlertRule,
    AlertState,
    InvalidAlertTransitionError,
    Severity,
    decide_alert,
    is_clear_met,
    is_condition_met,
    is_eligible_for_escalation,
    resolve_threshold,
    sustained_run,
)


def test_models_and_enums_definition():
    assert AlertState.OPEN == "open"
    assert AlertState.ACKNOWLEDGED == "acknowledged"
    assert AlertState.RESOLVED == "resolved"

    assert Severity.INFO == "info"
    assert Severity.WARNING == "warning"
    assert Severity.CRITICAL == "critical"

    rule = AlertRule(
        code="water_stress",
        metric="soil_moisture",
        operator="<",
        threshold=None,
        hysteresis=Decimal("3"),
        min_duration=timedelta(minutes=360),
        severity=Severity.WARNING,
    )
    assert rule.code == "water_stress"
    assert rule.min_duration == timedelta(minutes=360)

    now = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
    alert = Alert(state=AlertState.OPEN, severity=Severity.WARNING, opened_at=now)
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
    assert sustained_run([], lambda v: v > 35) is None

    # Latest sample fails -> no run
    samples = [
        (t0, 36.0),
        (t0 + timedelta(minutes=15), 36.0),
        (t0 + timedelta(minutes=30), 34.0),  # fails
    ]
    assert sustained_run(samples, lambda v: v > 35) is None

    # All samples satisfy -> run starts at first sample
    samples_all = [
        (t0, 36.0),
        (t0 + timedelta(minutes=15), 36.5),
        (t0 + timedelta(minutes=30), 37.0),
    ]
    assert sustained_run(samples_all, lambda v: v > 35) == timedelta(minutes=30)

    # A single sample that breaks the run resets it
    samples_interrupted = [
        (t0, 36.0),
        (t0 + timedelta(minutes=15), 36.0),
        (t0 + timedelta(minutes=30), 34.0),  # reset here
        (t0 + timedelta(minutes=45), 36.0),  # run starts fresh here
        (t0 + timedelta(minutes=60), 36.5),
    ]
    # Run is from 45 min to 60 min = 15 min (not 60 min)
    assert sustained_run(samples_interrupted, lambda v: v > 35) == timedelta(minutes=15)


def test_heat_stress_opens_after_3h_not_at_2h45():
    t0 = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)
    rule = AlertRule(
        code="heat_stress",
        metric="air_temp",
        operator=">",
        threshold=Decimal("35"),
        hysteresis=Decimal("1"),
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
        at=t0 + timedelta(minutes=165),
    )
    assert decision_2h45.action == AlertAction.NO_ACTION
    assert decision_2h45.alert is None

    # Sample at 180 min (3 h)
    samples_3h = samples_2h45 + [(t0 + timedelta(minutes=180), 36.0)]
    decision_3h = decide_alert(
        rule,
        samples_3h,
        at=t0 + timedelta(minutes=180),
    )
    assert decision_3h.action == AlertAction.OPEN
    assert decision_3h.alert is not None
    assert decision_3h.alert.state == AlertState.OPEN
    assert decision_3h.alert.severity == Severity.WARNING
    assert decision_3h.alert.opened_at == t0 + timedelta(minutes=180)


def test_threshold_per_rule_code():
    rule_ws = AlertRule(code="water_stress", metric="soil_moisture", operator="<")
    assert resolve_threshold(rule_ws, stress_moisture_pct=15.3) == 15.3
    # Missing stress_moisture_pct -> None (no evaluation)
    assert resolve_threshold(rule_ws, stress_moisture_pct=None) is None

    rule_wl = AlertRule(code="waterlogging", metric="soil_moisture", operator=">")
    assert resolve_threshold(rule_wl, field_capacity_pct=23.0) == 28.0
    assert resolve_threshold(rule_wl, field_capacity_pct=Decimal("23")) == Decimal("28")
    assert resolve_threshold(rule_wl, field_capacity_pct=None) is None

    rule_custom = AlertRule(code="custom_temp", operator=">", threshold=Decimal("40"))
    assert resolve_threshold(rule_custom) == Decimal("40")


def test_alert_transitions_and_errors():
    t0 = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=1)
    t2 = t0 + timedelta(hours=2)

    alert = Alert(state=AlertState.OPEN, severity=Severity.WARNING, opened_at=t0)

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


def test_upgrade_at_48h_for_water_stress():
    t0 = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)
    rule = AlertRule(
        code="water_stress",
        metric="soil_moisture",
        operator="<",
        hysteresis=Decimal("3"),
        min_duration=timedelta(minutes=360),
        severity=Severity.WARNING,
    )
    alert = Alert(state=AlertState.OPEN, severity=Severity.WARNING, opened_at=t0)

    # Ongoing condition: moisture still below threshold (e.g. 14.0% with threshold 15.3%)
    samples = [(t0 + timedelta(hours=47, minutes=59), 14.0)]

    # At 47 h 59 min: not yet 48 h -> no upgrade
    decision_before = decide_alert(
        rule,
        samples,
        at=t0 + timedelta(hours=47, minutes=59),
        current_alert=alert,
        stress_moisture_pct=15.3,
    )
    assert decision_before.action == AlertAction.NO_ACTION

    # At 48 h: upgrades to critical
    samples_48h = [(t0 + timedelta(hours=48), 14.0)]
    decision_48h = decide_alert(
        rule,
        samples_48h,
        at=t0 + timedelta(hours=48),
        current_alert=alert,
        stress_moisture_pct=15.3,
    )
    assert decision_48h.action == AlertAction.UPGRADE
    assert decision_48h.alert is not None
    assert decision_48h.alert.severity == Severity.CRITICAL

    # Non-water_stress rule (e.g. heat_stress) does not upgrade at 48 h
    rule_heat = AlertRule(code="heat_stress", operator=">", threshold=Decimal("35"))
    alert_heat = Alert(state=AlertState.OPEN, severity=Severity.WARNING, opened_at=t0)
    decision_heat = decide_alert(
        rule_heat,
        [(t0 + timedelta(hours=48), 36.0)],
        at=t0 + timedelta(hours=48),
        current_alert=alert_heat,
    )
    assert decision_heat.action == AlertAction.NO_ACTION


def test_escalation_eligibility_boundaries():
    t0 = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)
    critical_open = Alert(
        state=AlertState.OPEN,
        severity=Severity.CRITICAL,
        opened_at=t0,
    )

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
    warning_open = Alert(state=AlertState.OPEN, severity=Severity.WARNING, opened_at=t0)
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
    rule = AlertRule(
        code="water_stress",
        metric="soil_moisture",
        operator="<",
        threshold=None,  # dynamic
        hysteresis=Decimal("3"),
        min_duration=timedelta(minutes=360),  # 6 h
        severity=Severity.WARNING,
    )
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
        at=sub_1024[-1][0],
        stress_moisture_pct=theta_estres,
    )
    assert dec_1024.action == AlertAction.NO_ACTION

    # Verify at k=1025: opens at first sample >= 6 h
    sub_1025 = trajectory[:1026]  # samples 0..1025
    dec_1025 = decide_alert(
        rule,
        sub_1025,
        at=sub_1025[-1][0],
        stress_moisture_pct=theta_estres,
    )
    assert dec_1025.action == AlertAction.OPEN
    opened_alert = dec_1025.alert
    assert opened_alert is not None
    assert opened_alert.state == AlertState.OPEN
    assert opened_alert.severity == Severity.WARNING
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
        at=t_rec + timedelta(minutes=60),
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
        at=t_rec + timedelta(minutes=45),
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
        at=t_rec + timedelta(minutes=60),
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
    battery = AlertRule(
        code="node_battery_low",
        metric="battery_v",
        operator="<",
        threshold=Decimal("3.4"),
        min_duration=timedelta(0),
        severity=Severity.INFO,
    )

    assert decide_alert(battery, [], t0).action == AlertAction.NO_ACTION
    assert decide_alert(battery, [(t0, 3.9)], t0).action == AlertAction.NO_ACTION
    assert decide_alert(battery, [(t0, 3.3)], t0).action == AlertAction.OPEN
