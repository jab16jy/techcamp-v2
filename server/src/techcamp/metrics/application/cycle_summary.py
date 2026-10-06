"""Cycle-impact use case (docs/11-metricas.md §1; D-T0.8).

`summarize_cycle` is the only way a cycle's impact is produced: it reads the raw
totals `metrics_crop_cycle_totals` aggregates, resolves the three facts the view
cannot carry — the plot's area and irrigation practice, the crop of the cycle and
the enrollment survey — and hands them to the pure math in
`domain/cycle_summary.py`.

**Where the number lives is the use case's decision, not the math's** (D-T0.8,
docs/03-modelo-datos.md:443): a `harvested` or `lost` cycle is written to
`crop_cycle_summary`, and an `active` one is returned without a write, because a
running cycle's figures change every day and a stored row would be stale the
moment after it was written.

Access is resolved through `farms` (D-T0.1: `metrics → farms`), the same facade
`metrics/application/baseline.py` and `home` use: `crop_cycle` carries no
`org_id`, so a cycle outside the caller's organization is `CropCycleNotFoundError`
and cannot be told apart from a missing one (docs/09-cuellos-de-botella.md
#seguridad). The caller passes the `org_id` it was authorized for — the monthly
job (D-T0.7) and the read API (T7) each resolve it their own way.

The `CycleSummaryRepository` port lives here, not in `application/ports.py`,
because T3 owns that file and its metrics source repository (E11 lane split, the
same reason `BaselineRepository` lives in `application/baseline.py`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from techcamp.farms.application.ports import CropCycleRepository, PlotRepository
from techcamp.farms.domain.errors import CropCycleNotFoundError, PlotNotFoundError
from techcamp.farms.domain.models import CropCycleStatus, IrrigationSystem
from techcamp.metrics.application.baseline import BaselineRepository
from techcamp.metrics.application.ports import MetricsSourceRepository
from techcamp.metrics.domain.cycle_summary import CycleEvidence, CycleSummary, compute_cycle_summary

PERSISTED_STATUSES = frozenset({CropCycleStatus.HARVESTED, CropCycleStatus.LOST})
"""The cycles `crop_cycle_summary` holds (docs/03-modelo-datos.md:443, D-T0.8).
Everything else — today, only `active` — is computed on read."""


class CycleSummaryRepository(Protocol):
    """The impact of every finished cycle (docs/03 §`crop_cycle_summary`).

    Keyed by `crop_cycle_id`, and every method takes the organization: the table
    carries `org_id` and a metric row is a tenant's own figure
    (docs/09-cuellos-de-botella.md#seguridad).
    """

    async def upsert(self, summary: CycleSummary) -> CycleSummary:
        """Insert or replace the cycle's row and return the stored one.

        An upsert, because the monthly job may run twice over the same closed
        cycle and must land the same figures (docs/03-modelo-datos.md:442).
        """
        ...

    async def get_for_org(self, crop_cycle_id: UUID, org_id: UUID) -> CycleSummary | None:
        """The stored impact of a finished cycle, or `None` when it has none:
        either the cycle is still active (D-T0.8) or it belongs to another
        organization, which the caller cannot tell apart from the first."""
        ...


def _as_decimal(value: float) -> Decimal:
    """`Decimal(str(value))`, never `Decimal(value)`: a float's binary value is
    not its decimal one, so the direct constructor writes figures the database
    never computed (`Plot.area_ha` comes from PostGIS and the survey is a
    farmer's approximate number — same guard as
    `metrics/adapters/repositories.py:22`)."""
    return Decimal(str(value))


async def summarize_cycle(
    *,
    org_id: UUID,
    crop_cycle_id: UUID,
    cycles: CropCycleRepository,
    plots: PlotRepository,
    baselines: BaselineRepository,
    sources: MetricsSourceRepository,
    summaries: CycleSummaryRepository,
    now: datetime | None = None,
) -> CycleSummary:
    """The impact of one cycle, stored when the cycle has finished.

    `now` is the instant written to `computed_at` (D-T0.7); it defaults to the
    wall clock, and a test passes the one it wants to assert.

    Raises `CropCycleNotFoundError` when the cycle does not exist or its plot is
    in another organization — the same answer for both, so a cycle of another
    tenant cannot be probed for (docs/09-cuellos-de-botella.md#seguridad).
    """
    cycle = await cycles.get_for_orgs(crop_cycle_id, [org_id])
    if cycle is None:
        raise CropCycleNotFoundError(crop_cycle_id)
    plot = await plots.get_for_orgs(cycle.plot_id, [org_id])
    if plot is None:
        # Unreachable while `get_for_orgs` filters a cycle through its plot, and
        # answered as a missing plot rather than an assertion: the two reads are
        # separate statements, so a cycle deleted between them must not become a
        # crash with no domain error.
        raise PlotNotFoundError(cycle.plot_id)
    baseline = await baselines.get_for_org(plot.id, org_id)
    totals = await sources.cycle_totals(org_id, crop_cycle_id)

    summary = compute_cycle_summary(
        CycleEvidence(
            crop_cycle_id=cycle.id,
            plot_id=plot.id,
            org_id=org_id,
            area_ha=_as_decimal(plot.area_ha),
            irrigated=plot.irrigation_system is not IrrigationSystem.NONE,
            cycle_crop_id=cycle.crop_id,
            baseline_crop_id=None if baseline is None else baseline.crop_id,
            baseline_yield_kg_ha=(
                None if baseline is None else _as_decimal(baseline.last_yield_kg_ha)
            ),
            # A cycle of this organization with no row in the totals view has no
            # evidence of any kind, which is every metric null — not zero. It is
            # unreachable today (the view has no `WHERE`), and reading it as a
            # missing cycle instead would silently drop a cycle that did finish.
            yield_kg=None if totals is None else totals.yield_kg,
            revenue_cop=None if totals is None else totals.revenue_cop,
            labor_days=None if totals is None else totals.labor_days,
            cost_cop=None if totals is None else totals.cost_cop,
            irrigation_mm=None if totals is None else totals.irrigation_mm,
            loss_kg=None if totals is None else totals.loss_kg,
            loss_cop=None if totals is None else totals.loss_cop,
            water_stress_days=None if totals is None else totals.water_stress_days,
        ),
        computed_at=now if now is not None else datetime.now(UTC),
    )

    if cycle.status not in PERSISTED_STATUSES:
        return summary
    return await summaries.upsert(summary)
