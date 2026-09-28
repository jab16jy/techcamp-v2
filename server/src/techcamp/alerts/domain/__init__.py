"""Alerts domain package."""

from techcamp.alerts.domain.errors import AlertNotFoundError, InsufficientRoleError
from techcamp.alerts.domain.models import (
    ESCALATION_DELAY,
    RESOLUTION_WINDOW,
    WATER_STRESS_UPGRADE_AFTER,
    Alert,
    AlertAction,
    AlertDecision,
    AlertRule,
    AlertState,
    InvalidAlertTransitionError,
    Severity,
    decide_alert,
    ensure_can_manage_alert,
    is_clear_met,
    is_condition_met,
    is_eligible_for_escalation,
    resolve_threshold,
    sustained_run,
)

__all__ = [
    "ESCALATION_DELAY",
    "RESOLUTION_WINDOW",
    "WATER_STRESS_UPGRADE_AFTER",
    "Alert",
    "AlertAction",
    "AlertDecision",
    "AlertNotFoundError",
    "AlertRule",
    "AlertState",
    "InsufficientRoleError",
    "InvalidAlertTransitionError",
    "Severity",
    "decide_alert",
    "ensure_can_manage_alert",
    "is_clear_met",
    "is_condition_met",
    "is_eligible_for_escalation",
    "resolve_threshold",
    "sustained_run",
]
