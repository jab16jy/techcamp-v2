"""Read-only adapter over the metrics SQL views (E11 T3, D-T0.1).

`metrics` is the one module allowed to read another module's data through SQL
views instead of a facade query (docs/05-arquitectura.md §Reglas), and this is
the only code that touches them. The views are created by the `metrics_*`
migrations and are declared below as plain Core `Table`s: they are never
written, never mapped to an ORM entity, and they live in their own `MetaData`
so they never reach `Base.metadata`, where Alembic autogenerate and
`Base.metadata.create_all` would treat them as tables this application owns.

Every method takes `org_id` and filters on it (docs/09-cuellos-de-botella.md
§Seguridad), so another organization's row is absent rather than readable.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
    Uuid,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.metrics.application.ports import (
    DecisionDay,
    LogbookWeek,
    NodeMonthReadings,
)

_view_metadata = MetaData()


def _view(name: str, *columns: Column[Any]) -> Table:
    return Table(name, _view_metadata, *columns)


NodeMonthReadingsView = _view(
    "metrics_node_month_readings",
    Column("org_id", Uuid),
    Column("plot_id", Uuid),
    Column("node_id", Uuid),
    Column("month", Date),
    Column("interval_s", Integer),
    Column("claimed_seconds", BigInteger),
    Column("received_readings", BigInteger),
)

PlotMonthLogbookView = _view(
    "metrics_plot_month_logbook",
    Column("org_id", Uuid),
    Column("plot_id", Uuid),
    Column("month", Date),
    Column("week_start", Date),
    Column("entry_count", BigInteger),
)

PlotDayDecisionView = _view(
    "metrics_plot_day_decision",
    Column("org_id", Uuid),
    Column("plot_id", Uuid),
    Column("day", Date),
    Column("kind", Text),
    Column("depth_mm", Numeric),
    Column("irrigation_mm", Numeric),
)


class SqlAlchemyMetricsSourceRepository:
    """Implements `MetricsSourceRepository` over the read-only views."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def node_month_readings(
        self, org_id: UUID, plot_id: UUID, *, month: date
    ) -> list[NodeMonthReadings]:
        """Every claimed node of the plot with its evidence for `month`, ordered
        by node so a re-run reads the same sequence (D-T0.4).

        A node claimed inside the month is present even with no reading at all:
        the denominator has to exist for a silent node to read as 0 % rather
        than as "no evidence" (D-T0.3).
        """
        stmt = (
            select(
                NodeMonthReadingsView.c.node_id,
                NodeMonthReadingsView.c.plot_id,
                NodeMonthReadingsView.c.month,
                NodeMonthReadingsView.c.interval_s,
                NodeMonthReadingsView.c.claimed_seconds,
                NodeMonthReadingsView.c.received_readings,
            )
            .where(
                NodeMonthReadingsView.c.org_id == org_id,
                NodeMonthReadingsView.c.plot_id == plot_id,
                NodeMonthReadingsView.c.month == month,
            )
            .order_by(NodeMonthReadingsView.c.node_id)
        )
        result = await self._session.execute(stmt)
        return [
            NodeMonthReadings(
                node_id=row.node_id,
                plot_id=row.plot_id,
                month=row.month,
                interval_s=row.interval_s,
                claimed_seconds=row.claimed_seconds,
                received_readings=row.received_readings,
            )
            for row in result
        ]

    async def logbook_weeks(self, org_id: UUID, plot_id: UUID, *, month: date) -> list[LogbookWeek]:
        """The ISO weeks of `month` holding at least one entry, oldest first
        (D-T0.3).

        The count of rows is the `record_keeping` numerator. "Semanas del mes",
        the denominator, is the same calendar counted over the whole month and
        is calendar math rather than evidence, so it is not repeated here
        (docs/11 §2).
        """
        stmt = (
            select(
                PlotMonthLogbookView.c.plot_id,
                PlotMonthLogbookView.c.month,
                PlotMonthLogbookView.c.week_start,
                PlotMonthLogbookView.c.entry_count,
            )
            .where(
                PlotMonthLogbookView.c.org_id == org_id,
                PlotMonthLogbookView.c.plot_id == plot_id,
                PlotMonthLogbookView.c.month == month,
            )
            .order_by(PlotMonthLogbookView.c.week_start)
        )
        result = await self._session.execute(stmt)
        return [
            LogbookWeek(
                plot_id=row.plot_id,
                month=row.month,
                week_start=row.week_start,
                entry_count=row.entry_count,
            )
            for row in result
        ]

    async def decision_days(
        self, org_id: UUID, plot_id: UUID, *, from_day: date, to_day: date
    ) -> list[DecisionDay]:
        """Every stored recommendation of the plot in `[from_day, to_day]`,
        paired with the depth applied that day (D-T0.5).

        The kinds docs/11 §2 does not count (`rainfed`, `no_kc`) are returned
        too: the view reports evidence, and T5's domain code decides what the
        denominator counts.
        """
        stmt = (
            select(
                PlotDayDecisionView.c.plot_id,
                PlotDayDecisionView.c.day,
                PlotDayDecisionView.c.kind,
                PlotDayDecisionView.c.depth_mm,
                PlotDayDecisionView.c.irrigation_mm,
            )
            .where(
                PlotDayDecisionView.c.org_id == org_id,
                PlotDayDecisionView.c.plot_id == plot_id,
                PlotDayDecisionView.c.day >= from_day,
                PlotDayDecisionView.c.day <= to_day,
            )
            .order_by(PlotDayDecisionView.c.day)
        )
        result = await self._session.execute(stmt)
        return [
            DecisionDay(
                plot_id=row.plot_id,
                day=row.day,
                kind=row.kind,
                depth_mm=row.depth_mm,
                irrigation_mm=row.irrigation_mm,
            )
            for row in result
        ]
