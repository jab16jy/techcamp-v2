"""Read-side use cases for the metrics screens (E11 T7).

`GET /plots/{plot_id}/metrics` answers the adoption month the job stored,
`GET /plots/{plot_id}/cycles/{crop_cycle_id}/summary` answers the impact a
finished cycle stored or computes an active cycle's on read (D-T0.8), and
`GET /organizations/{org_id}/metrics` answers the organization's indicators for
one month (docs/04-api.md:236; D-T0.12, D-T7.2).

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

from collections.abc import Sequence
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
from techcamp.metrics.application.ports import (
    MetricsSourceRepository,
    NodeFirstReading,
    OrgMonthCycle,
)
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
    """`ciclos con cosecha registrada / ciclos terminados` over the cycles closed
    inside `month` (docs/11-metricas.md:72, D-T7.2).

    The denominator is the closed **and registered** cycles: an `active` cycle is
    in neither side, and a cycle closed with nothing written in the logbook
    belongs to no month, so a month with no closed cycle is `None` rather than
    `0` — "nothing was harvested" and "nothing was registered" are different
    facts and only the first would be a zero.
    """
    median_hours_to_first_reading: Decimal | None
    """Hours between a node's claim and its first valid reading, as the median of
    the per-node values of the nodes **claimed in `month`**
    (docs/11-metricas.md:73-75, D-T7.2).

    The node is the unit, so this is the median of the raw per-node values and not
    of per-plot medians. A node claimed in the month that has not answered yet
    contributes no value, which is not a zero; a month with no answering node is
    `None`.
    """


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


def _mean_index(rows: Sequence[PlotMonthlyMetric]) -> Decimal | None:
    """docs/11-metricas.md:75 averages over the plots **with** an index, so a
    plot that had no evidence that month leaves the average instead of entering it
    as a zero — a zero would say it adopted nothing."""
    with_index = [
        row.digital_adoption_index for row in rows if row.digital_adoption_index is not None
    ]
    if not with_index:
        return None
    return sum(with_index, start=Decimal(0)) / Decimal(len(with_index))


def _plots_with_index(rows: Sequence[PlotMonthlyMetric]) -> int | None:
    """How many plots reported an index, and `None` when the month reported no
    plot at all.

    `0` and `None` are different here: `0` says the job stored rows and none of
    them carried an index, while `None` says there is no stored month to count
    (D-T0.12). Only the stored-month listing can tell them apart.
    """
    if not rows:
        return None
    return sum(1 for row in rows if row.digital_adoption_index is not None)


def _monitored_ratio(rows: Sequence[PlotMonthlyMetric]) -> Decimal | None:
    """A non-null `monitoring` component is exactly "this plot had a node claimed
    inside the month" (D-T0.3). The denominator is the plots the month reported
    on, which is the population the listing enumerates."""
    if not rows:
        return None
    monitored = sum(1 for row in rows if row.components.monitoring is not None)
    return Decimal(monitored) / Decimal(len(rows))


def _harvested_ratio(cycles: Sequence[OrgMonthCycle]) -> Decimal | None:
    """`ciclos con cosecha registrada / ciclos terminados` (docs/11-metricas.md:72,
    D-T7.2).

    Every row is a cycle that closed inside the month, so the count of rows is the
    denominator docs/11 names and no status filter is repeated here. An empty
    sequence is `None`: a month with no closed cycle has no denominator, and `0`
    would report that the organization closed cycles and harvested none of them.
    """
    if not cycles:
        return None
    harvested = sum(1 for cycle in cycles if cycle.has_harvest)
    return Decimal(harvested) / Decimal(len(cycles))


def _hours_to_first_reading(node: NodeFirstReading) -> Decimal | None:
    """The hours between the node's alta and its first valid reading
    (docs/11-metricas.md:73), or `None` when it has none yet.

    The seconds come off the `timedelta` rather than `total_seconds()` because
    that returns a float, and a latency figure the caller may compare against a
    half hour should not arrive as `0.30000000000000004`. `reading.time` is
    second-resolution, so the microseconds a `timedelta` may carry are noise.
    """
    if node.first_reading_at is None:
        return None
    delta = node.first_reading_at - node.claimed_at
    return Decimal(delta.days * 86_400 + delta.seconds) / Decimal(3600)


def _median_hours_to_first_reading(nodes: Sequence[NodeFirstReading]) -> Decimal | None:
    """The median of the per-node hour values of the nodes claimed in the month
    (docs/11-metricas.md:73-75, D-T7.2).

    The node is the unit docs/11 names ("toma los nodos reclamados en ese mes"),
    so the median is taken over the raw per-node values — a median of per-plot
    medians is a different statistic, and a wrong one: it would weight a plot with
    three nodes as heavily as a plot with one. An even count averages the two
    middle values, the usual median.

    A node with no valid reading yet contributes no value: it is missing evidence,
    not an instant of zero. A month where no claimed node has answered is `None`
    (docs/03-modelo-datos.md:441).
    """
    hours = sorted(
        value for value in (_hours_to_first_reading(node) for node in nodes) if value is not None
    )
    if not hours:
        return None
    middle = len(hours) // 2
    if len(hours) % 2 == 1:
        return hours[middle]
    return (hours[middle - 1] + hours[middle]) / 2


async def get_org_metrics(
    *,
    user_id: UUID,
    org_id: UUID,
    month: date,
    metrics: OrgMetricsRepository,
    sources: MetricsSourceRepository,
    memberships: MembershipRepository,
) -> OrgMetrics:
    """The organization's indicators for one month, as `docs/04-api.md:236`
    promises them.

    Every figure is computed on read (D-T0.12, D-T7.2), and they do not all come
    from the same place: the mean, the plot count and the monitored ratio are read
    from the month the job stored, while the harvested-cycle ratio and the
    enrollment median are read from the views over the logbook and the telemetry
    tables. That is why each figure keeps its own evidence: a month the monthly
    job has not stored yet still answers the two that do not depend on it, and a
    month with no cycles or no answering node leaves only those two `None`.

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
    cycles = await sources.org_month_cycles(org_id, month=month)
    nodes = await sources.node_first_readings(org_id, month=month)
    return OrgMetrics(
        org_id=org_id,
        month=month,
        mean_digital_adoption_index=_mean_index(rows),
        plots_with_index=_plots_with_index(rows),
        monitored_plots_ratio=_monitored_ratio(rows),
        harvested_cycles_ratio=_harvested_ratio(cycles),
        median_hours_to_first_reading=_median_hours_to_first_reading(nodes),
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
