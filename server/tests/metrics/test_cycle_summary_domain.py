"""Pure impact math for one crop cycle (docs/11-metricas.md §1; D-T0.8, D-T0.9).

Every metric is a function of the raw totals `metrics_crop_cycle_totals` reads
(docs/11 §1) plus three facts the view cannot see: the plot's area and whether it
is irrigated at all, the crop of the cycle, and the enrollment survey the change
is measured against. No I/O and no clock — `computed_at` arrives as an argument,
so the numbers never depend on the day the suite runs (E11 lessons, #12).

The rule every test here defends is docs/03-modelo-datos.md:441: a missing
datum is `null`, never `0`. A division with nothing to divide by is therefore
missing evidence too, not a zero and not an error.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from techcamp.metrics.domain.cycle_summary import (
    CycleEvidence,
    CycleSummary,
    compute_cycle_summary,
)

ORG_ID = UUID("018f0000-0000-7000-8000-000000000001")
PLOT_ID = UUID("018f0000-0000-7000-8000-000000000002")
CYCLE_ID = UUID("018f0000-0000-7000-8000-000000000003")
COMPUTED_AT = datetime(2026, 10, 1, 7, 0, tzinfo=UTC)
"""Frozen: `computed_at` is what the month 1, 02:00 job writes (D-T0.7)."""

CROP_ID = 1
"""`maize`, seeded by `67cf2dd1f13e_add_crop_catalog`. `2` is `cassava`."""
OTHER_CROP_ID = 2

AREA_HA = Decimal("2")
"""A two-hectare plot: every per-hectare figure below divides by it exactly, so
an expectation can be written as a literal instead of a rounded float."""


@dataclass(frozen=True, slots=True)
class _Totals:
    """The view's own numbers, so each test states only what it varies."""

    yield_kg: Decimal | None = Decimal("12000")
    revenue_cop: Decimal | None = Decimal("9000000")
    labor_days: Decimal | None = Decimal("30")
    cost_cop: Decimal | None = Decimal("6000000")
    irrigation_mm: Decimal | None = Decimal("500")
    loss_kg: Decimal | None = Decimal("120")
    loss_cop: Decimal | None = Decimal("800000")
    water_stress_days: int | None = 7


def _evidence(
    totals: _Totals | None = None,
    *,
    area_ha: Decimal = AREA_HA,
    irrigated: bool = True,
    cycle_crop_id: int = CROP_ID,
    baseline_crop_id: int | None = CROP_ID,
    baseline_yield_kg_ha: Decimal | None = Decimal("5000"),
) -> CycleEvidence:
    totals = totals if totals is not None else _Totals()
    return CycleEvidence(
        crop_cycle_id=CYCLE_ID,
        plot_id=PLOT_ID,
        org_id=ORG_ID,
        area_ha=area_ha,
        irrigated=irrigated,
        cycle_crop_id=cycle_crop_id,
        baseline_crop_id=baseline_crop_id,
        baseline_yield_kg_ha=baseline_yield_kg_ha,
        yield_kg=totals.yield_kg,
        revenue_cop=totals.revenue_cop,
        labor_days=totals.labor_days,
        cost_cop=totals.cost_cop,
        irrigation_mm=totals.irrigation_mm,
        loss_kg=totals.loss_kg,
        loss_cop=totals.loss_cop,
        water_stress_days=totals.water_stress_days,
    )


def _summary(evidence: CycleEvidence | None = None) -> CycleSummary:
    evidence = evidence if evidence is not None else _evidence()
    return compute_cycle_summary(evidence, computed_at=COMPUTED_AT)


def test_a_cycle_with_evidence_reports_every_impact_metric() -> None:
    """docs/11 §1: one row of its table per metric, over the totals above."""
    summary = _summary()

    assert summary.crop_cycle_id == CYCLE_ID
    assert summary.plot_id == PLOT_ID
    assert summary.org_id == ORG_ID
    assert summary.computed_at == COMPUTED_AT
    assert summary.yield_kg_ha == Decimal("6000")
    assert summary.yield_change_vs_baseline == Decimal("0.2")
    assert summary.water_applied_m3_ha == Decimal("5000")
    assert summary.irrigation_wue_kg_m3 == Decimal("1.2")
    assert summary.water_stress_days == 7
    assert summary.cost_cop_ha == Decimal("3000000")
    assert summary.cost_cop_kg == Decimal("500")
    assert summary.yield_kg_per_labor_day == Decimal("400")
    assert summary.gross_margin_cop == Decimal("3000000")
    assert summary.loss_kg == Decimal("120")
    assert summary.loss_cop == Decimal("800000")


def test_a_cycle_without_a_harvest_reports_no_yield_and_no_per_kilogram_figure() -> None:
    """No harvest entry means no `yield_kg`, which is not a harvest of zero
    (docs/03-modelo-datos.md:441)."""
    summary = _summary(_evidence(_Totals(yield_kg=None, revenue_cop=None)))

    assert summary.yield_kg_ha is None
    assert summary.yield_change_vs_baseline is None
    assert summary.cost_cop_kg is None
    assert summary.yield_kg_per_labor_day is None
    assert summary.gross_margin_cop is None
    # Evidence of another kind survives the missing harvest.
    assert summary.cost_cop_ha == Decimal("3000000")
    assert summary.water_stress_days == 7


def test_a_rainfed_plot_reports_no_applied_water_and_no_water_productivity() -> None:
    """docs/11 §1: "Solo parcelas con riego"; a rainfed plot has neither, and its
    water result is reported with yield and stress days (ADR-0023)."""
    summary = _summary(_evidence(irrigated=False))

    assert summary.water_applied_m3_ha is None
    assert summary.irrigation_wue_kg_m3 is None
    assert summary.water_stress_days == 7
    assert summary.yield_kg_ha == Decimal("6000")


def test_a_cycle_whose_crop_differs_from_the_survey_has_no_comparable_change() -> None:
    """docs/11 §1: the change is computed "solo si el cultivo es el mismo"."""
    summary = _summary(_evidence(baseline_crop_id=OTHER_CROP_ID))

    assert summary.yield_change_vs_baseline is None
    assert summary.yield_kg_ha == Decimal("6000")


def test_a_plot_without_a_survey_has_no_comparable_change() -> None:
    summary = _summary(_evidence(baseline_crop_id=None, baseline_yield_kg_ha=None))

    assert summary.yield_change_vs_baseline is None


def test_a_declared_zero_yield_makes_the_change_undeterminable() -> None:
    """docs/11 §2 and #243: a lost season is a valid survey answer, but no
    percentage change can be measured against zero."""
    summary = _summary(_evidence(baseline_yield_kg_ha=Decimal("0")))

    assert summary.yield_change_vs_baseline is None
    assert summary.yield_kg_ha == Decimal("6000")


def test_the_relative_yield_stays_null_until_a_field_record_exists() -> None:
    """D-T0.9: there is no EVA `field_record` in E11, so the ratio has no
    denominator (docs/11 §1)."""
    assert _summary().relative_yield is None


def test_a_zero_denominator_reads_as_missing_evidence() -> None:
    """No jornal and no cubic meter were applied, so kilograms per day and per
    cubic meter cannot exist — `null`, not `0` (docs/03:441, the same rule as the
    zero-yield case of #243)."""
    summary = _summary(_evidence(_Totals(labor_days=Decimal("0"), irrigation_mm=Decimal("0"))))

    assert summary.yield_kg_per_labor_day is None
    assert summary.irrigation_wue_kg_m3 is None
    # Applied water of zero is evidence of its own: the logbook says the plot was
    # irrigated and no water went in, which is not the same as no irrigation entry.
    assert summary.water_applied_m3_ha == Decimal("0")


def test_a_cycle_with_no_evidence_at_all_reports_every_metric_null() -> None:
    summary = _summary(
        _evidence(
            _Totals(
                yield_kg=None,
                revenue_cop=None,
                labor_days=None,
                cost_cop=None,
                irrigation_mm=None,
                loss_kg=None,
                loss_cop=None,
                water_stress_days=None,
            )
        )
    )

    assert summary.yield_kg_ha is None
    assert summary.yield_change_vs_baseline is None
    assert summary.relative_yield is None
    assert summary.water_applied_m3_ha is None
    assert summary.irrigation_wue_kg_m3 is None
    assert summary.water_stress_days is None
    assert summary.cost_cop_ha is None
    assert summary.cost_cop_kg is None
    assert summary.yield_kg_per_labor_day is None
    assert summary.gross_margin_cop is None
    assert summary.loss_kg is None
    assert summary.loss_cop is None


def test_millimeters_become_cubic_meters_per_hectare() -> None:
    """docs/11 §1: `Σ mm de riego × 10` → m³/ha."""
    summary = _summary(_evidence(_Totals(irrigation_mm=Decimal("123.5"))))

    assert summary.water_applied_m3_ha == Decimal("1235.0")
