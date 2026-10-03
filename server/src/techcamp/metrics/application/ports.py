"""Ports of the metrics module (E11 T3).

`MetricsSourceRepository` is the one way the module reads other modules' data.
`metrics` is the documented exception to the "no joins between foreign tables"
rule (docs/05-arquitectura.md §Reglas: "`metrics` es la única excepción: lee
vistas SQL de solo lectura porque agrega datos de todos"), and this port is the
only place those read-only views are reached: plot access and the plot's cycles
come from the `farms` facade instead (docs/05, D-T0.1).

Every method takes `org_id` and filters on it (docs/09-cuellos-de-botella.md
§Seguridad), and every view exposes `org_id`, so a foreign organization's row is
absent rather than leaked.

The dataclasses below are **read-model rows, not domain entities**: each one
mirrors one row of one read-only SQL view. They live here, next to the port that
returns them, instead of in `metrics/domain/`, because they are projections of
another module's tables rather than something `metrics` decides. The formulas
that consume them stay in the pure domain code of T4 and T5; these views only
aggregate (D-T0.3 to D-T0.8).

A metric with no evidence is `None`, never `0`: `Decimal | None` and
`int | None` below are deliberate (docs/03-modelo-datos.md:441, D-T0.3).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class NodeMonthReadings:
    """One node's evidence for one calendar month (D-T0.4, docs/11 §2).

    `received_readings` counts distinct uplink instants, whatever `quality`:
    every sensor of a node shares one `reading.time` per uplink, so counting
    timestamps is what makes the numerator comparable to `interval_s`. Out-of-
    range readings carry a null `value` and still count — the component measures
    that the plot is being measured, and an out-of-range value is already covered
    by the node alerts (docs/11 §2).

    `claimed_seconds` is how long the node was claimed inside the month, so a
    node claimed mid-month is not charged for a half month it never had.
    """

    node_id: UUID
    plot_id: UUID
    month: date
    """First day of the calendar month in America/Bogota (D-T0.7)."""
    interval_s: int
    claimed_seconds: int
    received_readings: int


@dataclass(frozen=True, slots=True)
class LogbookWeek:
    """One ISO week of one month that holds at least one non-deleted entry.

    The `record_keeping` numerator is the count of these rows; the denominator,
    "semanas del mes", is the same convention counted over the whole month and
    is calendar math, not evidence, so it belongs to the domain code (T5).

    Weeks are ISO weeks (Monday start) **overlapping** the month: a week that
    straddles the first or the last of the month appears in both months, with
    only the entries of that month counted. Both months then see the same
    calendar, so the ratio never exceeds 1 (docs/11 §2).
    """

    plot_id: UUID
    month: date
    week_start: date
    entry_count: int


@dataclass(frozen=True, slots=True)
class DecisionDay:
    """One stored recommendation and what the producer actually did that day
    (D-T0.5, docs/11 §2).

    `irrigation_mm` is the day's total applied depth from the logbook, and it
    is `None` — not `0` — when no irrigation was recorded that day: "did not
    irrigate" and "applied no millimetres" are different facts, and `postpone`
    and `not_needed` days are only followed when nothing was applied.

    `kind` is the raw `irrigation_recommendation.kind`. The vocabulary is closed
    by that table's own CHECK (docs/03-modelo-datos.md:210); the `metrics` read
    model does not restate another module's enum, so the domain code of T5
    matches the codes that count (`irrigate`, `postpone`, `not_needed`;
    `no_kc` and `rainfed` do not) against docs/11 §2.
    """

    plot_id: UUID
    day: date
    kind: str
    depth_mm: Decimal | None
    """Null for every kind but `irrigate` (docs/03-modelo-datos.md:211)."""
    irrigation_mm: Decimal | None


@dataclass(frozen=True, slots=True)
class PlotAlertAction:
    """One plot alert of the month and whether it was acted on in time
    (D-T0.6, docs/11 §2).

    Only alerts with a `plot_id` are here: a node alert goes to the technician
    and does not count for the plot (docs/11 §2). `has_timely_action` is the
    existence of the action, not the alert's state, because acknowledging an
    alert is not acting on it.
    """

    plot_id: UUID
    alert_id: UUID
    opened_at: datetime
    rule_code: str
    has_timely_action: bool


@dataclass(frozen=True, slots=True)
class CycleTotals:
    """One crop cycle's raw totals, before any per-hectare or per-kilogram
    division (docs/11 §1, D-T0.8).

    Every field is `None` when the cycle has no evidence of that kind: a cycle
    with no harvest entry has no `yield_kg`, which is not the same as having
    harvested zero (docs/03-modelo-datos.md:441).

    `cost_cop` counts `task`, `input` and `cost` entries only. An `observation`
    carrying an `alert_id` records a **loss**, so its `cost_cop` lands in
    `loss_cop` and never in the cost of the cycle (docs/03-modelo-datos.md:424).
    """

    crop_cycle_id: UUID
    plot_id: UUID
    yield_kg: Decimal | None
    sold_kg: Decimal | None
    revenue_cop: Decimal | None
    """`Σ sold_kg × sale_price_cop_per_kg` over the cycle's harvest entries."""
    labor_days: Decimal | None
    cost_cop: Decimal | None
    irrigation_mm: Decimal | None
    loss_kg: Decimal | None
    loss_cop: Decimal | None
    water_stress_days: int | None
    """Days inside the cycle with `depletion_mm > raw_mm`, i.e. `Ks < 1`
    (docs/11 §1). `None` when the cycle has no assimilated balance at all,
    which is missing evidence rather than a stress-free cycle."""


class MetricsSourceRepository(Protocol):
    """Read-only access to the metrics SQL views (docs/05 §Reglas, D-T0.1).

    Implemented by `adapters.source_repository.SqlAlchemyMetricsSourceRepository`
    over the views `migrations`' `metrics_*_…` revisions create. Every method
    takes `org_id` and filters on it.
    """

    async def node_month_readings(
        self, org_id: UUID, plot_id: UUID, *, month: date
    ) -> Sequence[NodeMonthReadings]:
        """Per node, its evidence for one calendar month (D-T0.4).

        A node claimed inside the month appears even with zero readings, so the
        denominator exists and a silent node reads as `0 %` instead of as "no
        evidence" (D-T0.3). `month` is the first day of the month.
        """
        ...

    async def logbook_weeks(
        self, org_id: UUID, plot_id: UUID, *, month: date
    ) -> Sequence[LogbookWeek]:
        """The ISO weeks of the month that hold at least one non-deleted entry
        (D-T0.3, docs/11 §2). A month with no entry returns no rows, never a
        row with `entry_count = 0`."""
        ...

    async def decision_days(
        self, org_id: UUID, plot_id: UUID, *, from_day: date, to_day: date
    ) -> Sequence[DecisionDay]:
        """Every stored recommendation in `[from_day, to_day]`, with that day's
        applied depth (D-T0.5). Days without a recommendation are absent: the
        denominator counts days that carried one."""
        ...

    async def plot_alert_actions(
        self, org_id: UUID, plot_id: UUID, *, from_day: date, to_day: date
    ) -> Sequence[PlotAlertAction]:
        """The plot alerts opened in `[from_day, to_day]` and whether each had a
        timely action (D-T0.6). Node alerts are never returned."""
        ...

    async def cycle_totals(self, org_id: UUID, crop_cycle_id: UUID) -> CycleTotals | None:
        """One cycle's raw totals (docs/11 §1).

        `None` when the cycle does not exist or belongs to another organization
        (docs/09 §Seguridad): the caller cannot tell the two apart.
        """
        ...
