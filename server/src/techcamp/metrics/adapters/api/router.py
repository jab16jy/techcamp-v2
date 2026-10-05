"""The metrics endpoints: the enrollment survey and the reads behind the
"Indicadores de tecnificación" screen (docs/04-api.md:51-52, 224-226, 233-235;
D-T0.8, D-T0.10, D-T0.11).

The survey endpoints are `GET` any member and `404` a plot with no survey, and
`PUT` creates or replaces it for an owner or technician (`403` otherwise), with
`crop_id` validated against the catalog (`422`).

The reads answer only what was stored: `GET /plots/{id}/metrics` the adoption
month the job wrote, `GET /plots/{id}/cycles/{id}/summary` the impact a finished
cycle stored — an active cycle is computed on read and never written (D-T0.8).
Neither read writes, and a plot outside the caller's organizations is `404` for
every role (docs/04-api.md:237).

Pydantic stays at this boundary (AGENTS.md): the closed `irrigation_practice`
vocabulary and the non-negative figures are request validation here, while the
database `CHECK`s of T1's migration are the second line of defense.
"""

from __future__ import annotations

from datetime import date, datetime
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

_MONTH_QUERY = r"^\d{4}-(0[1-9]|1[0-2])$"
"""`YYYY-MM`, and a real month of the calendar: `2026-13` is a malformed query,
not a missing row, so it is `422` from the query validation rather than a `404`
from the store (docs/04-api.md:234)."""


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


class PlotMetricMonthlyView(BaseModel):
    """`plot_metric_monthly` on the wire (docs/04-api.md:234).

    The four components and the index are `null` when the plot had no evidence
    for them that month (D-T0.3): a component without a denominator is not a
    component that scored zero.
    """

    plot_id: UUID
    month: date
    """The stored bucket, the first day of the month (docs/03-modelo-datos.md:438).

    The query takes `YYYY-MM` and the row answers with the date it stores, so the
    figure the screen shows is the month the job actually computed."""
    monitoring: float | None
    record_keeping: float | None
    decision: float | None
    risk_management: float | None
    digital_adoption_index: float | None
    computed_at: datetime
