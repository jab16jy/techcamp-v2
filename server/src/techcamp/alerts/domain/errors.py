"""Domain-level alert failures. Pure, no I/O (mirrors `farms.domain.errors`).

Kept as alerts' own types: `domain` never imports another module's `domain`
(`telemetry.domain.errors.InsufficientRoleError` says the same).
"""

from __future__ import annotations

from uuid import UUID

from techcamp.identity.domain.models import Role


class AlertNotFoundError(Exception):
    """Raised when an alert doesn't exist or isn't in one of the caller's orgs.

    docs/04: a resource of another organization responds 404, never 403, so it
    never reveals that it exists.
    """

    def __init__(self, alert_id: UUID) -> None:
        self.alert_id = alert_id
        super().__init__(f"Alert {alert_id} not found")


class InsufficientRoleError(Exception):
    """Raised when a member's role may not manage alerts or alert rules."""

    def __init__(self, role: Role) -> None:
        self.role = role
        super().__init__(f"Role '{role}' cannot manage alerts")


class AlertRuleNotFoundError(Exception):
    """Raised when a rule doesn't exist, is a factory rule, or is another
    org's (D11: an org never edits the system rules).

    docs/04: 404, never 403, so a rule of another organization is not revealed.
    """

    def __init__(self, rule_id: UUID) -> None:
        self.rule_id = rule_id
        super().__init__(f"Alert rule {rule_id} not found")


class InvalidAlertRuleError(Exception):
    """Raised when the database refuses a rule, today a `crop_id` that doesn't
    exist: a client error (422), not a 500."""
