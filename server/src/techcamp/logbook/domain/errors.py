"""Domain-level logbook and extension visit failures (docs/03, docs/04, ADR-0013).

Pure, no I/O (mirrors `farms.domain.errors` and `alerts.domain.errors`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from techcamp.identity.domain.models import Role
    from techcamp.logbook.domain.models import SyncEntity


class InvalidEntryError(Exception):
    """Raised when logbook entry fields or extension visit topics violate validation rules.

    Mapped to RejectReason.INVALID ("invalid") during sync push (D5, D10).
    """


InvalidLogbookEntryError = InvalidEntryError


class ForbiddenRoleError(Exception):
    """Raised when a member's role is not permitted to sync an entity.

    Mapped to RejectReason.FORBIDDEN ("forbidden") during sync push (D3).
    """

    def __init__(self, role: Role, entity: SyncEntity | None = None) -> None:
        self.role = role
        self.entity = entity
        if entity is not None:
            super().__init__(f"Role '{role}' cannot sync '{entity}'")
        else:
            super().__init__(f"Role '{role}' cannot sync")


class InsufficientRoleError(ForbiddenRoleError):
    """Raised when a member's role is not permitted to sync an entity.

    Inherits from ForbiddenRoleError matching conventions in other modules.
    """


class NaiveDatetimeError(ValueError):
    """Raised when a datetime without timezone information is passed to sync logic."""


class VisitExportForbiddenError(Exception):
    """Raised when a member's role cannot export the org's extension visits.

    Mapped to 403 by the visits router (docs/04 §Visitas; D9; RF-19): only `owner`
    and `technician` export, and a non-member never gets here (404).
    """

    def __init__(self, role: Role) -> None:
        self.role = role
        super().__init__(f"Role '{role}' cannot export visits")
