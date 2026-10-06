"""The `crop_cycle_summary` store (docs/03-modelo-datos.md §`crop_cycle_summary`;
docs/09-cuellos-de-botella.md#seguridad).

One row per finished cycle, keyed by `crop_cycle_id` (D-T0.8): the monthly job
runs over the same closed cycle on every run, so the write is an upsert keyed by
that cycle and never an append (docs/03-modelo-datos.md:442).

Every read filters on `org_id`: an impact row is a tenant's own figure, and the
org id is a column of the table rather than something inferred from the cycle
(docs/03-modelo-datos.md:439).

The adapter's own job ends at the column boundary. `Decimal` in, `Decimal` out —
no rounding, no unit change, no default for a `null` metric: docs/11 §1 states no
precision for these ratios, and inventing one here would silently disagree with
the arithmetic the domain performed.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.metrics.adapters.orm import CropCycleSummaryRow
from techcamp.metrics.domain.cycle_summary import CycleSummary

_METRIC_FIELDS: tuple[str, ...] = (
    "yield_kg_ha",
    "yield_change_vs_baseline",
    "relative_yield",
    "water_applied_m3_ha",
    "irrigation_wue_kg_m3",
    "water_stress_days",
    "cost_cop_ha",
    "cost_cop_kg",
    "yield_kg_per_labor_day",
    "gross_margin_cop",
    "loss_kg",
    "loss_cop",
)


def _summary_from_row(row: CropCycleSummaryRow) -> CycleSummary:
    """The stored row as a domain value.

    `Numeric` columns come back as `Decimal` and `water_stress_days` as the `int`
    its `Integer` column stores, which are exactly the types the domain uses, so
    this is a name mapping and nothing else: no rounding, no conversion.
    """
    values: dict[str, Any] = {"crop_cycle_id": row.crop_cycle_id, "plot_id": row.plot_id}
    values.update({name: getattr(row, name) for name in _METRIC_FIELDS})
    values["org_id"] = row.org_id
    values["computed_at"] = row.computed_at
    return CycleSummary(**values)


def _for_cycle(crop_cycle_id: UUID, org_id: UUID) -> Select[tuple[CropCycleSummaryRow]]:
    """The only read this repository needs: a cycle's impact inside its own
    organization (docs/09-cuellos-de-botella.md#seguridad)."""
    return select(CropCycleSummaryRow).where(
        CropCycleSummaryRow.crop_cycle_id == crop_cycle_id,
        CropCycleSummaryRow.org_id == org_id,
    )


class SqlAlchemyCycleSummaryRepository:
    """The impact of finished cycles, upserted by cycle
    (docs/03-modelo-datos.md §`crop_cycle_summary`, D-T0.8)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_org(self, crop_cycle_id: UUID, org_id: UUID) -> CycleSummary | None:
        """The stored impact of a finished cycle, or `None`.

        `None` means either the cycle has no row — it is still active, so its
        summary is computed on read (D-T0.8) — or the row belongs to another
        organization, which the caller cannot tell apart
        (docs/09-cuellos-de-botella.md#seguridad).
        """
        result = await self._session.execute(_for_cycle(crop_cycle_id, org_id))
        row = result.scalar_one_or_none()
        return _summary_from_row(row) if row is not None else None

    async def upsert(self, summary: CycleSummary) -> CycleSummary:
        """Insert or replace the cycle's row and return the stored one.

        Idempotent by `crop_cycle_id` (docs/03-modelo-datos.md:442), so the
        monthly job may run twice over the same closed cycle.
        """
        values: dict[str, object] = {
            "plot_id": summary.plot_id,
            "org_id": summary.org_id,
            "computed_at": summary.computed_at,
        }
        for name in _METRIC_FIELDS:
            values[name] = getattr(summary, name)
        stmt = insert(CropCycleSummaryRow).values(crop_cycle_id=summary.crop_cycle_id, **values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[CropCycleSummaryRow.crop_cycle_id], set_=values
        )
        await self._session.execute(stmt)
        await self._session.commit()
        # Read back what the database stored, never what the caller sent: a
        # `Numeric` column may hand back a value the caller never typed (same
        # reason `SqlAlchemyBaselineRepository.put` does).
        result = await self._session.execute(_for_cycle(summary.crop_cycle_id, summary.org_id))
        return _summary_from_row(result.scalar_one())
