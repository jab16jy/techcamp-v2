"""Logbook domain errors (docs/04 §Visitas; docs/09; D9)."""

from __future__ import annotations

from techcamp.identity.domain.models import Role


class InsufficientRoleError(Exception):
    """Raised when a member's role cannot export extension visits (docs/04; D9)."""

    def __init__(self, role: Role) -> None:
        self.role = role
        super().__init__(f"Role '{role.value}' cannot export visits")
