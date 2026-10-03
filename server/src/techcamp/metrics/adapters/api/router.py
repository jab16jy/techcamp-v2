"""The enrollment-survey endpoints (docs/04-api.md:51-52, 233; D-T0.10, D-T0.11).

`GET` answers any member and `404`s a plot with no survey; `PUT` creates or
replaces it for an owner or technician (`403` otherwise), with `crop_id`
validated against the catalog (`422`).

Pydantic stays at this boundary (AGENTS.md): the closed `irrigation_practice`
vocabulary and the non-negative figures are request validation here, while the
database `CHECK`s of T1's migration are the second line of defense.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field

from techcamp.farms.adapters.api.deps import CropRepoDep, PlotRepoDep
from techcamp.farms.domain.errors import PlotNotFoundError
from techcamp.identity.adapters.api.deps import CurrentUserId, MembershipRepoDep
from techcamp.metrics.adapters.api.deps import BaselineRepoDep
from techcamp.metrics.application.baseline import get_plot_baseline, put_plot_baseline
from techcamp.metrics.domain.errors import (
    InsufficientRoleError,
    PlotBaselineNotFoundError,
    UnknownCropError,
)
from techcamp.metrics.domain.models import IrrigationPractice, PlotBaseline
from techcamp.shared.errors import ProblemError

router = APIRouter(tags=["metrics"])


class PlotBaselineInput(BaseModel):
    """The survey body of `PUT /plots/{plot_id}/baseline` (docs/04-api.md:52).

    `last_cost_cop_ha` is optional because the figure is approximate and a
    farmer often does not know it; omitting it stores `null`, which is missing
    evidence and not a free plot (docs/03-modelo-datos.md:426-430).
    """

    enrolled_on: date
    crop_id: int
    last_yield_kg_ha: float = Field(ge=0, allow_inf_nan=False)
    last_cost_cop_ha: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    irrigation_practice: IrrigationPractice
    """`allow_inf_nan=False` closes a hole `ge=0` leaves open: `inf` and `nan`
    both satisfy a lower bound, and JSON carries both literals, so without it an
    untrusted client reaches the `Numeric` column with a figure no frozen decimal
    can hold and the answer is a database error (500) instead of the 422 every
    other invalid figure gets (#244, R3-RELIABILITY-003)."""


class PlotBaselineView(BaseModel):
    """`PlotBaseline` on the wire.

    Named for the module, not the table: `farms` already declares a `PlotView`
    and two routers sharing a response-model name make FastAPI qualify both in
    the OpenAPI components (`tests/test_openapi_schema_names.py`).
    """

    plot_id: UUID
    org_id: UUID
    enrolled_on: date
    crop_id: int
    last_yield_kg_ha: float
    last_cost_cop_ha: float | None
    irrigation_practice: str
    recorded_by: UUID


def _baseline_view(baseline: PlotBaseline) -> PlotBaselineView:
    return PlotBaselineView(
        plot_id=baseline.plot_id,
        org_id=baseline.org_id,
        enrolled_on=baseline.enrolled_on,
        crop_id=baseline.crop_id,
        last_yield_kg_ha=baseline.last_yield_kg_ha,
        last_cost_cop_ha=baseline.last_cost_cop_ha,
        irrigation_practice=baseline.irrigation_practice.value,
        recorded_by=baseline.recorded_by,
    )


@router.get("/plots/{plot_id}/baseline", response_model=PlotBaselineView)
async def get_baseline(
    plot_id: UUID,
    user_id: CurrentUserId,
    plots: PlotRepoDep,
    baselines: BaselineRepoDep,
    memberships: MembershipRepoDep,
) -> PlotBaselineView:
    """Any member reads the survey of a plot they can see
    (docs/04-api.md:233)."""
    try:
        baseline = await get_plot_baseline(
            user_id=user_id,
            plot_id=plot_id,
            plots=plots,
            baselines=baselines,
            memberships=memberships,
        )
    except PlotNotFoundError as exc:
        raise ProblemError(status=404, title="Plot not found") from exc
    except PlotBaselineNotFoundError as exc:
        raise ProblemError(status=404, title="Plot has no enrollment survey") from exc
    return _baseline_view(baseline)


@router.put("/plots/{plot_id}/baseline", response_model=PlotBaselineView)
async def put_baseline(
    plot_id: UUID,
    payload: PlotBaselineInput,
    user_id: CurrentUserId,
    plots: PlotRepoDep,
    crops: CropRepoDep,
    baselines: BaselineRepoDep,
    memberships: MembershipRepoDep,
) -> PlotBaselineView:
    """Save or replace the survey; the caller becomes its `recorded_by`
    (docs/04-api.md:233; D-T0.11)."""
    try:
        baseline = await put_plot_baseline(
            user_id=user_id,
            plot_id=plot_id,
            enrolled_on=payload.enrolled_on,
            crop_id=payload.crop_id,
            last_yield_kg_ha=payload.last_yield_kg_ha,
            last_cost_cop_ha=payload.last_cost_cop_ha,
            irrigation_practice=payload.irrigation_practice,
            plots=plots,
            crops=crops,
            baselines=baselines,
            memberships=memberships,
        )
    except PlotNotFoundError as exc:
        raise ProblemError(status=404, title="Plot not found") from exc
    except InsufficientRoleError as exc:
        raise ProblemError(status=403, title="Role cannot save this enrollment survey") from exc
    except UnknownCropError as exc:
        raise ProblemError(status=422, title="crop_id is not a valid crop") from exc
    return _baseline_view(baseline)
