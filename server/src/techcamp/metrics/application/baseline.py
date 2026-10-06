"""Enrollment-survey use cases (docs/04-api.md:51-52, 233; D-T0.10, D-T0.11).

`GET /plots/{plot_id}/baseline` answers any member, `404` when the plot has no
survey; `PUT` saves or replaces it whole for an owner or technician (`403`
otherwise), recording the caller as `recorded_by`.

Plot access is resolved through the `farms` facade (`resolve_plot_access`), the
same path `irrigation` and `home` use (docs/05: a module imports only another
module's `application` package), so a foreign plot is `PlotNotFoundError` and the
caller cannot tell it apart from a missing one (docs/09-cuellos-de-botella.md
#seguridad).

The port lives here, not in `metrics/application/ports.py`, because T3 owns the
metrics source repository that goes in that file (E11 lane split).
"""

from __future__ import annotations

from datetime import date
from typing import Protocol
from uuid import UUID

from techcamp.farms.application.manage_plots import resolve_plot_access
from techcamp.farms.application.ports import CropRepository, PlotRepository
from techcamp.identity.application.ports import MembershipRepository
from techcamp.metrics.domain.errors import PlotBaselineNotFoundError, UnknownCropError
from techcamp.metrics.domain.models import (
    IrrigationPractice,
    PlotBaseline,
    ensure_can_edit_baseline,
)


class BaselineRepository(Protocol):
    """The survey store. One row per plot, so `plot_id` is the key: a `PUT` is a
    full-document write (docs/04-api.md:52), not a `PATCH`."""

    async def get_for_org(self, plot_id: UUID, org_id: UUID) -> PlotBaseline | None: ...

    """The plot's survey, or `None` when it has none. `org_id` is part of the
    filter, never assumed from the caller
    (docs/09-cuellos-de-botella.md#seguridad)."""

    async def put(self, baseline: PlotBaseline) -> PlotBaseline: ...

    """Insert or replace the plot's survey and return the stored row."""


async def get_plot_baseline(
    *,
    user_id: UUID,
    plot_id: UUID,
    plots: PlotRepository,
    baselines: BaselineRepository,
    memberships: MembershipRepository,
) -> PlotBaseline:
    """The plot's enrollment survey, or `PlotBaselineNotFoundError` when it has
    none (docs/04-api.md:233)."""
    plot, _role = await resolve_plot_access(
        user_id=user_id, plot_id=plot_id, plots=plots, memberships=memberships
    )
    baseline = await baselines.get_for_org(plot.id, plot.org_id)
    if baseline is None:
        raise PlotBaselineNotFoundError(plot.id)
    return baseline


async def put_plot_baseline(
    *,
    user_id: UUID,
    plot_id: UUID,
    enrolled_on: date,
    crop_id: int,
    last_yield_kg_ha: float,
    last_cost_cop_ha: float | None,
    irrigation_practice: IrrigationPractice,
    plots: PlotRepository,
    crops: CropRepository,
    baselines: BaselineRepository,
    memberships: MembershipRepository,
) -> PlotBaseline:
    """Create or replace the plot's survey (D-T0.11).

    Two writes to the same plot are allowed even after crop cycles exist: the
    survey is the farmer's own account of the last cycle and impact is measured
    against it, so correcting it stays editable (D-T0.11).

    `crop_id` must name a crop of the catalog (`UnknownCropError`, `422`): the
    survey compares against a cycle of that crop. `recorded_by` is the caller.
    """
    plot, role = await resolve_plot_access(
        user_id=user_id, plot_id=plot_id, plots=plots, memberships=memberships
    )
    ensure_can_edit_baseline(role)
    if await crops.get(crop_id) is None:
        raise UnknownCropError(crop_id)
    return await baselines.put(
        PlotBaseline(
            plot_id=plot.id,
            org_id=plot.org_id,
            enrolled_on=enrolled_on,
            crop_id=crop_id,
            last_yield_kg_ha=last_yield_kg_ha,
            last_cost_cop_ha=last_cost_cop_ha,
            irrigation_practice=irrigation_practice,
            recorded_by=user_id,
        )
    )
