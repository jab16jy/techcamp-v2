"""Logbook application ports (ADR-0002)."""

from __future__ import annotations

from datetime import date
from typing import Protocol
from uuid import UUID

from techcamp.logbook.domain.models import ExtensionVisit


class ExtensionVisitRepository(Protocol):
    """Extension visit persistence port (docs/03:249-263; docs/04 §Visitas)."""

    async def list_for_farm(
        self,
        farm_id: UUID,
        org_id: UUID,
        *,
        limit: int = 50,
        cursor: UUID | None = None,
    ) -> list[ExtensionVisit]:
        """List visits for a farm within org_id, excluding deleted, newest first."""
        ...

    async def list_for_org(
        self,
        org_id: UUID,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
        limit: int = 50,
        cursor: UUID | None = None,
    ) -> list[ExtensionVisit]:
        """List visits for an org, optionally filtered by inclusive [from_date, to_date],
        excluding deleted, newest first.
        """
        ...
