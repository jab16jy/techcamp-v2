"""The impact of one crop cycle, as pure domain math (docs/11-metricas.md §1).

Eleven figures come out of the raw totals `metrics_crop_cycle_totals` reads
(docs/11 §1) plus what the view cannot see: the plot's area and whether it is
irrigated at all, the crop of the cycle, and the enrollment survey the change is
measured against (docs/03-modelo-datos.md §`plot_baseline`). No I/O, no clock —
`computed_at` arrives as an argument (E11 lessons, #12), and no import outside
this package, so the math is testable as plain values.

**Nothing here stores a missing datum as zero** (docs/03-modelo-datos.md:441).
Every division that has nothing to divide by — or a zero to divide by — answers
`null`, which is the rule docs/11 §2 already states for the change against a
survey whose `last_yield_kg_ha` is `0` (#243). A cycle with no harvest entry did
not harvest zero kilograms; it has no harvest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

M3_PER_MM_HA = Decimal(10)
"""docs/11 §1: `Σ mm de riego × 10` → m³/ha. One millimetre of water spread over
one hectare is ten cubic metres, so the factor is exact and not a figure this
module may tune."""


@dataclass(frozen=True, slots=True)
class CycleEvidence:
    """Everything the impact math needs about one cycle, already read.

    The raw totals are `CycleTotals` (docs/11 §1) as the source repository hands
    them over; the four extra fields are what the totals view cannot carry and
    the caller therefore resolves before calling `compute_cycle_summary`.
    """

    crop_cycle_id: UUID
    plot_id: UUID
    org_id: UUID
    """Carried with the plot so the persisted row filters by organization
    (docs/09-cuellos-de-botella.md#seguridad)."""
    area_ha: Decimal
    """`plot.area_ha`, which PostGIS computes from the boundary
    (docs/03-modelo-datos.md:100)."""
    irrigated: bool
    """`plot.irrigation_system != none` (ADR-0023). A rainfed plot has no applied
    water and no water productivity, whatever its logbook says."""
    cycle_crop_id: int
    """The crop this cycle planted, compared with the survey's crop."""
    baseline_crop_id: int | None
    """`None` when the plot has no enrollment survey (D-T0.11)."""
    baseline_yield_kg_ha: Decimal | None
    """`plot_baseline.last_yield_kg_ha`, the figure impact is measured against
    (docs/03-modelo-datos.md:426-430)."""
    yield_kg: Decimal | None
    revenue_cop: Decimal | None
    """`Σ sold_kg × sale_price_cop_per_kg` over the cycle's harvest entries: the
    gross revenue of docs/11 §1's margin row."""
    labor_days: Decimal | None
    cost_cop: Decimal | None
    """`task`, `input` and `cost` entries only; an `observation` carrying an
    `alert_id` is a loss (docs/03-modelo-datos.md:424)."""
    irrigation_mm: Decimal | None
    loss_kg: Decimal | None
    loss_cop: Decimal | None
    water_stress_days: int | None
    """Days inside the cycle with `depletion_mm > raw_mm`, i.e. `Ks < 1`
    (docs/11 §1, ADR-0022). `None` is a cycle with no assimilated balance, which
    is not a stress-free cycle."""


@dataclass(frozen=True, slots=True)
class CycleSummary:
    """The impact of one finished cycle, as `crop_cycle_summary` stores it
    (docs/03-modelo-datos.md:439).

    Every metric is `Optional` and the row is only written for a `harvested` or
    `lost` cycle (D-T0.8); an active cycle gets the same value computed on read
    and never stored.
    """

    crop_cycle_id: UUID
    plot_id: UUID
    org_id: UUID
    yield_kg_ha: Decimal | None
    yield_change_vs_baseline: Decimal | None
    """`(yield − last_yield_kg_ha) / last_yield_kg_ha`, a fraction and not a
    percentage, computed only when the cycle planted the survey's crop
    (docs/11 §1)."""
    relative_yield: Decimal | None
    """Always `None` in E11: it divides by the EVA median and no `field_record`
    exists yet (D-T0.9)."""
    water_applied_m3_ha: Decimal | None
    irrigation_wue_kg_m3: Decimal | None
    """Harvested kilograms over applied cubic meters (docs/11 §1)."""
    water_stress_days: int | None
    cost_cop_ha: Decimal | None
    cost_cop_kg: Decimal | None
    yield_kg_per_labor_day: Decimal | None
    """Approximation of ODS 2.3.1 (docs/11 §1)."""
    gross_margin_cop: Decimal | None
    loss_kg: Decimal | None
    loss_cop: Decimal | None
    computed_at: datetime
    """When this computation ran. Passed in, never read from the clock, so a
    stored figure always says how fresh it is (D-T0.7)."""


def _quotient(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    """`numerator / denominator`, or `None` when there is nothing to divide or
    nothing to divide by.

    The zero denominator is the same case as the zero survey yield of docs/11 §2
    (#243): a lost season is a valid answer and no change can be measured against
    it, so the figure is missing rather than infinite.
    """
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _change_against(numerator: Decimal | None, baseline: Decimal | None) -> Decimal | None:
    """`(numerator − baseline) / baseline`, with the same zero rule as
    `_quotient` (#243)."""
    if numerator is None or baseline is None or baseline == 0:
        return None
    return (numerator - baseline) / baseline


def _difference(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    """`left − right`, or `None` when either side is missing evidence: a margin
    needs both the revenue it earned and the cost it spent (docs/11 §1)."""
    if left is None or right is None:
        return None
    return left - right


def compute_cycle_summary(evidence: CycleEvidence, *, computed_at: datetime) -> CycleSummary:
    """Turn one cycle's evidence into the impact of docs/11 §1.

    Every metric is a quotient of what the cycle recorded, so a cycle with no
    harvest, no task, no cost or no irrigation answers `null` for the figures
    that depend on it and keeps the ones it does have evidence for (D-T0.3,
    docs/03-modelo-datos.md:441).
    """
    yield_kg_ha = _quotient(evidence.yield_kg, evidence.area_ha)

    # A rainfed plot has no applied water and no water productivity at all
    # (docs/11 §1, ADR-0023), even if its logbook carries an irrigation entry.
    applied_mm = evidence.irrigation_mm if evidence.irrigated else None
    water_applied_m3_ha = None if applied_mm is None else applied_mm * M3_PER_MM_HA
    # Both sides are per hectare, so the hectare cancels: kg/ha ÷ m³/ha is the
    # kg/m³ docs/11 §1 asks for, without needing the plot's area twice.
    irrigation_wue_kg_m3 = _quotient(yield_kg_ha, water_applied_m3_ha)

    yield_change_vs_baseline = None
    if evidence.baseline_crop_id == evidence.cycle_crop_id:
        yield_change_vs_baseline = _change_against(yield_kg_ha, evidence.baseline_yield_kg_ha)

    return CycleSummary(
        crop_cycle_id=evidence.crop_cycle_id,
        plot_id=evidence.plot_id,
        org_id=evidence.org_id,
        yield_kg_ha=yield_kg_ha,
        yield_change_vs_baseline=yield_change_vs_baseline,
        # Always null in E11: the municipal median it divides by lives in EVA's
        # `field_record`, which v2 does not read yet (D-T0.9, #248's sibling
        # follow-up).
        relative_yield=None,
        water_applied_m3_ha=water_applied_m3_ha,
        irrigation_wue_kg_m3=irrigation_wue_kg_m3,
        water_stress_days=evidence.water_stress_days,
        cost_cop_ha=_quotient(evidence.cost_cop, evidence.area_ha),
        cost_cop_kg=_quotient(evidence.cost_cop, evidence.yield_kg),
        yield_kg_per_labor_day=_quotient(evidence.yield_kg, evidence.labor_days),
        gross_margin_cop=_difference(evidence.revenue_cop, evidence.cost_cop),
        loss_kg=evidence.loss_kg,
        loss_cop=evidence.loss_cop,
        computed_at=computed_at,
    )
