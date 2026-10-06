"""Postgres repository for the enrollment survey (docs/09-cuellos-de-botella.md#seguridad).

`plot_baseline` has no `org_id` column in T1's migration beyond the one the
composite foreign key ties to `plot(id, org_id)`, so the read filters on both:
a plot id from another organization finds nothing (docs/04-api.md: cross-org
access responds 404, never 403).
"""

from __future__ import annotations

import decimal
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.metrics.adapters.orm import PlotBaselineRow
from techcamp.metrics.domain.models import IrrigationPractice, PlotBaseline


def _as_decimal(value: float | None) -> decimal.Decimal | None:
    """`Decimal(str(value))`, never `Decimal(value)`: a float's binary value is
    not its decimal one, so the direct constructor writes figures the farmer
    never typed (same guard as `irrigation/adapters/repositories.py:37`)."""
    return None if value is None else decimal.Decimal(str(value))


def _baseline_from_row(row: PlotBaselineRow) -> PlotBaseline:
    return PlotBaseline(
        plot_id=row.plot_id,
        org_id=row.org_id,
        enrolled_on=row.enrolled_on,
        crop_id=row.crop_id,
        last_yield_kg_ha=float(row.last_yield_kg_ha),
        last_cost_cop_ha=None if row.last_cost_cop_ha is None else float(row.last_cost_cop_ha),
        irrigation_practice=IrrigationPractice(row.irrigation_practice),
        recorded_by=row.recorded_by,
    )


def _for_plot(plot_id: UUID, org_id: UUID) -> Select[tuple[PlotBaselineRow]]:
    """The only read this repository needs: a plot's survey inside its own
    organization (docs/09-cuellos-de-botella.md#seguridad)."""
    return select(PlotBaselineRow).where(
        PlotBaselineRow.plot_id == plot_id,
        PlotBaselineRow.org_id == org_id,
    )


class SqlAlchemyBaselineRepository:
    """One survey per plot, so `plot_id` is the key: `PUT` is a full-document
    write that replaces the row (docs/04-api.md:52; D-T0.11), which the caller
    is allowed to do after crop cycles exist."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_org(self, plot_id: UUID, org_id: UUID) -> PlotBaseline | None:
        result = await self._session.execute(_for_plot(plot_id, org_id))
        row = result.scalar_one_or_none()
        return _baseline_from_row(row) if row is not None else None

    async def put(self, baseline: PlotBaseline) -> PlotBaseline:
        values: dict[str, object] = {
            "org_id": baseline.org_id,
            "enrolled_on": baseline.enrolled_on,
            "crop_id": baseline.crop_id,
            "last_yield_kg_ha": _as_decimal(baseline.last_yield_kg_ha),
            "last_cost_cop_ha": _as_decimal(baseline.last_cost_cop_ha),
            "irrigation_practice": baseline.irrigation_practice.value,
            "recorded_by": baseline.recorded_by,
        }
        stmt = insert(PlotBaselineRow).values(plot_id=baseline.plot_id, **values)
        stmt = stmt.on_conflict_do_update(index_elements=[PlotBaselineRow.plot_id], set_=values)
        await self._session.execute(stmt)
        await self._session.commit()
        # Read back what the database stored, never what the caller sent: a
        # `Numeric` column may hand back a value the caller never typed.
        result = await self._session.execute(_for_plot(baseline.plot_id, baseline.org_id))
        return _baseline_from_row(result.scalar_one())
