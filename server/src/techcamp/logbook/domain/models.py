"""Logbook domain entities and invariants (docs/03:249-263, 430-448; docs/04; D9)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from techcamp.identity.domain.models import Role
from techcamp.logbook.domain.errors import InsufficientRoleError

EXPORT_ROLES: frozenset[Role] = frozenset({Role.OWNER, Role.TECHNICIAN})
"""Roles authorized to export organization visits (docs/04 §Visitas; D9; RF-19)."""


def ensure_can_export_visits(role: Role) -> None:
    """Reject an export from a role outside EXPORT_ROLES (producer/viewer -> 403)."""
    if role not in EXPORT_ROLES:
        raise InsufficientRoleError(role)


@dataclass(frozen=True, slots=True)
class ExtensionVisit:
    """Extension visit entity (docs/03:249-263, 430-448; docs/06 §7)."""

    id: UUID
    org_id: UUID
    farm_id: UUID
    plot_id: UUID | None
    technician_id: UUID
    visited_on: date
    topics: list[str]
    recommendations: str | None
    commitments: str | None
    notes: str | None
    client_updated_at: datetime
    server_version: int
    deleted_at: datetime | None = None
