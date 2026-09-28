"""Ports the sync push depends on (ADR-0002; docs/05; docs/04 §Bitácora).

Repositories are external I/O (Postgres), so what these abstractions buy is
the layering rule: the use case in `push.py` names the state it needs and
never imports SQLAlchemy. The change dataclasses live here because they are
the ports' own request shapes, and the stored-row projection is the ports'
answer to "what does the row under the lock hold".
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from techcamp.logbook.domain.models import SyncEntity, SyncOp


@dataclass(frozen=True, slots=True)
class LogbookEntryChange:
    """One `logbook_entry` change as the client sent it (docs/04 §Bitácora).

    `kind` travels as the wire string, not as a `LogbookKind`: an unknown kind
    is a field-rule failure of that one change (`rejected` `invalid`), not a
    422 for the whole batch (docs/04 §Bitácora names the `CHECK` per `kind` as
    an `invalid` case).
    """

    id: UUID
    op: SyncOp
    client_updated_at: datetime
    plot_id: UUID
    crop_cycle_id: UUID | None
    kind: str
    occurred_on: date
    quantity: Decimal | None
    unit: str | None
    cost_cop: Decimal | None
    yield_kg: Decimal | None
    sold_kg: Decimal | None
    sale_price_cop_per_kg: Decimal | None
    labor_days: Decimal | None
    irrigation_mm: Decimal | None
    alert_id: UUID | None
    notes: str | None
    created_offline: bool
    """docs/11 counts "entradas de bitácora creadas sin conexión", so only the
    client knows this: the phone stamps it at first save and sends it in
    `data` (docs/04 §Bitácora). Stored on insert, never rewritten."""


@dataclass(frozen=True, slots=True)
class ExtensionVisitChange:
    """One `extension_visit` change as the client sent it (docs/04 §Bitácora)."""

    id: UUID
    op: SyncOp
    client_updated_at: datetime
    farm_id: UUID
    plot_id: UUID | None
    technician_id: UUID
    visited_on: date
    topics: list[str]
    recommendations: str | None
    commitments: str | None
    notes: str | None


@dataclass(frozen=True, slots=True)
class StoredSyncRow:
    """What a push needs of the row it re-reads under the D1 lock.

    Deliberately not the whole row: an applied change writes the incoming
    values, so only the ownership (`org_id`), the LWW timestamp, the version
    the answer carries and the D13 tombstone are ever read.
    """

    org_id: UUID
    client_updated_at: datetime
    server_version: int
    deleted_at: datetime | None


class SyncTransaction(Protocol):
    """D1 ordering and D5 per-change isolation over the request's transaction.

    `lock()` is `pg_advisory_xact_lock` on the sync key: held until the
    transaction ends, so a version can only be allocated by a transaction that
    is about to commit, which is what makes the plain sequence commit-ordered.
    `savepoint()` is `BEGIN SAVEPOINT`, so a rejected change rolls back alone.
    """

    async def lock(self) -> None: ...

    async def next_server_version(self) -> int: ...

    def savepoint(self) -> AbstractAsyncContextManager[object]: ...


class SyncIdProbe(Protocol):
    """D4: an `id` owned by the other entity answers `not_found` like a missing
    one. The two tables have independent primary keys, so without this probe an
    entry id reused for a visit would insert cleanly and pull would return one
    id twice."""

    async def exists_in_other(self, *, entity: SyncEntity, row_id: UUID) -> bool: ...


class LogbookEntrySyncRepository(Protocol):
    async def get_for_update(self, entry_id: UUID) -> StoredSyncRow | None:
        """The stored row locked with `SELECT … FOR UPDATE`, read under the D1
        lock. Not filtered by org on purpose: the caller has to tell "no such
        row" from "a row of another org", and both answer `not_found` (D4)."""
        ...

    async def insert(
        self,
        change: LogbookEntryChange,
        *,
        org_id: UUID,
        caller_id: UUID,
        server_version: int,
    ) -> None:
        """A new id: `created_by` is the caller, and `created_offline` is what
        the client said it was (docs/11 counts those entries, so the server
        never guesses it)."""
        ...

    async def update(
        self, change: LogbookEntryChange, *, org_id: UUID, server_version: int
    ) -> None:
        """The change wins: every data column plus `client_updated_at` and the
        new `server_version`. `org_id`, `created_by` and `created_offline` are
        not written, so the audit columns keep the facts of the first save:
        a later edit from a phone back online must not relabel the entry."""
        ...

    async def soft_delete(
        self,
        entry_id: UUID,
        *,
        org_id: UUID,
        deleted_at: datetime,
        client_updated_at: datetime,
        server_version: int,
    ) -> None:
        """D12: only the tombstone, the incoming `client_updated_at` and the new
        `server_version`. The fields are never validated or rewritten, so a
        change rejected as `invalid` can still be deleted."""
        ...


class ExtensionVisitSyncRepository(Protocol):
    async def get_for_update(self, visit_id: UUID) -> StoredSyncRow | None: ...

    async def insert(
        self, change: ExtensionVisitChange, *, org_id: UUID, server_version: int
    ) -> None: ...

    async def update(
        self, change: ExtensionVisitChange, *, org_id: UUID, server_version: int
    ) -> None: ...

    async def soft_delete(
        self,
        visit_id: UUID,
        *,
        org_id: UUID,
        deleted_at: datetime,
        client_updated_at: datetime,
        server_version: int,
    ) -> None: ...
