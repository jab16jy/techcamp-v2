"""Read-side use case for the adoption month of a plot (E11 T7).

`GET /plots/{plot_id}/metrics` answers the row the monthly job stored for one
calendar month (docs/04-api.md:234). It resolves the plot through
`resolve_plot_access`, so a plot outside the caller's organizations is not found
for any role (docs/04-api.md:237, docs/09-cuellos-de-botella.md#seguridad).

It does not write. The monthly job owns every write to `plot_metric_monthly`,
so a `GET` that stored a row would make the dashboard's figures depend on who
looked at them and when (docs/11-metricas.md:131).
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from techcamp.farms.application.manage_plots import resolve_plot_access
from techcamp.farms.application.ports import PlotRepository
from techcamp.identity.application.ports import MembershipRepository
from techcamp.metrics.application.adoption import MonthlyMetricRepository
from techcamp.metrics.domain.adoption import PlotMonthlyMetric


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
    the key the row is stored under, which is why the adapter normalizes a
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
