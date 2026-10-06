"""The cycle-impact use case (docs/11-metricas.md §1; D-T0.8).

Two rules live here, not in the math: a cycle that is still `active` is computed
on read and never written (D-T0.8), and a cycle outside the caller's
organization is not found at all (docs/09-cuellos-de-botella.md#seguridad). The
math itself is covered by `test_cycle_summary_domain`.

Every collaborator is a fake: this is the layer that *decides* which repositories
to read, and the repositories' own filtering is proven against the real database
in `test_cycle_totals_view` (T3) and `test_cycle_summary_repository`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from techcamp.farms.domain.errors import CropCycleNotFoundError
from techcamp.farms.domain.models import CropCycle, CropCycleStatus, IrrigationSystem, Plot
from techcamp.metrics.application.cycle_summary import summarize_cycle
from techcamp.metrics.application.ports import CycleTotals
from techcamp.metrics.domain.cycle_summary import CycleSummary
from techcamp.metrics.domain.models import IrrigationPractice, PlotBaseline
from techcamp.shared.ids import uuid7

ORG_ID = uuid7()
PLOT_ID = uuid7()
CYCLE_ID = uuid7()
FARM_ID = uuid7()
USER_ID = uuid7()
CROP_ID = 1
"""`maize`, the crop `tests/metrics/conftest.py` seeds on its cycles."""
pytestmark = pytest.mark.anyio
NOW = datetime(2026, 10, 1, 7, 0, tzinfo=UTC)
"""Frozen: the row's `computed_at` is what the month 1, 02:00 job writes (D-T0.7)."""


class _FakeCycleRepository:
    """Only `get_for_orgs`, which is how a cycle is resolved inside one org.

    `crop_cycle` carries no `org_id`, so the real repository filters through the
    cycle's plot; `plot_org_id` is how this fake reproduces that join.
    """

    def __init__(self, cycle: CropCycle | None, *, plot_org_id: UUID = ORG_ID) -> None:
        self._cycle = cycle
        self._plot_org_id = plot_org_id
        self.calls: list[tuple[UUID, list[UUID]]] = []

    async def get_for_orgs(self, cycle_id: UUID, org_ids: list[UUID]) -> CropCycle | None:
        self.calls.append((cycle_id, org_ids))
        if self._cycle is None or self._cycle.id != cycle_id:
            return None
        return self._cycle if self._plot_org_id in org_ids else None


class _FakePlotRepository:
    def __init__(self, plot: Plot | None) -> None:
        self._plot = plot

    async def get_for_orgs(self, plot_id: UUID, org_ids: list[UUID]) -> Plot | None:
        if self._plot is None:
            return None
        if self._plot.id != plot_id or self._plot.org_id not in org_ids:
            return None
        return self._plot


class _FakeBaselineRepository:
    def __init__(self, baseline: PlotBaseline | None) -> None:
        self._baseline = baseline
        self.calls: list[tuple[UUID, UUID]] = []

    async def get_for_org(self, plot_id: UUID, org_id: UUID) -> PlotBaseline | None:
        self.calls.append((plot_id, org_id))
        if self._baseline is None:
            return None
        if self._baseline.plot_id != plot_id or self._baseline.org_id != org_id:
            return None
        return self._baseline


class _FakeSourceRepository:
    def __init__(self, totals: CycleTotals | None) -> None:
        self._totals = totals
        self.calls: list[tuple[UUID, UUID]] = []

    async def cycle_totals(self, org_id: UUID, crop_cycle_id: UUID) -> CycleTotals | None:
        self.calls.append((org_id, crop_cycle_id))
        if self._totals is None or self._totals.crop_cycle_id != crop_cycle_id:
            return None
        return self._totals


class _RecordingSummaryRepository:
    """Counts what the use case stores, and hands the same value back: the real
    repository reads the row the database wrote (adapters/cycle_summary_repository)."""

    def __init__(self) -> None:
        self.upserted: list[object] = []

    async def upsert(self, summary: object) -> object:
        self.upserted.append(summary)
        return summary


def _cycle(status: CropCycleStatus = CropCycleStatus.HARVESTED) -> CropCycle:
    return CropCycle(
        id=CYCLE_ID,
        plot_id=PLOT_ID,
        crop_id=CROP_ID,
        sown_on=date(2026, 8, 1),
        expected_harvest_on=date(2026, 12, 15),
        status=status,
    )


def _plot(*, irrigation_system: IrrigationSystem = IrrigationSystem.DRIP) -> Plot:
    return Plot(
        id=PLOT_ID,
        org_id=ORG_ID,
        farm_id=FARM_ID,
        name="Lote 1",
        boundary="POLYGON((0 0, 0 1, 1 1, 1 0, 0 0))",
        area_ha=2.0,
        weather_cell_id=None,
        irrigation_system=irrigation_system,
        irrigation_efficiency=None,
        system_flow_lph=None,
    )


def _totals() -> CycleTotals:
    return CycleTotals(
        crop_cycle_id=CYCLE_ID,
        plot_id=PLOT_ID,
        yield_kg=Decimal("12000"),
        sold_kg=Decimal("12000"),
        revenue_cop=Decimal("9000000"),
        labor_days=Decimal("30"),
        cost_cop=Decimal("6000000"),
        irrigation_mm=Decimal("500"),
        loss_kg=None,
        loss_cop=None,
        water_stress_days=7,
    )


def _baseline(*, crop_id: int = CROP_ID, last_yield_kg_ha: float = 5000.0) -> PlotBaseline:
    return PlotBaseline(
        plot_id=PLOT_ID,
        org_id=ORG_ID,
        enrolled_on=date(2026, 2, 10),
        crop_id=crop_id,
        last_yield_kg_ha=last_yield_kg_ha,
        last_cost_cop_ha=None,
        irrigation_practice=IrrigationPractice.GRAVITY,
        recorded_by=USER_ID,
    )


async def _summarize(
    *,
    cycle: CropCycle | None = None,
    plot: Plot | None = None,
    baseline: PlotBaseline | None = None,
    has_totals_row: bool = True,
    now: datetime = NOW,
) -> tuple[CycleSummary, _RecordingSummaryRepository]:
    """Run the use case over defaults that only a failing test changes.

    `has_totals_row=False` is the "no evidence at all" cycle, which is not the
    same as a cycle with no argument here.
    """
    cycle = cycle if cycle is not None else _cycle()
    plot = plot if plot is not None else _plot()
    totals = _totals() if has_totals_row else None
    summaries = _RecordingSummaryRepository()
    result = await summarize_cycle(
        org_id=ORG_ID,
        crop_cycle_id=CYCLE_ID,
        cycles=_FakeCycleRepository(cycle),
        plots=_FakePlotRepository(plot),
        baselines=_FakeBaselineRepository(baseline),
        sources=_FakeSourceRepository(totals),
        summaries=summaries,
        now=now,
    )
    return result, summaries


async def test_a_finished_cycle_is_summarized_and_stored() -> None:
    summary, summaries = await _summarize()

    assert len(summaries.upserted) == 1
    assert summary.yield_kg_ha == Decimal("6000")
    assert summary.cost_cop_ha == Decimal("3000000")
    assert summary.water_stress_days == 7
    assert summary.computed_at == NOW
    assert summary.org_id == ORG_ID
    assert summary.plot_id == PLOT_ID


@pytest.mark.parametrize("status", [CropCycleStatus.HARVESTED, CropCycleStatus.LOST])
async def test_both_ways_of_finishing_a_cycle_are_stored(status: CropCycleStatus) -> None:
    """docs/03-modelo-datos.md:443: the row holds `harvested` and `lost` cycles;
    a lost season is exactly the one whose survey change cannot be measured."""
    _summary_value, summaries = await _summarize(cycle=_cycle(status))

    assert len(summaries.upserted) == 1


async def test_an_active_cycle_is_computed_on_read_and_never_stored() -> None:
    """D-T0.8: the summary of a cycle still running is calculated when it is read
    and not saved, so it can never be stale."""
    summary, summaries = await _summarize(cycle=_cycle(CropCycleStatus.ACTIVE))

    assert summaries.upserted == []
    assert summary.yield_kg_ha == Decimal("6000")
    assert summary.computed_at == NOW


async def test_a_cycle_of_another_organization_is_not_found() -> None:
    """The cycle exists and carries real evidence; it belongs to another tenant,
    so the caller gets the same answer as for a cycle that does not exist
    (docs/09-cuellos-de-botella.md#seguridad)."""
    foreign_org_id = uuid7()
    cycles = _FakeCycleRepository(_cycle(), plot_org_id=foreign_org_id)
    sources = _FakeSourceRepository(_totals())
    summaries = _RecordingSummaryRepository()

    with pytest.raises(CropCycleNotFoundError):
        await summarize_cycle(
            org_id=ORG_ID,
            crop_cycle_id=CYCLE_ID,
            cycles=cycles,
            plots=_FakePlotRepository(_plot()),
            baselines=_FakeBaselineRepository(_baseline()),
            sources=sources,
            summaries=summaries,
            now=NOW,
        )

    assert cycles.calls == [(CYCLE_ID, [ORG_ID])]
    # Nothing downstream may be read for a cycle the caller cannot see.
    assert sources.calls == []
    assert summaries.upserted == []


async def test_a_rainfed_plot_is_summarized_without_applied_water() -> None:
    """ADR-0023: a rainfed plot has no applied water and no water productivity;
    yield and stress days still report its water result (docs/11 §1)."""
    summary, _summaries = await _summarize(plot=_plot(irrigation_system=IrrigationSystem.NONE))

    assert summary.water_applied_m3_ha is None
    assert summary.irrigation_wue_kg_m3 is None
    assert summary.water_stress_days == 7
    assert summary.yield_kg_ha == Decimal("6000")


async def test_the_change_against_the_survey_is_computed_only_for_the_same_crop() -> None:
    summary, _summaries = await _summarize(baseline=_baseline(crop_id=2))

    assert summary.yield_change_vs_baseline is None


async def test_a_plot_without_a_survey_is_still_summarized() -> None:
    summary, summaries = await _summarize(baseline=None)

    assert summary.yield_change_vs_baseline is None
    assert summary.yield_kg_ha == Decimal("6000")
    assert len(summaries.upserted) == 1


async def test_a_cycle_with_no_totals_row_is_stored_with_every_metric_null() -> None:
    """The view carries a row for every cycle, so this is a cycle whose evidence
    is entirely absent — a lost season with nothing logged is still a cycle that
    finished, and it stores `null`, never `0` (docs/03-modelo-datos.md:441)."""
    summary, summaries = await _summarize(has_totals_row=False)

    assert summary.yield_kg_ha is None
    assert summary.yield_change_vs_baseline is None
    assert summary.water_applied_m3_ha is None
    assert summary.water_stress_days is None
    assert summary.cost_cop_ha is None
    assert len(summaries.upserted) == 1


async def test_the_stored_row_is_stamped_with_the_instant_the_caller_names() -> None:
    """The clock is a parameter, so a test and the monthly job (D-T0.7) both know
    exactly which instant a stored figure describes."""
    later = datetime(2026, 10, 1, 7, 30, tzinfo=UTC)

    summary, _summaries = await _summarize(now=later)

    assert summary.computed_at == later
