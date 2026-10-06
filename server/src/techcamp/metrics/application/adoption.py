"""The monthly adoption-index use case and the latest-index read `home` calls
(docs/11-metricas.md §2, D-T0.3 to D-T0.7; ADR-0024).

One function computes and stores a plot-month: it asks T3's read-model port for
the four sets of evidence, hands them to the pure domain math, and upserts the
result. Running it twice for the same month leaves the same row (docs/03:442,
D-T0.2), which is what lets the monthly job re-run a single plot after a
failure without a special case.

Plot access and the plot's irrigation system come from the `farms` facade, never
from a join (docs/05-arquitectura.md §Reglas, D-T0.1): a module reads another
module's `application` package. `decision` is null for a rainfed plot, and that
fact is the plot's, not the month's — hence the facade rather than inferring it
from the evidence.

The port is declared here, next to its only caller, for the same reason T2's
baseline port is: T3 owns `application/ports.py` and this lane does not write it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Protocol
from uuid import UUID

from techcamp.farms.application.ports import PlotRepository
from techcamp.farms.domain.errors import PlotNotFoundError
from techcamp.farms.domain.models import IrrigationSystem
from techcamp.metrics.application.ports import MetricsSourceRepository
from techcamp.metrics.domain.adoption import (
    AdoptionComponents,
    NodeEvidence,
    PlotMonthlyMetric,
    RecommendationDay,
    decision_component,
    digital_adoption_index,
    monitoring_component,
    month_bounds,
    record_keeping_component,
    risk_management_component,
)


class MonthlyMetricRepository(Protocol):
    """The precalculated monthly metrics store (docs/03-modelo-datos.md:432-443).

    One row per `(plot_id, month)`, so writing is an upsert on that key rather
    than an insert-or-fail: the job must be safe to re-run (D-T0.2).
    """

    async def upsert(self, metric: PlotMonthlyMetric) -> PlotMonthlyMetric: ...

    async def get(self, org_id: UUID, plot_id: UUID, *, month: date) -> PlotMonthlyMetric | None:
        """The plot's row for one month, or `None`. `org_id` filters, never assumed
        (docs/09-cuellos-de-botella.md#seguridad)."""
        ...

    async def latest_for_plot(self, org_id: UUID, plot_id: UUID) -> PlotMonthlyMetric | None:
        """The plot's most recent stored month, or `None` when it has none.

        This is what `GET /plots/{plot_id}/status` returns (D-T0.13): the latest
        month on record, never a recomputation, because a dashboard reading must
        not change when nobody re-ran the job.
        """
        ...


async def _components_for(
    *,
    plot_id: UUID,
    org_id: UUID,
    month: date,
    rainfed: bool,
    sources: MetricsSourceRepository,
) -> AdoptionComponents:
    """The four components of one plot-month, from T3's views.

    Each view is asked exactly once and its rows mapped to the domain's own
    evidence types: `domain` imports nothing, so the mapping happens here at the
    boundary (docs/05 §Reglas).
    """
    from_day, to_day = month_bounds(month)

    nodes = [
        NodeEvidence(
            interval_s=row.interval_s,
            claimed_seconds=row.claimed_seconds,
            received_readings=row.received_readings,
        )
        for row in await sources.node_month_readings(org_id, plot_id, month=month)
    ]

    weeks = await sources.logbook_weeks(org_id, plot_id, month=month)

    days = [
        RecommendationDay(kind=row.kind, depth_mm=row.depth_mm, irrigation_mm=row.irrigation_mm)
        for row in await sources.decision_days(org_id, plot_id, from_day=from_day, to_day=to_day)
    ]

    alerts = await sources.plot_alert_actions(org_id, plot_id, from_day=from_day, to_day=to_day)
    timely = sum(1 for row in alerts if row.has_timely_action)

    return AdoptionComponents(
        monitoring=monitoring_component(nodes),
        record_keeping=record_keeping_component(len(weeks), month),
        decision=decision_component(days, rainfed=rainfed),
        risk_management=risk_management_component(len(alerts), timely),
    )


async def compute_plot_month(
    *,
    org_id: UUID,
    plot_id: UUID,
    month: date,
    plots: PlotRepository,
    sources: MetricsSourceRepository,
    metrics: MonthlyMetricRepository,
    computed_at: datetime | None = None,
) -> PlotMonthlyMetric:
    """Compute one plot's adoption index for a calendar month and store it.

    `month` is the first day of the calendar month in America/Bogota (D-T0.7);
    `month_bounds` derives the last day rather than the caller adding 30.

    `computed_at` is an argument so a caller can freeze the clock and a re-run of
    the same evidence writes the same row; it defaults to now (UTC) because the
    application layer stores instants naive-UTC (docs/05 §Reglas).
    """
    plot = await plots.get_for_orgs(plot_id, [org_id])
    if plot is None:
        raise PlotNotFoundError(plot_id)

    components = await _components_for(
        plot_id=plot_id,
        org_id=org_id,
        month=month,
        rainfed=plot.irrigation_system is IrrigationSystem.NONE,
        sources=sources,
    )
    metric = PlotMonthlyMetric(
        plot_id=plot_id,
        org_id=org_id,
        month=month,
        components=components,
        digital_adoption_index=digital_adoption_index(components),
        computed_at=computed_at if computed_at is not None else datetime.now(UTC),
    )
    return await metrics.upsert(metric)


async def get_latest_plot_month(
    *,
    org_id: UUID,
    plot_id: UUID,
    metrics: MonthlyMetricRepository,
) -> PlotMonthlyMetric | None:
    """The plot's latest stored month, or `None` (D-T0.13).

    Reads only what the job wrote: a plot with no stored month has no index yet,
    which is not an error and not an index of zero.
    """
    return await metrics.latest_for_plot(org_id, plot_id)
