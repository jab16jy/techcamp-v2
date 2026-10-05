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
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from techcamp.farms.adapters.api.deps import CropCycleRepoDep, CropRepoDep, PlotRepoDep
from techcamp.farms.domain.errors import CropCycleNotFoundError, PlotNotFoundError
from techcamp.identity.adapters.api.deps import CurrentUserId, MembershipRepoDep
from techcamp.identity.domain.errors import NotAMemberError
from techcamp.metrics.adapters.api.deps import (
    BaselineRepoDep,
    CycleSummaryRepoDep,
    MetricsSourceRepoDep,
    MonthlyMetricRepoDep,
)
from techcamp.metrics.application.baseline import get_plot_baseline, put_plot_baseline
from techcamp.metrics.application.metrics_read import (
    CycleSummaryRead,
    OrgMetrics,
    OrgMetricsForbiddenError,
    get_plot_month,
)
from techcamp.metrics.application.metrics_read import get_cycle_summary as read_cycle_summary
from techcamp.metrics.application.metrics_read import get_org_metrics as read_org_metrics
from techcamp.metrics.domain.adoption import PlotMonthlyMetric
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


class CropCycleSummaryView(BaseModel):
    """`crop_cycle_summary` on the wire plus the cycle's own status
    (docs/04-api.md:235, docs/03-modelo-datos.md:439).

    `cycle_status` is not a column of the summary: it lives on `crop_cycle`, and
    it is what tells a reader whether these figures were stored (a finished
    cycle) or computed for the moment (D-T0.8).
    """

    crop_cycle_id: UUID
    plot_id: UUID
    org_id: UUID
    cycle_status: str
    yield_kg_ha: float | None
    yield_change_vs_baseline: float | None
    relative_yield: float | None
    water_applied_m3_ha: float | None
    irrigation_wue_kg_m3: float | None
    water_stress_days: int | None
    cost_cop_ha: float | None
    cost_cop_kg: float | None
    yield_kg_per_labor_day: float | None
    gross_margin_cop: float | None
    loss_kg: float | None
    loss_cop: float | None
    computed_at: datetime


def _as_float(value: Decimal | None) -> float | None:
    """`Decimal` to `float` for the wire, never rounding on the way.

    The domain and the `Numeric` columns keep `Decimal`; a JSON figure is a
    double, and the conversion is exact enough for a ratio or a COP amount while
    leaving the stored value untouched (the adapters read back what the
    database holds, same as T4's and T5's stores).
    """
    return None if value is None else float(value)


def _month_view(metric: PlotMonthlyMetric) -> PlotMetricMonthlyView:
    components = metric.components
    return PlotMetricMonthlyView(
        plot_id=metric.plot_id,
        month=metric.month,
        monitoring=_as_float(components.monitoring),
        record_keeping=_as_float(components.record_keeping),
        decision=_as_float(components.decision),
        risk_management=_as_float(components.risk_management),
        digital_adoption_index=_as_float(metric.digital_adoption_index),
        computed_at=metric.computed_at,
    )


class OrgMetricsView(BaseModel):
    """The organization's indicators for one month
    (docs/04-api.md:236, docs/11-metricas.md:69-75; D-T0.12, D-T7.1).

    Named for the module, like `PlotMetricMonthlyView`: `OrgMetrics` is the
    application value and a response model sharing that name would collide in
    the OpenAPI components (`tests/test_openapi_schema_names.py`).

    `harvested_cycles_ratio` and `median_hours_to_first_reading` are always `null`
    in this lane (D-T7.1): the org-month listing carries neither cycles nor node
    instants, and `0` would report "no cycle was harvested" and "every node
    answered instantly" instead.
    """

    org_id: UUID
    month: date
    mean_digital_adoption_index: float | None
    plots_with_index: int | None
    monitored_plots_ratio: float | None
    harvested_cycles_ratio: float | None
    median_hours_to_first_reading: float | None


def _org_metrics_view(metrics: OrgMetrics) -> OrgMetricsView:
    return OrgMetricsView(
        org_id=metrics.org_id,
        month=metrics.month,
        mean_digital_adoption_index=_as_float(metrics.mean_digital_adoption_index),
        plots_with_index=metrics.plots_with_index,
        monitored_plots_ratio=_as_float(metrics.monitored_plots_ratio),
        harvested_cycles_ratio=_as_float(metrics.harvested_cycles_ratio),
        median_hours_to_first_reading=_as_float(metrics.median_hours_to_first_reading),
    )


def _summary_view(read: CycleSummaryRead) -> CropCycleSummaryView:
    summary = read.summary
    return CropCycleSummaryView(
        crop_cycle_id=summary.crop_cycle_id,
        plot_id=summary.plot_id,
        org_id=summary.org_id,
        cycle_status=read.cycle_status.value,
        yield_kg_ha=_as_float(summary.yield_kg_ha),
        yield_change_vs_baseline=_as_float(summary.yield_change_vs_baseline),
        relative_yield=_as_float(summary.relative_yield),
        water_applied_m3_ha=_as_float(summary.water_applied_m3_ha),
        irrigation_wue_kg_m3=_as_float(summary.irrigation_wue_kg_m3),
        water_stress_days=summary.water_stress_days,
        cost_cop_ha=_as_float(summary.cost_cop_ha),
        cost_cop_kg=_as_float(summary.cost_cop_kg),
        yield_kg_per_labor_day=_as_float(summary.yield_kg_per_labor_day),
        gross_margin_cop=_as_float(summary.gross_margin_cop),
        loss_kg=_as_float(summary.loss_kg),
        loss_cop=_as_float(summary.loss_cop),
        computed_at=summary.computed_at,
    )


def _month_start(month: str) -> date:
    """`YYYY-MM` to the first day of that month, the key the row is stored under.

    The query pattern already rejected anything that is not a real month, so this
    parse cannot fail; `date(..., 1)` is what makes `(plot_id, month)` the upsert
    key (D-T0.2) and what `month_bounds` reads back.
    """
    return date(int(month[:4]), int(month[5:7]), 1)


@router.get("/plots/{plot_id}/metrics", response_model=PlotMetricMonthlyView)
async def get_plot_metrics(
    month: Annotated[str, Query(pattern=_MONTH_QUERY, description="Month as YYYY-MM")],
    plot_id: UUID,
    user_id: CurrentUserId,
    plots: PlotRepoDep,
    metrics: MonthlyMetricRepoDep,
    memberships: MembershipRepoDep,
) -> PlotMetricMonthlyView:
    """The plot's adoption index for one month, as the job stored it.

    Any member reads it (D-T0.10), a month with no stored row is `404`, and a
    plot in another organization is the same `404`
    (docs/04-api.md:234, 237; docs/09-cuellos-de-botella.md#seguridad).
    """
    try:
        metric = await get_plot_month(
            user_id=user_id,
            plot_id=plot_id,
            month=_month_start(month),
            plots=plots,
            metrics=metrics,
            memberships=memberships,
        )
    except PlotNotFoundError as exc:
        raise ProblemError(status=404, title="Plot not found") from exc
    if metric is None:
        raise ProblemError(status=404, title="Plot has no metrics for that month")
    return _month_view(metric)


@router.get("/organizations/{org_id}/metrics", response_model=OrgMetricsView)
async def get_organization_metrics(
    month: Annotated[str, Query(pattern=_MONTH_QUERY, description="Month as YYYY-MM")],
    org_id: UUID,
    user_id: CurrentUserId,
    metrics: MonthlyMetricRepoDep,
    memberships: MembershipRepoDep,
) -> OrgMetricsView:
    """The organization's indicators for one month, computed on read
    (docs/04-api.md:236; D-T0.12, D-T0.10).

    Owner or technician (`403` for any other member); a caller who is not a
    member of the organization is `404`, so the endpoint never reveals that it
    exists.

    A month with no stored report is `200` with every figure `null`, not `404`:
    the organization exists and the month simply has nothing to say yet
    (docs/03-modelo-datos.md:441). `harvested_cycles_ratio` and
    `median_hours_to_first_reading` are `null` in every answer of this lane
    (D-T7.1) and unlock in the follow-up lane that adds their views.
    """
    try:
        org_metrics = await read_org_metrics(
            user_id=user_id,
            org_id=org_id,
            month=_month_start(month),
            metrics=metrics,
            memberships=memberships,
        )
    except NotAMemberError as exc:
        raise ProblemError(status=404, title="Organization not found") from exc
    except OrgMetricsForbiddenError as exc:
        raise ProblemError(
            status=403, title="Role cannot read the organization's indicators"
        ) from exc
    return _org_metrics_view(org_metrics)


@router.get("/plots/{plot_id}/cycles/{crop_cycle_id}/summary", response_model=CropCycleSummaryView)
async def get_cycle_summary(
    plot_id: UUID,
    crop_cycle_id: UUID,
    user_id: CurrentUserId,
    plots: PlotRepoDep,
    cycles: CropCycleRepoDep,
    baselines: BaselineRepoDep,
    sources: MetricsSourceRepoDep,
    summaries: CycleSummaryRepoDep,
    memberships: MembershipRepoDep,
) -> CropCycleSummaryView:
    """One cycle's impact: the stored row of a finished cycle, the same figures
    computed on read for an active one (D-T0.8).

    A cycle of another plot or another organization is `404`, and a finished
    cycle with no stored row yet is `404` too — a read never summarizes
    (docs/04-api.md:235).
    """
    try:
        read = await read_cycle_summary(
            user_id=user_id,
            plot_id=plot_id,
            crop_cycle_id=crop_cycle_id,
            cycles=cycles,
            plots=plots,
            baselines=baselines,
            sources=sources,
            summaries=summaries,
            memberships=memberships,
        )
    except PlotNotFoundError as exc:
        raise ProblemError(status=404, title="Plot not found") from exc
    except CropCycleNotFoundError as exc:
        raise ProblemError(status=404, title="Crop cycle not found") from exc
    if read is None:
        raise ProblemError(status=404, title="Cycle has no stored summary yet")
    return _summary_view(read)
