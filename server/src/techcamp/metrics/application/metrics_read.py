"""Read-side use cases for the metrics dashboard (E11 T7).

`GET /plots/{plot_id}/metrics` answers the adoption month the job stored, and
`GET /plots/{plot_id}/cycles/{crop_cycle_id}/summary` answers the impact a
finished cycle stored or computes an active cycle's on read (D-T0.8).

Both resolve the plot through `resolve_plot_access`, so a plot outside the
caller's organizations is not found for any role
(docs/04-api.md:237, docs/09-cuellos-de-botella.md#seguridad).

Neither use case writes. The monthly job owns every write to
`plot_metric_monthly` and `crop_cycle_summary`, so a `GET` that stored a row
would make the dashboard's figures depend on who looked at them and when
(docs/11-metricas.md:131).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from techcamp.farms.application.manage_plots import resolve_plot_access
from techcamp.farms.application.ports import CropCycleRepository, PlotRepository
from techcamp.farms.domain.errors import CropCycleNotFoundError
from techcamp.farms.domain.models import CropCycleStatus
from techcamp.identity.application.ports import MembershipRepository
from techcamp.metrics.application.adoption import MonthlyMetricRepository
from techcamp.metrics.application.baseline import BaselineRepository
from techcamp.metrics.application.cycle_summary import (
    PERSISTED_STATUSES,
    CycleSummaryRepository,
    summarize_cycle,
)
from techcamp.metrics.application.ports import MetricsSourceRepository
from techcamp.metrics.domain.adoption import PlotMonthlyMetric
from techcamp.metrics.domain.cycle_summary import CycleSummary


@dataclass(frozen=True, slots=True)
class CycleSummaryRead:
    """What the cycle-summary read answers: the impact plus the cycle's own
    status, which lives on `crop_cycle` and not on the summary row
    (docs/04-api.md:235 — the response carries the columns of
    `crop_cycle_summary` *and* `cycle_status`)."""

    summary: CycleSummary
    cycle_status: CropCycleStatus


async def get_plot_month(
    *,
    user_id: UUID,
    plot_id: UUID,
    month: date,
    plots: PlotRepository,
    metrics: MonthlyMetricRepository,
    memberships: MembershipRepository,
) -> PlotMonthlyMetric | None:
    """The plot's stored adoption month, or `None` when the job stored none.

    `month` is the first day of the calendar month in America/Bogota (D-T0.7) —
    the key the row is stored under, which is why the caller normalizes a
    `YYYY-MM` query before it arrives here.

    `None` is a missing row rather than a failed read, and the adapter answers
    `404`: a month the job never stored is a resource that does not exist yet
    (docs/04-api.md:234). A plot in another organization raises
    `PlotNotFoundError` instead, the same `404`, so the two cannot be told apart
    (docs/09-cuellos-de-botella.md#seguridad).
    """
    plot, _role = await resolve_plot_access(
        user_id=user_id, plot_id=plot_id, plots=plots, memberships=memberships
    )
    return await metrics.get(plot.org_id, plot.id, month=month)


async def get_cycle_summary(
    *,
    user_id: UUID,
    plot_id: UUID,
    crop_cycle_id: UUID,
    cycles: CropCycleRepository,
    plots: PlotRepository,
    baselines: BaselineRepository,
    sources: MetricsSourceRepository,
    summaries: CycleSummaryRepository,
    memberships: MembershipRepository,
) -> CycleSummaryRead | None:
    """One cycle's impact as the endpoint answers it, or `None` when a finished
    cycle has no stored row yet.

    Two branches, both from D-T0.8 and docs/04-api.md:235:

    - A `harvested` or `lost` cycle returns its **stored** row. `None` here means
      the job has not summarized that cycle yet, and it stays `None`: a `GET`
      recomputing it would store a row from a read, and returning a fresh
      computation instead would make the same finished cycle report two
      different figures depending on when it was asked for.
    - An `active` cycle has no stored row by design and is **computed on read**
      from the views. `summarize_cycle` stores only the persisted statuses, so
      this branch cannot write.

    The path's plot owns the cycle, and `get_for_orgs` filters the cycle through
    its own plot, so a cycle of another plot or another organization is one and
    the same `404` (`CropCycleNotFoundError`).
    """
    plot, _role = await resolve_plot_access(
        user_id=user_id, plot_id=plot_id, plots=plots, memberships=memberships
    )
    cycle = await cycles.get_for_orgs(crop_cycle_id, [plot.org_id])
    if cycle is None or cycle.plot_id != plot.id:
        raise CropCycleNotFoundError(crop_cycle_id)

    if cycle.status in PERSISTED_STATUSES:
        stored = await summaries.get_for_org(crop_cycle_id, plot.org_id)
        if stored is None:
            return None
        return CycleSummaryRead(summary=stored, cycle_status=cycle.status)

    # The cycle is resolved once more inside `summarize_cycle`, which owns the
    # evidence assembly of T4: reusing it is what keeps the active-cycle figures
    # identical to the ones the job would have stored the day the cycle closed.
    summary = await summarize_cycle(
        org_id=plot.org_id,
        crop_cycle_id=crop_cycle_id,
        cycles=cycles,
        plots=plots,
        baselines=baselines,
        sources=sources,
        summaries=summaries,
    )
    return CycleSummaryRead(summary=summary, cycle_status=cycle.status)
