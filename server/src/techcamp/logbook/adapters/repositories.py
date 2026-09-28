"""SQLAlchemy repositories for logbook (docs/03:249-263; docs/04 §Visitas; docs/09; D9)."""

from __future__ import annotations

import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.logbook.adapters.orm import ExtensionVisitRow
from techcamp.logbook.domain.models import ExtensionVisit


def _visit_from_row(row: ExtensionVisitRow) -> ExtensionVisit:
    return ExtensionVisit(
        id=row.id,
        org_id=row.org_id,
        farm_id=row.farm_id,
        plot_id=row.plot_id,
        technician_id=row.technician_id,
        visited_on=row.visited_on,
        topics=list(row.topics),
        recommendations=row.recommendations,
        commitments=row.commitments,
        notes=row.notes,
        client_updated_at=row.client_updated_at,
        server_version=row.server_version,
        deleted_at=row.deleted_at,
    )


class SqlAlchemyExtensionVisitRepository:
    """Extension visit queries filtered by org_id, newest first (D9)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_farm(
        self,
        farm_id: UUID,
        org_id: UUID,
        *,
        limit: int = 50,
        cursor: UUID | None = None,
    ) -> list[ExtensionVisit]:
        stmt = select(ExtensionVisitRow).where(
            ExtensionVisitRow.farm_id == farm_id,
            ExtensionVisitRow.org_id == org_id,
            ExtensionVisitRow.deleted_at.is_(None),
        )
        if cursor is not None:
            stmt = stmt.where(ExtensionVisitRow.id < cursor)
        result = await self._session.execute(
            stmt.order_by(ExtensionVisitRow.id.desc()).limit(limit)
        )
        return [_visit_from_row(row) for row in result.scalars()]

    async def list_for_org(
        self,
        org_id: UUID,
        *,
        from_date: datetime.date | None = None,
        to_date: datetime.date | None = None,
        limit: int = 50,
        cursor: UUID | None = None,
    ) -> list[ExtensionVisit]:
        stmt = select(ExtensionVisitRow).where(
            ExtensionVisitRow.org_id == org_id,
            ExtensionVisitRow.deleted_at.is_(None),
        )
        if from_date is not None:
            stmt = stmt.where(ExtensionVisitRow.visited_on >= from_date)
        if to_date is not None:
            stmt = stmt.where(ExtensionVisitRow.visited_on <= to_date)
        if cursor is not None:
            stmt = stmt.where(ExtensionVisitRow.id < cursor)
        result = await self._session.execute(
            stmt.order_by(ExtensionVisitRow.id.desc()).limit(limit)
        )
        return [_visit_from_row(row) for row in result.scalars()]
