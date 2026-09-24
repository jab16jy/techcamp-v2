"""Crop cycle write use cases (docs/04-api.md:56; docs/00-glosario.md: a
plot holds at most one active cycle). Access is resolved through the plot,
same rule as soil (T4/T5, odd/tasks/techcamp-v2-e3-farms.md): owner and
technician write, producer and viewer read only; a plot or cycle outside the
caller's orgs is `*NotFoundError` (404).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any
from uuid import UUID

from techcamp.farms.application.manage_plots import resolve_plot_access
from techcamp.farms.application.ports import (
    CropCycleRepository,
    CropRepository,
    PlotRepository,
)
from techcamp.farms.domain.errors import (
    ActiveCropCycleExistsError,
    CropCycleNotFoundError,
    CropNotFoundError,
)
from techcamp.farms.domain.models import (
    CropCycle,
    CropCycleStatus,
    compute_expected_harvest_on,
    ensure_can_write,
    ensure_harvest_not_before_sowing,
    ensure_valid_cycle_status_transition,
)
from techcamp.identity.application.ports import MembershipRepository
from techcamp.identity.domain.models import Role
from techcamp.shared.ids import uuid7


async def create_cycle(
    *,
    user_id: UUID,
    plot_id: UUID,
    crop_id: int,
    sown_on: date,
    plots: PlotRepository,
    crops: CropRepository,
    cycles: CropCycleRepository,
    memberships: MembershipRepository,
) -> CropCycle:
    plot, role = await resolve_plot_access(
        user_id=user_id, plot_id=plot_id, plots=plots, memberships=memberships
    )
    ensure_can_write(role)
    crop = await crops.get(crop_id)
    if crop is None:
        raise CropNotFoundError(crop_id)
    if await cycles.get_active_for_plot(plot.id) is not None:
        # Application-layer precheck backing the DB partial unique index
        # (`uq_crop_cycle_active_per_plot`, T6 decision): avoids a raw
        # `IntegrityError` reaching the API, at the cost of a small,
        # accepted race window between two concurrent creates on the same
        # plot (ponytail: no existing pattern in this module for turning a
        # constraint violation into a domain error at the adapter layer).
        raise ActiveCropCycleExistsError(plot.id)
    return await cycles.create(
        cycle_id=uuid7(),
        plot_id=plot.id,
        crop_id=crop.id,
        sown_on=sown_on,
        expected_harvest_on=compute_expected_harvest_on(sown_on, crop),
        status=CropCycleStatus.ACTIVE,
    )


async def resolve_cycle_access(
    *,
    user_id: UUID,
    cycle_id: UUID,
    cycles: CropCycleRepository,
    plots: PlotRepository,
    memberships: MembershipRepository,
) -> tuple[CropCycle, Role]:
    """The cycle and the caller's role in its plot's org, or
    `CropCycleNotFoundError` (see `manage_farms.resolve_farm_access` for why
    this checks every org the caller belongs to)."""
    roles_by_org = {m.org_id: m.role for m in await memberships.list_for_user(user_id)}
    cycle = await cycles.get_for_orgs(cycle_id, list(roles_by_org))
    if cycle is None:
        raise CropCycleNotFoundError(cycle_id)
    plot = await plots.get_for_orgs(cycle.plot_id, list(roles_by_org))
    assert plot is not None  # the org-scoped join in get_for_orgs already proved this
    return cycle, roles_by_org[plot.org_id]


async def update_cycle(
    *,
    user_id: UUID,
    cycle_id: UUID,
    changes: dict[str, Any],
    cycles: CropCycleRepository,
    plots: PlotRepository,
    memberships: MembershipRepository,
) -> CropCycle:
    cycle, role = await resolve_cycle_access(
        user_id=user_id, cycle_id=cycle_id, cycles=cycles, plots=plots, memberships=memberships
    )
    ensure_can_write(role)
    if "status" in changes:
        ensure_valid_cycle_status_transition(cycle.status, changes["status"])
    merged = replace(cycle, **changes)
    if "expected_harvest_on" in changes:
        ensure_harvest_not_before_sowing(merged.sown_on, merged.expected_harvest_on)
    return await cycles.update(
        cycle_id, status=merged.status, expected_harvest_on=merged.expected_harvest_on
    )
