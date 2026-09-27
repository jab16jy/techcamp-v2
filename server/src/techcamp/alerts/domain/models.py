"""Alert domain data models, state transitions, and evaluation rules (docs/06 §3, §10; ADR-0022).

Pure domain logic: no I/O, no database dependencies. Imports only stdlib, its
own errors and `identity.domain.models.Role` (the one shared enum, the same
allowance `farms.domain` has).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from techcamp.alerts.domain.errors import InsufficientRoleError
from techcamp.identity.domain.models import Role


class AlertState(StrEnum):
    """Alert lifecycle state (docs/00-glosario.md; docs/06 §3)."""

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class Severity(StrEnum):
    """Alert severity level (docs/03:288; docs/06 §3)."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertAction(StrEnum):
    """Action resulting from alert rule evaluation."""

    OPEN = "open"
    RESOLVE = "resolve"
    UPGRADE = "upgrade"
    NO_ACTION = "no_action"


class InvalidAlertTransitionError(Exception):
    """Raised on invalid alert state transitions (mapped to HTTP 409 in adapters)."""


def ensure_can_manage_alert(role: Role) -> None:
    """Deny `viewer` from acknowledging or resolving an alert.

    A viewer reads the tray; acknowledging is a claim of the work, and resolving
    closes it, so neither is offered to that role (docs/04 §Alertas).
    """
    if role == Role.VIEWER:
        raise InsufficientRoleError(role)


RESOLUTION_WINDOW = timedelta(minutes=60)
"""D2: resolution condition sustained for 60 minutes (domain constant)."""

WATER_STRESS_UPGRADE_AFTER = timedelta(hours=48)
"""docs/06 §3: water_stress alert upgraded to critical after 48 h."""

ESCALATION_DELAY = timedelta(hours=2)
"""D12: critical open alert escalated to technician after 2 h from opened_at."""


@dataclass(frozen=True, slots=True)
class AlertRule:
    """Alert evaluation rule value (docs/03:272-283; docs/06 §3).

    `id` is the stored rule the evaluator decided with, so `open_alert` never
    looks a rule up by code. `org_id` is `None` for a factory rule.
    """

    code: str
    id: UUID
    org_id: UUID | None = None
    metric: str | None = None
    operator: str | None = None
    threshold: Decimal | float | None = None
    hysteresis: Decimal | float = Decimal(0)
    min_duration: timedelta = timedelta(0)
    severity: Severity = Severity.WARNING
    crop_id: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.min_duration, (int, float)):
            object.__setattr__(self, "min_duration", timedelta(minutes=self.min_duration))
        if isinstance(self.severity, str) and not isinstance(self.severity, Severity):
            object.__setattr__(self, "severity", Severity(self.severity))


@dataclass(frozen=True, slots=True)
class Alert:
    """Alert entity value (docs/03:284-297; docs/06 §3).

    The four identity fields are the alert row's (docs/03 `alert`), so an alert
    always knows which row, organization and rule it is; `plot_id` and `node_id`
    are mutually exclusive there (`ck_alert_target_exactly_one`). `rule_code`
    travels with the alert because the `NOTIFY` payload carries the code and
    nothing else joins on it (ADR-0015: ids and minimal data).
    """

    state: AlertState
    severity: Severity
    opened_at: datetime
    id: UUID
    org_id: UUID
    rule_id: UUID
    rule_code: str
    plot_id: UUID | None = None
    node_id: UUID | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    escalated_at: datetime | None = None
    resolution_note: str | None = None
    outcome: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.state, str) and not isinstance(self.state, AlertState):
            object.__setattr__(self, "state", AlertState(self.state))
        if isinstance(self.severity, str) and not isinstance(self.severity, Severity):
            object.__setattr__(self, "severity", Severity(self.severity))

    def acknowledge(self, at: datetime) -> Alert:
        """Acknowledge an open alert (docs/06 §3). Acknowledging acknowledged is a no-op."""
        if self.state == AlertState.RESOLVED:
            raise InvalidAlertTransitionError("Cannot acknowledge a resolved alert")
        if self.state == AlertState.ACKNOWLEDGED:
            return self
        return replace(self, state=AlertState.ACKNOWLEDGED, acknowledged_at=at)

    def resolve_manually(self, at: datetime, note: str | None = None) -> Alert:
        """Manual resolution only allowed from acknowledged state (docs/06 §3 diagram)."""
        if self.state != AlertState.ACKNOWLEDGED:
            raise InvalidAlertTransitionError(
                f"Manual resolution is only allowed from acknowledged state (current: {self.state})"
            )
        return replace(
            self,
            state=AlertState.RESOLVED,
            resolved_at=at,
            resolution_note=note,
        )

    def resolve_automatically(self, at: datetime) -> Alert:
        """Automatic resolution from open or acknowledged state (docs/06 §3)."""
        if self.state not in (AlertState.OPEN, AlertState.ACKNOWLEDGED):
            raise InvalidAlertTransitionError(
                "Automatic resolution is only allowed from open or acknowledged state "
                f"(current: {self.state})"
            )
        return replace(
            self,
            state=AlertState.RESOLVED,
            resolved_at=at,
        )

    def escalate(self, at: datetime) -> Alert:
        """Escalate a critical open alert (D12)."""
        if not is_eligible_for_escalation(self, at):
            raise InvalidAlertTransitionError("Alert is not eligible for escalation")
        return replace(self, escalated_at=at)


@dataclass(frozen=True, slots=True)
class AlertDecision:
    """Outcome of rule evaluation for a target."""

    action: AlertAction
    alert: Alert | None = None


def is_condition_met(
    operator: str | None,
    value: float | Decimal,
    threshold: float | Decimal,
) -> bool:
    """True when reading `value` violates the rule condition (docs/06 §3).

    For '<': value < threshold
    For '>': value > threshold
    """
    v = float(value)
    t = float(threshold)
    if operator == "<":
        return v < t
    if operator == ">":
        return v > t
    return False


def is_clear_met(
    operator: str | None,
    value: float | Decimal,
    threshold: float | Decimal,
    hysteresis: float | Decimal = Decimal(0),
) -> bool:
    """True when reading `value` clears the condition beyond the hysteresis band (docs/06 §3).

    For '<': value > threshold + hysteresis
    For '>': value < threshold - hysteresis
    """
    v = float(value)
    t = float(threshold)
    h = float(hysteresis)
    if operator == "<":
        return v > t + h
    if operator == ">":
        return v < t - h
    return False


def sustained_run(
    samples: Sequence[tuple[datetime, float | Decimal]],
    predicate: Callable[[float], bool],
    at: datetime | None = None,
) -> timedelta | None:
    """How long the predicate has held continuously up to the latest sample (D1).

    The run starts at the first sample after the last sample that failed the predicate.
    Returns None when there is no run (no samples, or the latest sample fails the
    predicate), so a zero-length run of one violating sample stays distinguishable.
    """
    if not samples:
        return None
    if at is not None:
        filtered = [s for s in samples if s[0] <= at]
    else:
        filtered = list(samples)
    if not filtered:
        return None

    # Latest sample must satisfy the predicate
    if not predicate(float(filtered[-1][1])):
        return None

    # Search backwards for the last failing sample
    start_index = 0
    for i in range(len(filtered) - 2, -1, -1):
        if not predicate(float(filtered[i][1])):
            start_index = i + 1
            break

    return filtered[-1][0] - filtered[start_index][0]


def resolve_threshold(
    rule: AlertRule,
    *,
    stress_moisture_pct: float | Decimal | None = None,
    field_capacity_pct: float | Decimal | None = None,
) -> float | Decimal | None:
    """Determine effective threshold for a rule (docs/06 §3; ADR-0022).

    - water_stress -> plot's stress_moisture_pct (missing -> None, no evaluation)
    - waterlogging -> plot field capacity (%) + 5
    - other rules -> rule's own threshold
    """
    if rule.code == "water_stress":
        return stress_moisture_pct
    if rule.code == "waterlogging":
        if field_capacity_pct is None:
            return None
        if isinstance(field_capacity_pct, Decimal):
            return field_capacity_pct + Decimal(5)
        return field_capacity_pct + 5
    return rule.threshold


def is_eligible_for_escalation(alert: Alert, now: datetime) -> bool:
    """True when critical, open (not acknowledged), unescalated, and open >= 2 h (D12)."""
    return (
        alert.severity == Severity.CRITICAL
        and alert.state == AlertState.OPEN
        and alert.escalated_at is None
        and now - alert.opened_at >= ESCALATION_DELAY
    )


# Alias for convenience
is_escalation_eligible = is_eligible_for_escalation


def decide_alert(
    rule: AlertRule,
    samples: Sequence[tuple[datetime, float | Decimal]],
    at: datetime,
    *,
    threshold: float | Decimal | None = None,
    current_alert: Alert | None = None,
    stress_moisture_pct: float | Decimal | None = None,
    field_capacity_pct: float | Decimal | None = None,
) -> AlertDecision:
    """Decide alert action for one rule and target given time-ordered samples (docs/06 §3).

    - none + condition run >= min_duration -> open (the caller opens the stored alert)
    - open/acknowledged + clear run >= 60 min -> resolve
    - water_stress open/acknowledged, still warning, and at - opened_at >= 48 h ->
      upgrade to critical
    - otherwise no action
    """
    if threshold is None:
        threshold = resolve_threshold(
            rule,
            stress_moisture_pct=stress_moisture_pct,
            field_capacity_pct=field_capacity_pct,
        )

    if current_alert is None:
        if threshold is None or rule.operator not in ("<", ">"):
            return AlertDecision(action=AlertAction.NO_ACTION, alert=None)

        thresh_f = float(threshold)
        cond_run = sustained_run(
            samples,
            lambda v: is_condition_met(rule.operator, v, thresh_f),
            at=at,
        )
        if cond_run is not None and cond_run >= rule.min_duration:
            # No alert yet, so none to return: `open_alert` builds the stored
            # value with its id, org and rule (an `Alert` always carries them).
            return AlertDecision(action=AlertAction.OPEN, alert=None)
        return AlertDecision(action=AlertAction.NO_ACTION, alert=None)

    if current_alert.state in (AlertState.OPEN, AlertState.ACKNOWLEDGED):
        # 1. Check resolution (D2: 60 min clear run)
        if threshold is not None and rule.operator in ("<", ">"):
            thresh_f = float(threshold)
            hyst_f = float(rule.hysteresis)
            clear_run = sustained_run(
                samples,
                lambda v: is_clear_met(rule.operator, v, thresh_f, hyst_f),
                at=at,
            )
            if clear_run is not None and clear_run >= RESOLUTION_WINDOW:
                return AlertDecision(
                    action=AlertAction.RESOLVE,
                    alert=current_alert.resolve_automatically(at),
                )

        # 2. Check upgrade for water_stress at 48 h
        if rule.code == "water_stress" and current_alert.severity == Severity.WARNING:
            if at - current_alert.opened_at >= WATER_STRESS_UPGRADE_AFTER:
                return AlertDecision(
                    action=AlertAction.UPGRADE,
                    alert=replace(current_alert, severity=Severity.CRITICAL),
                )

    return AlertDecision(action=AlertAction.NO_ACTION, alert=current_alert)
