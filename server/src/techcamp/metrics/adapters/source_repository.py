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
    Boolean,
    Column,
    Date,
    DateTime,
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
    CycleTotals,
    DecisionDay,
    LogbookWeek,
    NodeMonthReadings,
    PlotAlertAction,
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

PlotAlertActionView = _view(
    "metrics_plot_alert_action",
    Column("org_id", Uuid),
    Column("plot_id", Uuid),
    Column("alert_id", Uuid),
    Column("opened_at", DateTime(timezone=True)),
    Column("opened_day", Date),
    Column("rule_code", Text),
    Column("has_timely_action", Boolean),
)

CropCycleTotalsView = _view(
    "metrics_crop_cycle_totals",
    Column("org_id", Uuid),
    Column("crop_cycle_id", Uuid),
    Column("plot_id", Uuid),
    Column("yield_kg", Numeric),
    Column("sold_kg", Numeric),
    Column("revenue_cop", Numeric),
    Column("labor_days", Numeric),
    Column("cost_cop", Numeric),
    Column("irrigation_mm", Numeric),
    Column("loss_kg", Numeric),
    Column("loss_cop", Numeric),
    Column("water_stress_days", BigInteger),
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

    async def plot_alert_actions(
        self, org_id: UUID, plot_id: UUID, *, from_day: date, to_day: date
    ) -> list[PlotAlertAction]:
        """The plot alerts opened in `[from_day, to_day]` and whether each had a
        timely action (D-T0.6), oldest first.

        The window is the logbook's grain: `occurred_on` is a date, so "timely"
        runs from the local day of `opened_at` to the local day of
        `opened_at + 48 h`. Node alerts are never returned — they go to the
        technician (docs/11 §2). The range filters the view's `opened_day`, the
        local calendar day, and never `opened_at`: an instant compared against a
        bare date would be read in the session's zone and move the month by five
        hours under UTC (D-T0.7).
        """
        stmt = (
            select(
                PlotAlertActionView.c.plot_id,
                PlotAlertActionView.c.alert_id,
                PlotAlertActionView.c.opened_at,
                PlotAlertActionView.c.rule_code,
                PlotAlertActionView.c.has_timely_action,
            )
            .where(
                PlotAlertActionView.c.org_id == org_id,
                PlotAlertActionView.c.plot_id == plot_id,
                PlotAlertActionView.c.opened_day >= from_day,
                PlotAlertActionView.c.opened_day <= to_day,
            )
            .order_by(PlotAlertActionView.c.opened_at)
        )
        result = await self._session.execute(stmt)
        return [
            PlotAlertAction(
                plot_id=row.plot_id,
                alert_id=row.alert_id,
                opened_at=row.opened_at,
                rule_code=row.rule_code,
                has_timely_action=row.has_timely_action,
            )
            for row in result
        ]

    async def cycle_totals(self, org_id: UUID, crop_cycle_id: UUID) -> CycleTotals | None:
        """One cycle's raw totals, before any per-hectare or per-kilogram
        division (docs/11 §1).

        `None` when the cycle does not exist or belongs to another organization
        (docs/09 §Seguridad): the caller cannot tell the two apart, and neither
        may leak the other's existence.

        `water_stress_days` covers the days from `sown_on` to
        `LEAST(expected_harvest_on, today)`. `crop_cycle` carries no end date to
        join on, so that window is the view's own documented choice and not a
        foreign key: the future is never inside a cycle, which makes it right for
        an active cycle measured on read (D-T0.8) and for a finished one measured
        up to the harvest date it carries. It is `None` when the cycle has no
        assimilated balance at all, and 0 when the window holds balance days that
        are simply never stressed.
        """
        stmt = select(CropCycleTotalsView).where(
            CropCycleTotalsView.c.org_id == org_id,
            CropCycleTotalsView.c.crop_cycle_id == crop_cycle_id,
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            return None
        return CycleTotals(
            crop_cycle_id=row.crop_cycle_id,
            plot_id=row.plot_id,
            yield_kg=row.yield_kg,
            sold_kg=row.sold_kg,
            revenue_cop=row.revenue_cop,
            labor_days=row.labor_days,
            cost_cop=row.cost_cop,
            irrigation_mm=row.irrigation_mm,
            loss_kg=row.loss_kg,
            loss_cop=row.loss_cop,
            water_stress_days=row.water_stress_days,
        )
