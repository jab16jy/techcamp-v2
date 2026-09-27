"""Alert domain data models, state transitions, and evaluation rules (docs/06 §3, §10; ADR-0022).

Pure domain logic: no I/O, no database dependencies. Imports only stdlib, its
own errors and `identity.domain.models.Role` (the one shared enum, the same
allowance `farms.domain` has).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
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


def ensure_can_manage_rules(role: Role) -> None:
    """D11, D15: only an `owner` creates or patches an org's alert rules.

    A rule decides when an org's farm is notified, so it is the org owner's
    call; every other role reads the rules (`GET /alert-rules`) and nothing
    more.
    """
    if role != Role.OWNER:
        raise InsufficientRoleError(role)


RESOLUTION_WINDOW = timedelta(minutes=60)
"""D2: resolution condition sustained for 60 minutes (domain constant)."""

WATER_STRESS_UPGRADE_AFTER = timedelta(hours=48)
"""docs/06 §3: water_stress alert upgraded to critical after 48 h."""

ESCALATION_DELAY = timedelta(hours=2)
"""D12: critical open alert escalated to technician after 2 h from opened_at."""

NODE_SILENCE_INTERVALS = 3
"""docs/06 §3: `node_offline` is "sin lecturas durante 3 intervalos", the same
3 × `interval_s` that "Tolerancia de huecos y frescura" makes `max_gap`."""

NON_PLOT_RULE_CODES: frozenset[str] = frozenset(
    {
        # docs/06 §3 has five sources and only "Umbral sobre lecturas" is
        # decided over a plot's sensor readings; these codes belong to the other
        # four (node health, forecast, model, balance), so they never carry a
        # plot reading threshold (D17).
        "fungal_risk",
        "heavy_rain_forecast",
        "flood_risk",
        "drought_risk",
        "node_offline",
        "node_battery_low",
    }
)
"""The rule codes the reading-threshold source of docs/06 §3 does not decide."""


@dataclass(frozen=True, slots=True)
class AlertRule:
    """Alert evaluation rule value (docs/03:272-283; docs/06 §3).

    `id` is the stored rule the evaluator decided with, so `open_alert` never
    looks a rule up by code. `org_id` is `None` for a factory rule. The
    thresholds are the `Numeric` columns of `alert_rule` read as `float`: the
    adapter converts on the way in, the domain compares numbers.
    """

    code: str
    id: UUID
    org_id: UUID | None = None
    metric: str | None = None
    operator: str | None = None
    threshold: float | None = None
    hysteresis: float = 0.0
    min_duration: timedelta = timedelta(0)
    severity: Severity = Severity.WARNING
    crop_id: int | None = None


@dataclass(frozen=True, slots=True)
class AlertRuleChanges:
    """Only the rule fields the caller stated (R3-001): `None` means "not
    stated", never "clear it", so two PATCHes cannot revert each other."""

    threshold: float | None = None
    hysteresis: float | None = None
    min_duration: timedelta | None = None
    severity: Severity | None = None


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

    def upgrade_to_critical(self) -> Alert:
        """D5: only a live alert is upgraded; a resolved one stays closed."""
        if self.state == AlertState.RESOLVED:
            raise InvalidAlertTransitionError("Cannot upgrade a resolved alert")
        return replace(self, severity=Severity.CRITICAL)

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


def is_condition_met(operator: str | None, value: float, threshold: float) -> bool:
    """True when reading `value` violates the rule condition (docs/06 §3).

    For '<': value < threshold
    For '>': value > threshold
    """
    if operator == "<":
        return value < threshold
    if operator == ">":
        return value > threshold
    return False


def is_clear_met(
    operator: str | None, value: float, threshold: float, hysteresis: float = 0.0
) -> bool:
    """True when reading `value` clears the condition beyond the hysteresis band (docs/06 §3).

    For '<': value > threshold + hysteresis
    For '>': value < threshold - hysteresis
    """
    if operator == "<":
        return value > threshold + hysteresis
    if operator == ">":
        return value < threshold - hysteresis
    return False


def sustained_run(
    samples: Sequence[tuple[datetime, float]],
    predicate: Callable[[float], bool],
    at: datetime,
    *,
    max_gap: timedelta,
) -> timedelta | None:
    """How long the predicate has held continuously up to the latest sample (D1).

    The run starts at the first sample after the last sample that failed the
    predicate. `max_gap` is the caller's 3 × `interval_s` of the node that produced
    the samples (the same margin that defines `node_offline`, docs/06 §3): two
    samples farther apart than that are not consecutive evidence, so the gap ends
    the run the same way a failing sample does, and a latest sample older than
    `max_gap` is no evidence at all (the node is offline).

    Returns None when there is no run (no samples, a stale latest sample, or the
    latest sample fails the predicate), so a zero-length run of one violating
    sample stays distinguishable.
    """
    filtered = [sample for sample in samples if sample[0] <= at]
    if not filtered:
        return None
    if at - filtered[-1][0] > max_gap:
        return None

    # Latest sample must satisfy the predicate
    if not predicate(filtered[-1][1]):
        return None

    # Search backwards for the last failing sample, or the last gap
    start_index = 0
    for i in range(len(filtered) - 2, -1, -1):
        ends_run = not predicate(filtered[i][1]) or filtered[i + 1][0] - filtered[i][0] > max_gap
        if ends_run:
            start_index = i + 1
            break

    return filtered[-1][0] - filtered[start_index][0]


def node_silence_window(interval_s: int) -> timedelta:
    """How long a node may stay silent before it counts as offline (docs/06 §3)."""
    return timedelta(seconds=NODE_SILENCE_INTERVALS * interval_s)


def heard_from_run(
    samples: Sequence[tuple[datetime, float]], at: datetime, *, max_gap: timedelta
) -> timedelta | None:
    """How long the node has been heard from without a gap longer than `max_gap`.

    The value of a sample is irrelevant here — the node speaking is the
    evidence — so this is `sustained_run` with a predicate every sample meets,
    reused so the "Tolerancia de huecos y frescura" rule stays in one place: a
    node that reports, goes quiet for more than the margin and reports again
    has no run, which is what keeps a flapping node from resolving its alert.
    """
    return sustained_run(samples, lambda _value: True, at, max_gap=max_gap)


def decide_node_health(
    *,
    last_seen_at: datetime | None,
    at: datetime,
    interval_s: int,
    current_alert: Alert | None = None,
    heard_run: timedelta | None = None,
) -> AlertDecision:
    """Decide the node-health rules of docs/06 §3, "Salud del nodo" (D18).

    `node_offline` is decided on the ABSENCE of evidence, so it gets its own
    decision instead of a series faked to look like a threshold: the seeded rule
    carries no `metric` and no `operator`, which `decide_alert` answers
    `NO_ACTION` for. The rule's own columns are never read, so the rule value
    is not a parameter; the caller holds it to open the alert with.

    - no alert + `at - last_seen_at` past 3 × `interval_s` (or never seen) -> open
    - open/acknowledged + heard from for 60 min without a gap past that same
      margin -> resolve (D2: the resolution window applies here too)
    - otherwise no action

    `heard_run` is how long the node has been heard from (see `heard_from_run`),
    read from the node's own readings; it is only needed to resolve, and only
    for a node that already has an open alert.
    """
    # A resolved alert no longer holds its (rule, target): it is decided as no
    # alert, so the silence is evaluated from scratch (same as `decide_alert`).
    if current_alert is not None and current_alert.state is AlertState.RESOLVED:
        current_alert = None

    if current_alert is None:
        silent = last_seen_at is None or at - last_seen_at > node_silence_window(interval_s)
        return AlertDecision(
            action=AlertAction.OPEN if silent else AlertAction.NO_ACTION, alert=None
        )

    if heard_run is not None and heard_run >= RESOLUTION_WINDOW:
        return AlertDecision(
            action=AlertAction.RESOLVE, alert=current_alert.resolve_automatically(at)
        )

    return AlertDecision(action=AlertAction.NO_ACTION, alert=current_alert)


def resolve_threshold(
    rule: AlertRule,
    *,
    stress_moisture_pct: float | None = None,
    field_capacity_pct: float | None = None,
) -> float | None:
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


def plot_rule_metric(rule: AlertRule) -> str | None:
    """The sensor metric a plot reading threshold is decided on, or `None` when
    the rule belongs to another source of docs/06 §3.

    `alert_rule` has no `source` column (docs/03:272-283), so the code is the
    discriminator today: the codes of the other four sources are data in
    `NON_PLOT_RULE_CODES`, never a branch per rule. Their metric is a second
    half this source never reads (`fungal_risk` also needs a 20-30 °C mean,
    docs/06 §3), a node column (`node_battery_low`) or a weather-cell value
    (`heavy_rain_forecast`), so deciding them here would be a wrong alert.

    `water_stress` **is** a plot rule: docs/06 §3 gives it the plot's θ_estrés
    and T10 supplies `stress_moisture_pct`, which is why it is not listed.
    """
    if rule.code in NON_PLOT_RULE_CODES:
        return None
    return rule.metric


def _condition_run(
    rule: AlertRule,
    samples: Sequence[tuple[datetime, float]],
    at: datetime,
    *,
    max_gap: timedelta,
    threshold: float,
) -> timedelta | None:
    """How long the rule's violating condition has held (the run that opens)."""
    return sustained_run(
        samples,
        lambda value: is_condition_met(rule.operator, value, threshold),
        at,
        max_gap=max_gap,
    )


def _clear_run(
    rule: AlertRule,
    samples: Sequence[tuple[datetime, float]],
    at: datetime,
    *,
    max_gap: timedelta,
    threshold: float,
) -> timedelta | None:
    """How long the rule has cleared beyond its hysteresis band (the 60 min run)."""
    return sustained_run(
        samples,
        lambda value: is_clear_met(rule.operator, value, threshold, rule.hysteresis),
        at,
        max_gap=max_gap,
    )


def decide_alert(
    rule: AlertRule,
    samples: Sequence[tuple[datetime, float]],
    at: datetime,
    *,
    max_gap: timedelta,
    threshold: float | None = None,
    current_alert: Alert | None = None,
    stress_moisture_pct: float | None = None,
    field_capacity_pct: float | None = None,
) -> AlertDecision:
    """Decide alert action for one rule and target given time-ordered samples (docs/06 §3).

    - no alert + condition run >= min_duration -> open (the caller opens the stored alert)
    - open/acknowledged + clear run >= 60 min -> resolve
    - water_stress open/acknowledged, still warning, at - opened_at >= 48 h and the
      condition still holding -> upgrade to critical
    - otherwise no action

    `max_gap` is the caller's 3 × `interval_s` (see `sustained_run`).
    """
    if threshold is None:
        threshold = resolve_threshold(
            rule,
            stress_moisture_pct=stress_moisture_pct,
            field_capacity_pct=field_capacity_pct,
        )
    # A resolved alert no longer holds its (rule, target): it is decided as no
    # alert, so the condition is evaluated from scratch and may open a new one.
    if current_alert is not None and current_alert.state is AlertState.RESOLVED:
        current_alert = None

    if current_alert is None:
        if threshold is None or rule.operator not in ("<", ">"):
            return AlertDecision(action=AlertAction.NO_ACTION, alert=None)

        cond_run = _condition_run(rule, samples, at, max_gap=max_gap, threshold=threshold)
        if cond_run is not None and cond_run >= rule.min_duration:
            # No alert yet, so none to return: `open_alert` builds the stored
            # value with its id, org and rule (an `Alert` always carries them).
            return AlertDecision(action=AlertAction.OPEN, alert=None)
        return AlertDecision(action=AlertAction.NO_ACTION, alert=None)

    if threshold is None or rule.operator not in ("<", ">"):
        return AlertDecision(action=AlertAction.NO_ACTION, alert=current_alert)

    # 1. Check resolution (D2: 60 min clear run)
    clear_run = _clear_run(rule, samples, at, max_gap=max_gap, threshold=threshold)
    if clear_run is not None and clear_run >= RESOLUTION_WINDOW:
        return AlertDecision(
            action=AlertAction.RESOLVE,
            alert=current_alert.resolve_automatically(at),
        )

    # 2. Check upgrade for water_stress at 48 h, only while the condition holds:
    # docs/06 §3 "crítica si dura 48 h". A plot already recovering has no
    # violating run left, so it was resolved above or is left alone here.
    if (
        rule.code == "water_stress"
        and current_alert.severity is Severity.WARNING
        and at - current_alert.opened_at >= WATER_STRESS_UPGRADE_AFTER
        and _condition_run(rule, samples, at, max_gap=max_gap, threshold=threshold) is not None
    ):
        return AlertDecision(
            action=AlertAction.UPGRADE,
            alert=current_alert.upgrade_to_critical(),
        )

    return AlertDecision(action=AlertAction.NO_ACTION, alert=current_alert)
