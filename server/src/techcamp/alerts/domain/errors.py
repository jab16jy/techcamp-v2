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
    """Raised when a member's role may not acknowledge or resolve an alert."""

    def __init__(self, role: Role) -> None:
        self.role = role
        super().__init__(f"Role '{role}' cannot manage alerts")
