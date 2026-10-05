"""Postgres store for `plot_metric_monthly` (E11 T5, D-T0.2; docs/03-modelo-datos.md:432-443).

Writes are an upsert on `(plot_id, month)` rather than an insert, because the
monthly job must be safe to re-run: docs/03:442 states that running it twice
leaves the same result, and a partial failure that re-processes one plot cannot
distinguish "not written yet" from "written last month".

`Numeric` has no scale, so the fractions arrive and leave unquantized (the
domain decided the same, and no document fixes a precision).

Every read filters on `org_id` (docs/09-cuellos-de-botella.md#seguridad): the
column is in the table precisely so a metric row cannot be read across
organizations even when the plot id is known.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.metrics.adapters.orm import PlotMetricMonthlyRow
from techcamp.metrics.domain.adoption import AdoptionComponents, PlotMonthlyMetric
from techcamp.metrics.domain.errors import PlotMonthOwnedByAnotherOrganizationError


def _metric_from_row(row: PlotMetricMonthlyRow) -> PlotMonthlyMetric:
    return PlotMonthlyMetric(
        plot_id=row.plot_id,
        org_id=row.org_id,
        month=row.month,
        components=AdoptionComponents(
            monitoring=row.monitoring,
            record_keeping=row.record_keeping,
            decision=row.decision,
            risk_management=row.risk_management,
        ),
        digital_adoption_index=row.digital_adoption_index,
        computed_at=row.computed_at,
    )


def _for_plot(org_id: UUID, plot_id: UUID) -> Select[tuple[PlotMetricMonthlyRow]]:
    """Every stored month of one plot inside its own organization.

    `org_id` is part of the filter, never assumed from the caller
    (docs/09-cuellos-de-botella.md#seguridad).
    """
    return select(PlotMetricMonthlyRow).where(
        PlotMetricMonthlyRow.plot_id == plot_id,
        PlotMetricMonthlyRow.org_id == org_id,
    )


class SqlAlchemyMonthlyMetricRepository:
    """The precalculated monthly metrics of a plot (docs/03:438)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, metric: PlotMonthlyMetric) -> PlotMonthlyMetric:
        components = metric.components
        values: dict[str, object] = {
            "org_id": metric.org_id,
            "monitoring": components.monitoring,
            "record_keeping": components.record_keeping,
            "decision": components.decision,
            "risk_management": components.risk_management,
            "digital_adoption_index": metric.digital_adoption_index,
            "computed_at": metric.computed_at,
        }
        key = [PlotMetricMonthlyRow.plot_id, PlotMetricMonthlyRow.month]
        stmt = insert(PlotMetricMonthlyRow).values(
            plot_id=metric.plot_id, month=metric.month, **values
        )
        # Ownership never moves. `org_id` is not part of the conflict update, and
        # a month that already belongs to another organization is left as it is
        # rather than adopted, so a conflicting upsert cannot transfer the row
        # across tenants (docs/09-cuellos-de-botella.md#seguridad).
        stmt = stmt.on_conflict_do_update(
            index_elements=key,
            set_={column: value for column, value in values.items() if column != "org_id"},
            where=PlotMetricMonthlyRow.org_id == metric.org_id,
        )
        await self._session.execute(stmt)
        await self._session.commit()
        # Read back what the database stored, never what the caller sent: a
        # `Numeric` column may hand back a value the caller never computed.
        result = await self._session.execute(
            _for_plot(metric.org_id, metric.plot_id).where(
                PlotMetricMonthlyRow.month == metric.month
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            # The conflict update was skipped above, so this upsert stored
            # nothing: the month belongs to another organization.
            raise PlotMonthOwnedByAnotherOrganizationError(metric.plot_id, metric.month)
        return _metric_from_row(row)

    async def get(self, org_id: UUID, plot_id: UUID, *, month: date) -> PlotMonthlyMetric | None:
        row = (
            await self._session.execute(
                _for_plot(org_id, plot_id).where(PlotMetricMonthlyRow.month == month)
            )
        ).scalar_one_or_none()
        return _metric_from_row(row) if row is not None else None

    async def latest_for_plot(self, org_id: UUID, plot_id: UUID) -> PlotMonthlyMetric | None:
        """The plot's most recent stored month, or `None`.

        Ordered by `month` descending rather than by `computed_at`: a month is
        identified by its bucket, and a re-run of an older month must not become
        the latest one (D-T0.13).
        """
        row = (
            await self._session.execute(
                _for_plot(org_id, plot_id).order_by(PlotMetricMonthlyRow.month.desc()).limit(1)
            )
        ).scalar_one_or_none()
        return _metric_from_row(row) if row is not None else None

    async def list_for_org_month(self, org_id: UUID, *, month: date) -> list[PlotMonthlyMetric]:
        """Every plot's row for one month, oldest plot first (D-T0.12).

        The listing behind `OrgMetrics`, which averages the index over the plots
        that have one. Read here rather than in the index's own use case because
        T7 owns that endpoint's shape; this lane only stores what it will read.
        """
        result = await self._session.execute(
            select(PlotMetricMonthlyRow)
            .where(
                PlotMetricMonthlyRow.org_id == org_id,
                PlotMetricMonthlyRow.month == month,
            )
            .order_by(PlotMetricMonthlyRow.plot_id)
        )
        # `Result.scalars()`, not iterating the Result: iterating rows yields
        # Row tuples whose `row.plot_id` raises, which is what `row["plot_id"]`
        # would have to be written instead.
        return [_metric_from_row(row) for row in result.scalars()]
