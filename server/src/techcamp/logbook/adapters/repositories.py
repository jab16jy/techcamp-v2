"""Postgres adapters for the sync push (docs/04 §Bitácora; docs/06 §7; D1, D5).

Owns the session, the SQL and the D1 lock. Only these classes import
SQLAlchemy, and none of them decides a sync outcome.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import insert, select, text, update
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.logbook.adapters.orm import (
    ExtensionVisitRow,
    LogbookEntryRow,
    sync_server_version_seq,
)
from techcamp.logbook.application.ports import (
    ExtensionVisitChange,
    LogbookEntryChange,
    StoredSyncRow,
)
from techcamp.logbook.domain.errors import InvalidEntryError
from techcamp.logbook.domain.models import SyncEntity

SYNC_LOCK_KEY = int.from_bytes(b"tcsyncv1", "big")
"""D1's fixed `pg_advisory_xact_lock` key, the first eight bytes of a name no
other lock in this database uses. Every push takes the same one, so allocation
order is commit order (docs/06 §7)."""


class PostgresSyncTransaction:
    """The request's transaction: D1's lock, the shared version sequence and
    the per-change savepoints (D5)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock(self) -> None:
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": SYNC_LOCK_KEY}
        )

    async def next_server_version(self) -> int:
        """Taken under the lock, never before it (D1)."""
        result = await self._session.execute(select(sync_server_version_seq.next_value()))
        version: int = result.scalar_one()
        return version

    def savepoint(self) -> AbstractAsyncContextManager[object]:
        return self._session.begin_nested()


class SqlAlchemySyncIdProbe:
    """D4's cross-entity probe: the two synced tables have independent primary
    keys, so without it an id reused for the other entity would insert cleanly
    and pull would return one id twice."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def exists_in_other(self, *, entity: SyncEntity, row_id: UUID) -> bool:
        other = ExtensionVisitRow if entity is SyncEntity.LOGBOOK_ENTRY else LogbookEntryRow
        result = await self._session.execute(select(other.id).where(other.id == row_id))
        return result.scalar_one_or_none() is not None


def _stored(row: Row[Any]) -> StoredSyncRow:
    return StoredSyncRow(
        org_id=row.org_id,
        client_updated_at=row.client_updated_at,
        server_version=row.server_version,
        deleted_at=row.deleted_at,
    )


def _check_violation(entity: str) -> InvalidEntryError:
    """D5's backstop: the per-`kind` CHECKs and the topics vocabulary `CHECK`
    are the table's last line, so a violation the domain rules did not catch is
    `invalid`, never a 500 (docs/04 §Bitácora). The change's savepoint rolls
    the failed statement back and the batch continues.

    The database message is not echoed back: it names columns and rules the
    caller has no business reading (the same rule
    `SqlAlchemyAlertRuleRepository.create` follows).
    """
    return InvalidEntryError(f"{entity} violates a table constraint")


class SqlAlchemyLogbookEntrySyncRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_update(self, entry_id: UUID) -> StoredSyncRow | None:
        """The stored row under `SELECT … FOR UPDATE`.

        No `org_id` filter on purpose: the caller has to tell a row of another
        org from no row at all (D4), and both answer `not_found`.
        """
        result = await self._session.execute(
            select(
                LogbookEntryRow.org_id,
                LogbookEntryRow.client_updated_at,
                LogbookEntryRow.server_version,
                LogbookEntryRow.deleted_at,
            )
            .where(LogbookEntryRow.id == entry_id)
            .with_for_update()
        )
        row = result.one_or_none()
        return _stored(row) if row is not None else None

    async def insert(
        self,
        change: LogbookEntryChange,
        *,
        org_id: UUID,
        caller_id: UUID,
        server_version: int,
    ) -> None:
        try:
            await self._session.execute(
                insert(LogbookEntryRow).values(
                    id=change.id,
                    org_id=org_id,
                    plot_id=change.plot_id,
                    crop_cycle_id=change.crop_cycle_id,
                    # `change.kind` was accepted as a `LogbookKind` by the use
                    # case, so this string is one of the six the `CHECK` allows.
                    kind=change.kind,
                    occurred_on=change.occurred_on,
                    quantity=change.quantity,
                    unit=change.unit,
                    cost_cop=change.cost_cop,
                    yield_kg=change.yield_kg,
                    sold_kg=change.sold_kg,
                    sale_price_cop_per_kg=change.sale_price_cop_per_kg,
                    labor_days=change.labor_days,
                    irrigation_mm=change.irrigation_mm,
                    alert_id=change.alert_id,
                    notes=change.notes,
                    created_by=caller_id,
                    created_offline=change.created_offline,
                    client_updated_at=change.client_updated_at,
                    server_version=server_version,
                    deleted_at=None,
                )
            )
        except IntegrityError as exc:
            raise _check_violation("logbook entry") from exc

    async def update(
        self, change: LogbookEntryChange, *, org_id: UUID, server_version: int
    ) -> None:
        try:
            await self._session.execute(
                update(LogbookEntryRow)
                .where(LogbookEntryRow.id == change.id, LogbookEntryRow.org_id == org_id)
                .values(
                    plot_id=change.plot_id,
                    crop_cycle_id=change.crop_cycle_id,
                    kind=change.kind,
                    occurred_on=change.occurred_on,
                    quantity=change.quantity,
                    unit=change.unit,
                    cost_cop=change.cost_cop,
                    yield_kg=change.yield_kg,
                    sold_kg=change.sold_kg,
                    sale_price_cop_per_kg=change.sale_price_cop_per_kg,
                    labor_days=change.labor_days,
                    irrigation_mm=change.irrigation_mm,
                    alert_id=change.alert_id,
                    notes=change.notes,
                    client_updated_at=change.client_updated_at,
                    server_version=server_version,
                )
            )
        except IntegrityError as exc:
            raise _check_violation("logbook entry") from exc

    async def soft_delete(
        self,
        entry_id: UUID,
        *,
        org_id: UUID,
        deleted_at: datetime,
        client_updated_at: datetime,
        server_version: int,
    ) -> None:
        await self._session.execute(
            update(LogbookEntryRow)
            .where(LogbookEntryRow.id == entry_id, LogbookEntryRow.org_id == org_id)
            .values(
                deleted_at=deleted_at,
                client_updated_at=client_updated_at,
                server_version=server_version,
            )
        )


class SqlAlchemyExtensionVisitSyncRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_update(self, visit_id: UUID) -> StoredSyncRow | None:
        result = await self._session.execute(
            select(
                ExtensionVisitRow.org_id,
                ExtensionVisitRow.client_updated_at,
                ExtensionVisitRow.server_version,
                ExtensionVisitRow.deleted_at,
            )
            .where(ExtensionVisitRow.id == visit_id)
            .with_for_update()
        )
        row = result.one_or_none()
        return _stored(row) if row is not None else None

    async def insert(
        self, change: ExtensionVisitChange, *, org_id: UUID, server_version: int
    ) -> None:
        try:
            await self._session.execute(
                insert(ExtensionVisitRow).values(
                    id=change.id,
                    org_id=org_id,
                    farm_id=change.farm_id,
                    plot_id=change.plot_id,
                    technician_id=change.technician_id,
                    visited_on=change.visited_on,
                    topics=list(change.topics),
                    recommendations=change.recommendations,
                    commitments=change.commitments,
                    notes=change.notes,
                    client_updated_at=change.client_updated_at,
                    server_version=server_version,
                    deleted_at=None,
                )
            )
        except IntegrityError as exc:
            raise _check_violation("extension visit") from exc

    async def update(
        self, change: ExtensionVisitChange, *, org_id: UUID, server_version: int
    ) -> None:
        try:
            await self._session.execute(
                update(ExtensionVisitRow)
                .where(ExtensionVisitRow.id == change.id, ExtensionVisitRow.org_id == org_id)
                .values(
                    farm_id=change.farm_id,
                    plot_id=change.plot_id,
                    technician_id=change.technician_id,
                    visited_on=change.visited_on,
                    topics=list(change.topics),
                    recommendations=change.recommendations,
                    commitments=change.commitments,
                    notes=change.notes,
                    client_updated_at=change.client_updated_at,
                    server_version=server_version,
                )
            )
        except IntegrityError as exc:
            raise _check_violation("extension visit") from exc

    async def soft_delete(
        self,
        visit_id: UUID,
        *,
        org_id: UUID,
        deleted_at: datetime,
        client_updated_at: datetime,
        server_version: int,
    ) -> None:
        await self._session.execute(
            update(ExtensionVisitRow)
            .where(ExtensionVisitRow.id == visit_id, ExtensionVisitRow.org_id == org_id)
            .values(
                deleted_at=deleted_at,
                client_updated_at=client_updated_at,
                server_version=server_version,
            )
        )
