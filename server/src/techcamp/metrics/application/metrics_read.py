"""Read-side use cases for the metrics screens (E11 T7).

`GET /plots/{plot_id}/metrics` answers the adoption month the job stored,
`GET /plots/{plot_id}/cycles/{crop_cycle_id}/summary` answers the impact a
finished cycle stored or computes an active cycle's on read (D-T0.8), and
`GET /organizations/{org_id}/metrics` answers the organization's indicators for
one month (docs/04-api.md:236; D-T0.12, D-T7.1).

The plot-scoped reads resolve the plot through `resolve_plot_access`, so a plot
outside the caller's organizations is not found for any role
(docs/04-api.md:237, docs/09-cuellos-de-botella.md#seguridad). The organization
read resolves membership first, so a non-member never learns that the
organization exists.

None of them writes. The monthly job owns every write to
`plot_metric_monthly` and `crop_cycle_summary`, so a `GET` that stored a row
would make the dashboard's figures depend on who looked at them and when
(docs/11-metricas.md:131).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from techcamp.farms.application.manage_plots import resolve_plot_access
from techcamp.farms.application.ports import CropCycleRepository, PlotRepository
from techcamp.farms.domain.errors import CropCycleNotFoundError
from techcamp.farms.domain.models import CropCycleStatus
from techcamp.identity.application.ports import MembershipRepository
from techcamp.identity.application.resolve_org_access import resolve_org_membership
from techcamp.identity.domain.models import Role
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

ORG_METRICS_ROLES: frozenset[Role] = frozenset({Role.OWNER, Role.TECHNICIAN})
"""Who reads the organization's indicators (D-T0.10): the two roles that also
export the extension visits and save the enrollment survey. A `producer` or a
`viewer` is a member of the organization and still sees nothing here."""


class OrgMetricsForbiddenError(Exception):
    """Raised when a role outside `ORG_METRICS_ROLES` reads the indicators.

    Its own type rather than `domain.errors.InsufficientRoleError`: that one
    names the enrollment survey, and this is a different capability — the same
    reason `logbook` carries `VisitExportForbiddenError` beside its own role
    error. The adapter maps this to `403`, while a non-member is `404`.
    """

    def __init__(self, role: Role) -> None:
        self.role = role
        super().__init__(f"Role '{role}' cannot read the organization's indicators")


class OrgMetricsRepository(Protocol):
    """The org-month listing behind `OrgMetrics` (docs/04-api.md:226; D-T0.12).

    Declared here instead of added to `MonthlyMetricRepository`, whose three
    methods are the plot-scoped reads: this one aggregates over the whole
    organization, so it belongs to this use case's own port.
    """

    async def list_for_org_month(self, org_id: UUID, *, month: date) -> list[PlotMonthlyMetric]:
        """Every plot's stored month for one organization, empty when the job
        stored no row for it (D-T0.12)."""
        ...


@dataclass(frozen=True, slots=True)
class OrgMetrics:
    """The organization's indicators for one calendar month
    (docs/04-api.md:236, docs/11-metricas.md:69-75; D-T0.12, D-T7.1).

    A figure with no evidence is `None`, never `0`: no report for the month, no
    plot reporting it, and an organization that adopted nothing are three
    different facts, and only the third is a zero.
    """

    org_id: UUID
    month: date
    mean_digital_adoption_index: Decimal | None
    plots_with_index: int | None
    monitored_plots_ratio: Decimal | None
    harvested_cycles_ratio: Decimal | None
    """Always `None` in this lane (D-T7.1).

    The org-month listing carries plot figures only, so there is no cycle to
    count here. The follow-up lane adds the org-cycle view and the
    `source_repository` method that reads it, and this figure becomes
    `ciclos con cosecha / ciclos terminados` for the asked month."""
    median_hours_to_first_reading: Decimal | None
    """Always `None` in this lane (D-T7.1).

    Hours from a node's claim to its first valid reading: no port the metrics
    module reads today carries a claim instant together with a first reading
    (`metrics_node_month_readings` holds counts). The follow-up lane adds the
    view that does."""


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


async def get_org_metrics(
    *,
    user_id: UUID,
    org_id: UUID,
    month: date,
    metrics: OrgMetricsRepository,
    memberships: MembershipRepository,
) -> OrgMetrics:
    """The organization's indicators for one month, as `docs/04-api.md:236`
    promises them.

    Computed on read over the stored month (D-T0.12), which is why this reads the
    listing instead of a summary row: these figures are about the organization,
    not about one plot.

    Membership resolves first, so a caller who is not a member is `404` and never
    learns the organization exists; a member outside `ORG_METRICS_ROLES` is `403`
    (D-T0.10).
    """
    membership = await resolve_org_membership(
        user_id=user_id, org_id=org_id, memberships=memberships
    )
    if membership.role not in ORG_METRICS_ROLES:
        raise OrgMetricsForbiddenError(membership.role)

    rows = await metrics.list_for_org_month(org_id, month=month)
    if not rows:
        # Nothing was reported for this month, so there is no population for the
        # mean and no denominator for a ratio. `None` throughout rather than `0`:
        # "no report" is not "nothing adopted" (docs/03-modelo-datos.md:441).
        return OrgMetrics(
            org_id=org_id,
            month=month,
            mean_digital_adoption_index=None,
            plots_with_index=None,
            monitored_plots_ratio=None,
            harvested_cycles_ratio=None,
            median_hours_to_first_reading=None,
        )

    with_index = [
        row.digital_adoption_index for row in rows if row.digital_adoption_index is not None
    ]
    monitored = sum(1 for row in rows if row.components.monitoring is not None)
    return OrgMetrics(
        org_id=org_id,
        month=month,
        # docs/11-metricas.md:75 averages over the plots *with* an index, so a
        # plot that had no evidence that month leaves the average instead of
        # entering it as a zero — a zero would say it adopted nothing.
        mean_digital_adoption_index=(
            None if not with_index else sum(with_index, start=Decimal(0)) / Decimal(len(with_index))
        ),
        plots_with_index=len(with_index),
        # A non-null `monitoring` component is exactly "this plot had a node
        # claimed inside the month" (D-T0.3). The denominator is the plots the
        # month reported on, which is the population the listing enumerates.
        monitored_plots_ratio=Decimal(monitored) / Decimal(len(rows)),
        harvested_cycles_ratio=None,
        median_hours_to_first_reading=None,
    )


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
