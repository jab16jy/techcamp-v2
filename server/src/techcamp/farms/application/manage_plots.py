"""Plot write use cases and org-scoped access resolution (docs/04-api.md; ADR-0023).

`POST /farms/{farm_id}/plots` and `PATCH /plots/{plot_id}`. Applies the T1
domain rules: default irrigation efficiency by system when omitted, and a
rainfed plot (`irrigation_system = none`) may not carry efficiency or flow.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import UUID

from techcamp.farms.application.manage_farms import resolve_farm_access
from techcamp.farms.application.ports import FarmRepository, PlotRepository
from techcamp.farms.domain.errors import MissingIrrigationEfficiencyError, PlotNotFoundError
from techcamp.farms.domain.models import (
    IrrigationSystem,
    Plot,
    default_efficiency_for,
    ensure_can_write,
    ensure_rainfed_has_no_irrigation,
)
from techcamp.identity.application.ports import MembershipRepository
from techcamp.identity.domain.models import Role
from techcamp.shared.ids import uuid7


async def resolve_plot_access(
    *, user_id: UUID, plot_id: UUID, plots: PlotRepository, memberships: MembershipRepository
) -> tuple[Plot, Role]:
    """The plot and the caller's role in its org, or `PlotNotFoundError`
    (see `manage_farms.resolve_farm_access` for why this checks every org
    the caller belongs to)."""
    roles_by_org = {m.org_id: m.role for m in await memberships.list_for_user(user_id)}
    plot = await plots.get_for_orgs(plot_id, list(roles_by_org))
    if plot is None:
        raise PlotNotFoundError(plot_id)
    return plot, roles_by_org[plot.org_id]


async def create_plot(
    *,
    user_id: UUID,
    farm_id: UUID,
    name: str,
    boundary_wkt: str,
    irrigation_system: IrrigationSystem,
    irrigation_efficiency: float | None,
    system_flow_lph: float | None,
    farms: FarmRepository,
    plots: PlotRepository,
    memberships: MembershipRepository,
) -> Plot:
    farm, role = await resolve_farm_access(
        user_id=user_id, farm_id=farm_id, farms=farms, memberships=memberships
    )
    ensure_can_write(role)
    if irrigation_efficiency is None and irrigation_system is not IrrigationSystem.NONE:
        irrigation_efficiency = default_efficiency_for(irrigation_system)
    ensure_rainfed_has_no_irrigation(irrigation_system, irrigation_efficiency, system_flow_lph)
    return await plots.create(
        plot_id=uuid7(),
        org_id=farm.org_id,
        farm_id=farm.id,
        name=name,
        boundary_wkt=boundary_wkt,
        irrigation_system=irrigation_system,
        irrigation_efficiency=irrigation_efficiency,
        system_flow_lph=system_flow_lph,
    )


async def update_plot(
    *,
    user_id: UUID,
    plot_id: UUID,
    changes: dict[str, Any],
    plots: PlotRepository,
    memberships: MembershipRepository,
) -> Plot:
    plot, role = await resolve_plot_access(
        user_id=user_id, plot_id=plot_id, plots=plots, memberships=memberships
    )
    ensure_can_write(role)
    merged = replace(plot, **changes)
    explicit_null_efficiency = (
        "irrigation_efficiency" in changes and changes["irrigation_efficiency"] is None
    )
    if explicit_null_efficiency and merged.irrigation_system is not IrrigationSystem.NONE:
        # An explicit `null` is a client error, not "no preference": the
        # default below applies only when the field is omitted (#21 round 4).
        raise MissingIrrigationEfficiencyError(merged.irrigation_system.value)
    system_changed_without_efficiency = (
        merged.irrigation_system is not plot.irrigation_system
        and "irrigation_efficiency" not in changes
    )
    missing_efficiency = (
        merged.irrigation_system is not IrrigationSystem.NONE
        and merged.irrigation_efficiency is None
    )
    if system_changed_without_efficiency or missing_efficiency:
        # Same T1 rule as `create_plot`: an efficiency not given follows the
        # current system's default, never the previous system's value.
        merged = replace(
            merged, irrigation_efficiency=default_efficiency_for(merged.irrigation_system)
        )
    system_changed_to_rainfed = (
        merged.irrigation_system is IrrigationSystem.NONE
        and plot.irrigation_system is not IrrigationSystem.NONE
    )
    if system_changed_to_rainfed and "system_flow_lph" not in changes:
        # ADR-0023: a rainfed plot has neither efficiency nor flow. Leftover
        # flow from the previous system must be cleared automatically, not
        # left to trip `ensure_rainfed_has_no_irrigation` below into a 422
        # (GitHub issue #21 round 4) — mirrors efficiency's own clearing via
        # `default_efficiency_for(NONE)` above.
        merged = replace(merged, system_flow_lph=None)
    ensure_rainfed_has_no_irrigation(
        merged.irrigation_system, merged.irrigation_efficiency, merged.system_flow_lph
    )
    return await plots.update(
        plot_id,
        plot.org_id,
        name=merged.name,
        boundary_wkt=merged.boundary,
        irrigation_system=merged.irrigation_system,
        irrigation_efficiency=merged.irrigation_efficiency,
        system_flow_lph=merged.system_flow_lph,
    )
