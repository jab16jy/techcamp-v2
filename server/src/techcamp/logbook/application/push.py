"""Offline push: one change in, one result out (docs/04 §Bitácora; docs/06 §7).

Per change, in this order: the clock, the caller's role in the row's org, the
references the change points at, then the D1 lock and the re-read of the stored
row before deciding. Nothing read before the lock is trusted after it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import NoReturn
from uuid import UUID

from techcamp.alerts.application.ports import AlertRepository
from techcamp.farms.application.ports import CropCycleRepository, FarmRepository, PlotRepository
from techcamp.identity.application.ports import MembershipRepository
from techcamp.logbook.application.ports import (
    ExtensionVisitChange,
    ExtensionVisitSyncRepository,
    LogbookEntryChange,
    LogbookEntrySyncRepository,
    StoredSyncRow,
    SyncIdProbe,
    SyncTransaction,
)
from techcamp.logbook.domain.errors import InsufficientRoleError, InvalidEntryError
from techcamp.logbook.domain.models import (
    LogbookEntryFields,
    LogbookKind,
    RejectReason,
    SyncEntity,
    SyncOp,
    SyncStatus,
    decide_push,
    ensure_can_sync,
    ensure_valid_entry,
    ensure_valid_topics,
    is_clock_skewed,
)

MAX_BATCH_CHANGES = 100
"""D5: a batch carries at most 100 changes, more answers 422 (docs/04
§Bitácora). Enforced by the request model; the use case applies whatever the
caller hands it."""


@dataclass(frozen=True, slots=True)
class PushResult:
    """One line of the `results` table (docs/04 §Bitácora)."""

    id: UUID
    status: SyncStatus
    server_version: int | None
    error: RejectReason | None


class _Rejected(Exception):
    """A per-change rejection carrying the stable code the client maps to copy
    (D5). Raised inside the change's savepoint, so it rolls back that change
    alone and the batch continues."""

    def __init__(self, reason: RejectReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


def _reject(reason: RejectReason) -> NoReturn:
    raise _Rejected(reason)


def _fields(change: LogbookEntryChange) -> LogbookEntryFields:
    """The incoming values as the domain models them, so D10's rules apply to
    exactly what the client sent. An unknown `kind` is a `ValueError`, which
    the caller answers as `invalid`."""
    return LogbookEntryFields(
        kind=LogbookKind(change.kind),
        quantity=change.quantity,
        unit=change.unit,
        cost_cop=change.cost_cop,
        yield_kg=change.yield_kg,
        sold_kg=change.sold_kg,
        sale_price_cop_per_kg=change.sale_price_cop_per_kg,
        labor_days=change.labor_days,
        irrigation_mm=change.irrigation_mm,
    )


def _decide(stored: StoredSyncRow | None, incoming: datetime) -> tuple[SyncStatus, int | None]:
    """D4 in one place: the status, and the `server_version` the answer carries
    when nothing is written (the stored one, docs/04 §Bitácora)."""
    if stored is None:
        return SyncStatus.APPLIED, None
    status = decide_push(stored.client_updated_at, incoming)
    return status, stored.server_version if status is not SyncStatus.APPLIED else None


async def push_logbook_entry(
    change: LogbookEntryChange,
    *,
    caller_id: UUID,
    now: datetime,
    tx: SyncTransaction,
    ids: SyncIdProbe,
    entries: LogbookEntrySyncRepository,
    memberships: MembershipRepository,
    plots: PlotRepository,
    cycles: CropCycleRepository,
    alerts: AlertRepository,
) -> PushResult:
    """Apply one `logbook_entry` change. Never raises: every rejection is a
    `PushResult` (D5), and nothing written offline is dropped silently."""
    if is_clock_skewed(change.client_updated_at, now):
        return PushResult(change.id, SyncStatus.REJECTED, None, RejectReason.CLOCK_SKEW)
    try:
        async with tx.savepoint():
            return await _apply_entry(
                change,
                caller_id=caller_id,
                now=now,
                tx=tx,
                ids=ids,
                entries=entries,
                memberships=memberships,
                plots=plots,
                cycles=cycles,
                alerts=alerts,
            )
    except _Rejected as rejected:
        return PushResult(change.id, SyncStatus.REJECTED, None, rejected.reason)
    except InvalidEntryError:
        # A D10 rule the client broke. Its change stays local with this error
        # until the user fixes or discards it (docs/06 §7, D7).
        return PushResult(change.id, SyncStatus.REJECTED, None, RejectReason.INVALID)


async def _apply_entry(
    change: LogbookEntryChange,
    *,
    caller_id: UUID,
    now: datetime,
    tx: SyncTransaction,
    ids: SyncIdProbe,
    entries: LogbookEntrySyncRepository,
    memberships: MembershipRepository,
    plots: PlotRepository,
    cycles: CropCycleRepository,
    alerts: AlertRepository,
) -> PushResult:
    roles_by_org = {m.org_id: m.role for m in await memberships.list_for_user(caller_id)}
    plot = await plots.get_for_orgs(change.plot_id, list(roles_by_org))
    if plot is None:
        # A plot of another org, or one the caller belongs to no org of, is a
        # missing plot: the answer never reveals that it exists (D4, docs/09).
        _reject(RejectReason.NOT_FOUND)
    try:
        ensure_can_sync(SyncEntity.LOGBOOK_ENTRY, roles_by_org[plot.org_id])
    except InsufficientRoleError:
        _reject(RejectReason.FORBIDDEN)

    if change.crop_cycle_id is not None:
        # D6: nullable, but a cycle of this plot when it is there.
        cycle = await cycles.get_for_orgs(change.crop_cycle_id, list(roles_by_org))
        if cycle is None or cycle.plot_id != plot.id:
            _reject(RejectReason.NOT_FOUND)
    if change.alert_id is not None:
        alert = await alerts.get_for_orgs(change.alert_id, list(roles_by_org))
        if alert is None:
            _reject(RejectReason.NOT_FOUND)
        if alert.plot_id != plot.id:
            _reject(RejectReason.ALERT_PLOT_MISMATCH)

    # D1: the lock, then the re-read. The stored row is what decides and it is
    # read here, under the lock, never from a read taken before it.
    await tx.lock()
    if await ids.exists_in_other(entity=SyncEntity.LOGBOOK_ENTRY, row_id=change.id):
        _reject(RejectReason.NOT_FOUND)
    stored = await entries.get_for_update(change.id)
    if stored is not None and stored.org_id != plot.org_id:
        _reject(RejectReason.NOT_FOUND)

    if change.op is SyncOp.DELETE:
        return await _delete_entry(stored, change, now=now, tx=tx, entries=entries)
    if stored is not None and stored.deleted_at is not None:
        # D13: deletes are final, an upsert on a tombstone is not an undelete.
        _reject(RejectReason.NOT_FOUND)

    status, answer = _decide(stored, change.client_updated_at)
    if status is not SyncStatus.APPLIED:
        return PushResult(change.id, status, answer, None)
    try:
        ensure_valid_entry(_fields(change))
    except ValueError as exc:  # LogbookKind(change.kind) on an unknown kind
        raise InvalidEntryError(f"unknown logbook kind '{change.kind}'") from exc
    version = await tx.next_server_version()
    if stored is None:
        await entries.insert(
            change, org_id=plot.org_id, caller_id=caller_id, server_version=version
        )
    else:
        await entries.update(change, org_id=plot.org_id, server_version=version)
    return PushResult(change.id, SyncStatus.APPLIED, version, None)


async def _delete_entry(
    stored: StoredSyncRow | None,
    change: LogbookEntryChange,
    *,
    now: datetime,
    tx: SyncTransaction,
    entries: LogbookEntrySyncRepository,
) -> PushResult:
    """D12: the last write wins as in an upsert, and a row the server never
    received is `applied` with nothing written and no version."""
    if stored is None:
        return PushResult(change.id, SyncStatus.APPLIED, None, None)
    status, answer = _decide(stored, change.client_updated_at)
    if status is not SyncStatus.APPLIED:
        return PushResult(change.id, status, answer, None)
    version = await tx.next_server_version()
    await entries.soft_delete(
        change.id,
        org_id=stored.org_id,
        deleted_at=now,
        client_updated_at=change.client_updated_at,
        server_version=version,
    )
    return PushResult(change.id, SyncStatus.APPLIED, version, None)


async def push_extension_visit(
    change: ExtensionVisitChange,
    *,
    caller_id: UUID,
    now: datetime,
    tx: SyncTransaction,
    ids: SyncIdProbe,
    visits: ExtensionVisitSyncRepository,
    memberships: MembershipRepository,
    farms: FarmRepository,
    plots: PlotRepository,
) -> PushResult:
    """Apply one `extension_visit` change (same contract as the entry's, with
    the visit's own role and reference rules)."""
    if is_clock_skewed(change.client_updated_at, now):
        return PushResult(change.id, SyncStatus.REJECTED, None, RejectReason.CLOCK_SKEW)
    try:
        async with tx.savepoint():
            return await _apply_visit(
                change,
                caller_id=caller_id,
                now=now,
                tx=tx,
                ids=ids,
                visits=visits,
                memberships=memberships,
                farms=farms,
                plots=plots,
            )
    except _Rejected as rejected:
        return PushResult(change.id, SyncStatus.REJECTED, None, rejected.reason)
    except InvalidEntryError:
        return PushResult(change.id, SyncStatus.REJECTED, None, RejectReason.INVALID)


async def _apply_visit(
    change: ExtensionVisitChange,
    *,
    caller_id: UUID,
    now: datetime,
    tx: SyncTransaction,
    ids: SyncIdProbe,
    visits: ExtensionVisitSyncRepository,
    memberships: MembershipRepository,
    farms: FarmRepository,
    plots: PlotRepository,
) -> PushResult:
    roles_by_org = {m.org_id: m.role for m in await memberships.list_for_user(caller_id)}
    farm = await farms.get_for_orgs(change.farm_id, list(roles_by_org))
    if farm is None:
        _reject(RejectReason.NOT_FOUND)
    try:
        ensure_can_sync(SyncEntity.EXTENSION_VISIT, roles_by_org[farm.org_id])
    except InsufficientRoleError:
        _reject(RejectReason.FORBIDDEN)
    if change.technician_id != caller_id:
        # docs/03 §extension_visit: `technician_id` is who synchronizes.
        _reject(RejectReason.FORBIDDEN)
    if change.plot_id is not None:
        plot = await plots.get_for_orgs(change.plot_id, list(roles_by_org))
        if plot is None or plot.farm_id != farm.id:
            _reject(RejectReason.NOT_FOUND)

    await tx.lock()
    if await ids.exists_in_other(entity=SyncEntity.EXTENSION_VISIT, row_id=change.id):
        _reject(RejectReason.NOT_FOUND)
    stored = await visits.get_for_update(change.id)
    if stored is not None and stored.org_id != farm.org_id:
        _reject(RejectReason.NOT_FOUND)

    if change.op is SyncOp.DELETE:
        if stored is None:
            return PushResult(change.id, SyncStatus.APPLIED, None, None)
        status, answer = _decide(stored, change.client_updated_at)
        if status is not SyncStatus.APPLIED:
            return PushResult(change.id, status, answer, None)
        version = await tx.next_server_version()
        await visits.soft_delete(
            change.id,
            org_id=stored.org_id,
            deleted_at=now,
            client_updated_at=change.client_updated_at,
            server_version=version,
        )
        return PushResult(change.id, SyncStatus.APPLIED, version, None)
    if stored is not None and stored.deleted_at is not None:
        _reject(RejectReason.NOT_FOUND)

    status, answer = _decide(stored, change.client_updated_at)
    if status is not SyncStatus.APPLIED:
        return PushResult(change.id, status, answer, None)
    ensure_valid_topics(change.topics)
    version = await tx.next_server_version()
    if stored is None:
        await visits.insert(change, org_id=farm.org_id, server_version=version)
    else:
        await visits.update(change, org_id=farm.org_id, server_version=version)
    return PushResult(change.id, SyncStatus.APPLIED, version, None)
