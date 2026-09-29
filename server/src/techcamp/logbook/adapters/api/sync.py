"""`POST /sync/push` (docs/04 §Bitácora; docs/06 §7; ADR-0013).

The request is one transaction: the D1 lock is taken once for the whole batch
and the session commits at the end, so the versions the batch allocates are
allocated in commit order. Each change's own savepoint is what keeps one
rejection from sinking the rest (D5).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import AwareDatetime, BaseModel, Field

from techcamp.alerts.adapters.api.deps import AlertRepoDep
from techcamp.farms.adapters.api.deps import CropCycleRepoDep, FarmRepoDep, PlotRepoDep
from techcamp.identity.adapters.api.deps import CurrentUserId, MembershipRepoDep
from techcamp.logbook.adapters.api.deps import (
    EntrySyncRepoDep,
    SyncIdProbeDep,
    SyncTransactionDep,
    VisitSyncRepoDep,
)
from techcamp.logbook.application import pull_changes
from techcamp.logbook.application.ports import ExtensionVisitChange, LogbookEntryChange
from techcamp.logbook.application.push import (
    MAX_BATCH_CHANGES,
    PushResult,
    push_extension_visit,
    push_logbook_entry,
)
from techcamp.logbook.domain.models import SyncEntity, SyncOp
from techcamp.shared.db import SessionDep
from techcamp.shared.errors import ProblemError

router = APIRouter(tags=["logbook"])


class LogbookEntryData(BaseModel):
    """`data` of a `logbook_entry` change: the columns of docs/03 §logbook_entry
    for that `kind`, `alert_id` included."""

    plot_id: UUID
    crop_cycle_id: UUID | None = None
    kind: str
    occurred_on: date
    quantity: Decimal | None = None
    unit: str | None = None
    cost_cop: Decimal | None = None
    yield_kg: Decimal | None = None
    sold_kg: Decimal | None = None
    sale_price_cop_per_kg: Decimal | None = None
    labor_days: Decimal | None = None
    irrigation_mm: Decimal | None = None
    alert_id: UUID | None = None
    notes: str | None = None
    created_offline: bool = False
    """docs/11 counts the entries the client created with no signal, so the
    phone sends it; absent means the client did not claim it."""


class ExtensionVisitData(BaseModel):
    """`data` of an `extension_visit` change (docs/03 §extension_visit)."""

    farm_id: UUID
    plot_id: UUID | None = None
    technician_id: UUID
    visited_on: date
    topics: list[str]
    recommendations: str | None = None
    commitments: str | None = None
    notes: str | None = None


class LogbookEntryChangeBody(BaseModel):
    id: UUID
    entity: Literal["logbook_entry"]
    op: SyncOp
    client_updated_at: AwareDatetime
    data: LogbookEntryData


class ExtensionVisitChangeBody(BaseModel):
    id: UUID
    entity: Literal["extension_visit"]
    op: SyncOp
    client_updated_at: AwareDatetime
    data: ExtensionVisitData


_Change = Annotated[
    LogbookEntryChangeBody | ExtensionVisitChangeBody, Field(discriminator="entity")
]


class PushRequest(BaseModel):
    """D5: at most 100 changes per batch, more answers 422 for the whole
    request, before any change is applied."""

    device_id: str
    changes: Annotated[list[_Change], Field(max_length=MAX_BATCH_CHANGES)]


class PushResultView(BaseModel):
    id: UUID
    status: str
    server_version: int | None
    error: str | None = None


class PushResponse(BaseModel):
    results: list[PushResultView]


def _entry_change(body: LogbookEntryChangeBody) -> LogbookEntryChange:
    return LogbookEntryChange(
        id=body.id,
        op=body.op,
        client_updated_at=body.client_updated_at,
        plot_id=body.data.plot_id,
        crop_cycle_id=body.data.crop_cycle_id,
        # The wire string, so an unknown kind is that change's `invalid`
        # rejection instead of a 422 for the batch (docs/04 §Bitácora).
        kind=body.data.kind,
        occurred_on=body.data.occurred_on,
        quantity=body.data.quantity,
        unit=body.data.unit,
        cost_cop=body.data.cost_cop,
        yield_kg=body.data.yield_kg,
        sold_kg=body.data.sold_kg,
        sale_price_cop_per_kg=body.data.sale_price_cop_per_kg,
        labor_days=body.data.labor_days,
        irrigation_mm=body.data.irrigation_mm,
        alert_id=body.data.alert_id,
        notes=body.data.notes,
        created_offline=body.data.created_offline,
    )


def _visit_change(body: ExtensionVisitChangeBody) -> ExtensionVisitChange:
    return ExtensionVisitChange(
        id=body.id,
        op=body.op,
        client_updated_at=body.client_updated_at,
        farm_id=body.data.farm_id,
        plot_id=body.data.plot_id,
        technician_id=body.data.technician_id,
        visited_on=body.data.visited_on,
        topics=body.data.topics,
        recommendations=body.data.recommendations,
        commitments=body.data.commitments,
        notes=body.data.notes,
    )


def _view(result: PushResult) -> PushResultView:
    return PushResultView(
        id=result.id,
        status=result.status.value,
        server_version=result.server_version,
        error=None if result.error is None else result.error.value,
    )


@router.post("/sync/push", response_model=PushResponse)
async def push_changes(
    body: PushRequest,
    user_id: CurrentUserId,
    session: SessionDep,
    tx: SyncTransactionDep,
    ids: SyncIdProbeDep,
    entries: EntrySyncRepoDep,
    visits: VisitSyncRepoDep,
    memberships: MembershipRepoDep,
    plots: PlotRepoDep,
    farms: FarmRepoDep,
    cycles: CropCycleRepoDep,
    alerts: AlertRepoDep,
) -> PushResponse:
    """Apply a batch of offline changes, one result per change."""
    now = datetime.now(UTC)
    results: list[PushResult] = []
    for change in body.changes:
        if isinstance(change, LogbookEntryChangeBody):
            results.append(
                await push_logbook_entry(
                    _entry_change(change),
                    caller_id=user_id,
                    now=now,
                    tx=tx,
                    ids=ids,
                    entries=entries,
                    memberships=memberships,
                    plots=plots,
                    cycles=cycles,
                    alerts=alerts,
                )
            )
        else:
            results.append(
                await push_extension_visit(
                    _visit_change(change),
                    caller_id=user_id,
                    now=now,
                    tx=tx,
                    ids=ids,
                    visits=visits,
                    memberships=memberships,
                    farms=farms,
                    plots=plots,
                )
            )
    # One commit for the batch: the D1 lock is held until it ends, so the
    # versions allocated above are allocated in commit order (docs/06 §7).
    await session.commit()
    return PushResponse(results=[_view(result) for result in results])


class LogbookEntryPullData(BaseModel):
    """`data` of a `logbook_entry` pull change. Amounts are floats so they serialize
    as JSON numbers, matching web types (docs/03 §logbook_entry).
    """

    id: UUID
    org_id: UUID
    plot_id: UUID
    crop_cycle_id: UUID | None = None
    kind: str
    occurred_on: date
    quantity: float | None = None
    unit: str | None = None
    cost_cop: float | None = None
    yield_kg: float | None = None
    sold_kg: float | None = None
    sale_price_cop_per_kg: float | None = None
    labor_days: float | None = None
    irrigation_mm: float | None = None
    alert_id: UUID | None = None
    notes: str | None = None
    created_by: UUID | None = None
    created_offline: bool
    client_updated_at: AwareDatetime
    deleted_at: AwareDatetime | None = None


class ExtensionVisitPullData(BaseModel):
    """`data` of an `extension_visit` pull change (docs/03 §extension_visit)."""

    id: UUID
    org_id: UUID
    farm_id: UUID
    plot_id: UUID | None = None
    technician_id: UUID
    visited_on: date
    topics: list[str]
    recommendations: str | None = None
    commitments: str | None = None
    notes: str | None = None
    client_updated_at: AwareDatetime
    deleted_at: AwareDatetime | None = None


class LogbookEntryPullChange(BaseModel):
    id: UUID
    entity: Literal["logbook_entry"]
    op: SyncOp
    server_version: int
    data: LogbookEntryPullData


class ExtensionVisitPullChange(BaseModel):
    id: UUID
    entity: Literal["extension_visit"]
    op: SyncOp
    server_version: int
    data: ExtensionVisitPullData


_PullChange = Annotated[
    LogbookEntryPullChange | ExtensionVisitPullChange,
    Field(discriminator="entity"),
]


class PullResponse(BaseModel):
    changes: list[_PullChange]
    next_since: int
    has_more: bool


def _float(val: Decimal | None) -> float | None:
    return float(val) if val is not None else None


@router.get("/sync/pull", response_model=PullResponse)
async def pull(
    session: SessionDep,
    user_id: CurrentUserId,
    entries: EntrySyncRepoDep,
    visits: VisitSyncRepoDep,
    memberships: MembershipRepoDep,
    since: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
) -> PullResponse:
    """`GET /sync/pull?since=<server_version>&limit=500` (docs/04 §Bitácora; D1, D2).

    Runs in a single REPEATABLE READ (read-only) transaction so that entries and visits
    see one snapshot, preventing cursor gaps (D1; RNF-01).
    """
    if session.in_transaction():
        raise ProblemError(
            status=500,
            title="Session already in transaction",
            detail=(
                "GET /sync/pull requires a fresh session to configure REPEATABLE READ isolation."
            ),
        )
    await session.connection(
        execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True}
    )
    page = await pull_changes(
        caller_id=user_id,
        since=since,
        limit=limit,
        memberships=memberships,
        entries=entries,
        visits=visits,
    )
    change_views: list[_PullChange] = []
    for item in page.changes:
        if item.entity is SyncEntity.LOGBOOK_ENTRY:
            entry = item.data
            change_views.append(
                LogbookEntryPullChange(
                    id=item.id,
                    entity="logbook_entry",
                    op=item.op,
                    server_version=item.server_version,
                    data=LogbookEntryPullData(
                        id=entry.id,
                        org_id=entry.org_id,
                        plot_id=entry.plot_id,
                        crop_cycle_id=entry.crop_cycle_id,
                        kind=entry.kind,
                        occurred_on=entry.occurred_on,
                        quantity=_float(entry.quantity),
                        unit=entry.unit,
                        cost_cop=_float(entry.cost_cop),
                        yield_kg=_float(entry.yield_kg),
                        sold_kg=_float(entry.sold_kg),
                        sale_price_cop_per_kg=_float(entry.sale_price_cop_per_kg),
                        labor_days=_float(entry.labor_days),
                        irrigation_mm=_float(entry.irrigation_mm),
                        alert_id=entry.alert_id,
                        notes=entry.notes,
                        created_by=entry.created_by,
                        created_offline=entry.created_offline,
                        client_updated_at=entry.client_updated_at,
                        deleted_at=entry.deleted_at,
                    ),
                )
            )
        elif item.entity is SyncEntity.EXTENSION_VISIT:
            visit = item.data
            change_views.append(
                ExtensionVisitPullChange(
                    id=item.id,
                    entity="extension_visit",
                    op=item.op,
                    server_version=item.server_version,
                    data=ExtensionVisitPullData(
                        id=visit.id,
                        org_id=visit.org_id,
                        farm_id=visit.farm_id,
                        plot_id=visit.plot_id,
                        technician_id=visit.technician_id,
                        visited_on=visit.visited_on,
                        topics=visit.topics,
                        recommendations=visit.recommendations,
                        commitments=visit.commitments,
                        notes=visit.notes,
                        client_updated_at=visit.client_updated_at,
                        deleted_at=visit.deleted_at,
                    ),
                )
            )
        else:
            raise ValueError(f"Unknown pull change entity: {item.entity}")
    return PullResponse(
        changes=change_views,
        next_since=page.next_since,
        has_more=page.has_more,
    )
