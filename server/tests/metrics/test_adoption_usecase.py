"""The monthly adoption-index use case over a fake read model (E11 T5).

The component math is proven pure in `test_adoption_domain.py`; what this module
adds is the wiring the database cannot check on its own:

- which view is asked for which component, and with which window;
- that a rainfed plot's `decision` is null because of the **plot**, not because
  the month happened to hold no recommendation;
- that `org_id` reaches every port, so the use case cannot ask another
  organization's evidence;
- that storing twice leaves the same row (D-T0.2), which is what makes the
  monthly job safe to re-run.

The read model and the store are fakes rather than a database: this is about the
use case's own decisions. `test_monthly_repository.py` proves the SQL against
the real database.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from techcamp.farms.domain.errors import PlotNotFoundError
from techcamp.farms.domain.models import IrrigationSystem, Plot
from techcamp.metrics.application.adoption import (
    compute_plot_month,
    get_latest_plot_month,
)
from techcamp.metrics.application.ports import (
    DecisionDay,
    LogbookWeek,
    NodeMonthReadings,
    PlotAlertAction,
)
from techcamp.metrics.domain.adoption import AdoptionComponents, PlotMonthlyMetric

pytestmark = pytest.mark.anyio

MONTH = date(2026, 9, 1)
ORG_ID = UUID("11111111-1111-1111-1111-111111111111")
OTHER_ORG_ID = UUID("22222222-2222-2222-2222-222222222222")
PLOT_ID = UUID("33333333-3333-3333-3333-333333333333")
FARM_ID = UUID("44444444-4444-4444-4444-444444444444")
COMPUTED_AT = datetime(2026, 10, 1, 7, 0)


def _plot(*, irrigation_system: IrrigationSystem = IrrigationSystem.DRIP) -> Plot:
    return Plot(
        id=PLOT_ID,
        org_id=ORG_ID,
        farm_id=FARM_ID,
        name="Parcela 1",
        boundary="POLYGON((0 0, 0 1, 1 1, 1 0, 0 0))",
        area_ha=1.0,
        weather_cell_id=None,
        irrigation_system=irrigation_system,
        irrigation_efficiency=None if irrigation_system is IrrigationSystem.NONE else 0.9,
        system_flow_lph=None,
    )


class FakeSources:
    """A `MetricsSourceRepository` that records every call.

    Recording the arguments is the point: `month` versus the `from_day`/`to_day`
    window is a distinction the use case has to get right, and a fake is the only
    place it is observable.
    """

    def __init__(
        self,
        *,
        nodes: list[NodeMonthReadings] | None = None,
        weeks: list[LogbookWeek] | None = None,
        days: list[DecisionDay] | None = None,
        alerts: list[PlotAlertAction] | None = None,
    ) -> None:
        self._nodes = nodes or []
        self._weeks = weeks or []
        self._days = days or []
        self._alerts = alerts or []
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def node_month_readings(
        self, org_id: UUID, plot_id: UUID, *, month: date
    ) -> list[NodeMonthReadings]:
        self.calls.append(("node_month_readings", (org_id, plot_id), {"month": month}))
        return self._nodes

    async def logbook_weeks(self, org_id: UUID, plot_id: UUID, *, month: date) -> list[LogbookWeek]:
        self.calls.append(("logbook_weeks", (org_id, plot_id), {"month": month}))
        return self._weeks

    async def decision_days(
        self, org_id: UUID, plot_id: UUID, *, from_day: date, to_day: date
    ) -> list[DecisionDay]:
        self.calls.append(
            ("decision_days", (org_id, plot_id), {"from_day": from_day, "to_day": to_day})
        )
        return self._days

    async def plot_alert_actions(
        self, org_id: UUID, plot_id: UUID, *, from_day: date, to_day: date
    ) -> list[PlotAlertAction]:
        self.calls.append(
            ("plot_alert_actions", (org_id, plot_id), {"from_day": from_day, "to_day": to_day})
        )
        return self._alerts


class FakePlots:
    """The plot the use case resolves, or nothing for a foreign org."""

    def __init__(self, plot: Plot | None) -> None:
        self._plot = plot

    async def get_for_orgs(self, plot_id: UUID, org_ids: list[UUID]) -> Plot | None:
        if self._plot is None or self._plot.id != plot_id:
            return None
        return self._plot if self._plot.org_id in org_ids else None


class FakeMetrics:
    """The `plot_metric_monthly` store, keyed exactly as the database is."""

    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, date], PlotMonthlyMetric] = {}
        self.upserts = 0

    async def upsert(self, metric: PlotMonthlyMetric) -> PlotMonthlyMetric:
        self.upserts += 1
        self.rows[(metric.plot_id, metric.month)] = metric
        return metric

    async def get(self, org_id: UUID, plot_id: UUID, *, month: date) -> PlotMonthlyMetric | None:
        row = self.rows.get((plot_id, month))
        return row if row is not None and row.org_id == org_id else None

    async def latest_for_plot(self, org_id: UUID, plot_id: UUID) -> PlotMonthlyMetric | None:
        rows = [
            row for row in self.rows.values() if row.plot_id == plot_id and row.org_id == org_id
        ]
        return max(rows, key=lambda row: row.month) if rows else None


async def _compute(
    sources: FakeSources,
    *,
    plot: Plot | None = None,
    metrics: FakeMetrics | None = None,
) -> PlotMonthlyMetric:
    store = metrics if metrics is not None else FakeMetrics()
    result = await compute_plot_month(
        org_id=ORG_ID,
        plot_id=PLOT_ID,
        month=MONTH,
        plots=FakePlots(plot if plot is not None else _plot()),
        sources=sources,
        metrics=store,
        computed_at=COMPUTED_AT,
    )
    return result


def _node_row(*, interval_s: int, claimed_seconds: int, received: int) -> NodeMonthReadings:
    return NodeMonthReadings(
        node_id=UUID("55555555-5555-5555-5555-555555555555"),
        plot_id=PLOT_ID,
        month=MONTH,
        interval_s=interval_s,
        claimed_seconds=claimed_seconds,
        received_readings=received,
    )


def _week_row(week_start: date) -> LogbookWeek:
    return LogbookWeek(plot_id=PLOT_ID, month=MONTH, week_start=week_start, entry_count=1)


def _day_row(day: date, kind: str, *, depth=None, irrigation=None) -> DecisionDay:
    return DecisionDay(
        plot_id=PLOT_ID, day=day, kind=kind, depth_mm=depth, irrigation_mm=irrigation
    )


def _alert_row(*, timely: bool) -> PlotAlertAction:
    return PlotAlertAction(
        plot_id=PLOT_ID,
        alert_id=UUID("66666666-6666-6666-6666-666666666666"),
        opened_at=datetime(2026, 9, 10, 8, 0),
        rule_code="water_stress",
        has_timely_action=timely,
    )


async def test_compute_stores_the_index_and_its_components() -> None:
    sources = FakeSources(
        nodes=[_node_row(interval_s=300, claimed_seconds=4320 * 300, received=4320)],
        weeks=[_week_row(date(2026, 9, 7))],
        days=[
            _day_row(date(2026, 9, 1), "irrigate", depth=Decimal("10"), irrigation=Decimal("10"))
        ],
        alerts=[_alert_row(timely=True)],
    )

    metric = await _compute(sources)

    assert metric.month == MONTH
    assert metric.components == AdoptionComponents(
        monitoring=Decimal(1),
        record_keeping=Decimal(1) / Decimal(5),
        decision=Decimal(1),
        risk_management=Decimal(1),
    )
    assert metric.digital_adoption_index is not None
    assert metric.computed_at == COMPUTED_AT


async def test_compute_reads_the_month_window_from_the_first_to_the_last_day() -> None:
    sources = FakeSources(days=[_day_row(date(2026, 9, 1), "postpone")])

    await _compute(sources)

    # 30 days: September's last day is the 30th, not the 1st plus 30 (D-T0.7).
    assert (
        "decision_days",
        (ORG_ID, PLOT_ID),
        {"from_day": MONTH, "to_day": date(2026, 9, 30)},
    ) in sources.calls
    assert (
        "plot_alert_actions",
        (ORG_ID, PLOT_ID),
        {"from_day": MONTH, "to_day": date(2026, 9, 30)},
    ) in sources.calls
    assert ("node_month_readings", (ORG_ID, PLOT_ID), {"month": MONTH}) in sources.calls
    assert ("logbook_weeks", (ORG_ID, PLOT_ID), {"month": MONTH}) in sources.calls


async def test_compute_of_a_february_window_ends_on_the_twenty_eighth() -> None:
    sources = FakeSources()
    february = date(2026, 2, 1)

    await compute_plot_month(
        org_id=ORG_ID,
        plot_id=PLOT_ID,
        month=february,
        plots=FakePlots(_plot()),
        sources=sources,
        metrics=FakeMetrics(),
        computed_at=COMPUTED_AT,
    )

    assert sources.calls[-1][2] == {"from_day": february, "to_day": date(2026, 2, 28)}


async def test_decision_is_null_for_a_rainfed_plot_even_with_recommendations() -> None:
    # docs/11:52: the component does not apply, so it must not be scored even
    # when the view happens to hold an irrigate day for the plot. The other
    # three weigh 100/3 each.
    sources = FakeSources(
        nodes=[_node_row(interval_s=300, claimed_seconds=4320 * 300, received=4320)],
        days=[
            _day_row(date(2026, 9, 1), "irrigate", depth=Decimal("10"), irrigation=Decimal("10"))
        ],
        alerts=[_alert_row(timely=True)],
    )

    metric = await _compute(sources, plot=_plot(irrigation_system=IrrigationSystem.NONE))

    assert metric.components.decision is None
    # monitoring 1, risk_management 1, record_keeping 0 (no logbook week).
    # `100 * 2 / 3` in that order: Decimal division is not associative, so
    # `(100 / 3) * 2` differs in the last of 28 significant digits.
    assert metric.digital_adoption_index == Decimal(100) * Decimal(2) / Decimal(3)


async def test_a_plot_with_no_evidence_scores_zero_through_record_keeping() -> None:
    # No node, no week, no alert and a rainfed plot. Three components are null,
    # and the index is still a number: "semanas del mes" always exists, so a
    # month with no entry is a measured 0 rather than a missing denominator
    # (docs/11:57, D-T0.3). The all-null index that docs/03:438 stores as null is
    # unreachable from a real plot for exactly this reason; the domain tests pin
    # its behavior.
    sources = FakeSources()

    metric = await _compute(sources, plot=_plot(irrigation_system=IrrigationSystem.NONE))

    assert metric.components.monitoring is None
    assert metric.components.decision is None
    assert metric.components.risk_management is None
    assert metric.components.record_keeping == Decimal(0)
    assert metric.digital_adoption_index == Decimal(0)


async def test_compute_of_another_organizations_plot_is_not_found() -> None:
    # The plot belongs to ORG_ID and the caller says OTHER_ORG_ID: the use case
    # must not compute against another org's evidence (docs/09 §Seguridad).
    sources = FakeSources()

    with pytest.raises(PlotNotFoundError):
        await compute_plot_month(
            org_id=OTHER_ORG_ID,
            plot_id=PLOT_ID,
            month=MONTH,
            plots=FakePlots(_plot()),
            sources=sources,
            metrics=FakeMetrics(),
            computed_at=COMPUTED_AT,
        )

    assert sources.calls == []


async def test_compute_passes_the_callers_org_to_every_read() -> None:
    sources = FakeSources(alerts=[_alert_row(timely=True)])

    await _compute(sources)

    assert sources.calls
    assert all(args[0] is ORG_ID for _, args, _ in sources.calls)


async def test_computing_the_same_month_twice_stores_one_row() -> None:
    # D-T0.2: the job writes with an upsert, so a re-run leaves the same row.
    sources = FakeSources(weeks=[_week_row(date(2026, 9, 7))])
    store = FakeMetrics()

    await _compute(sources, metrics=store)
    await _compute(sources, metrics=store)

    assert store.upserts == 2
    assert list(store.rows) == [(PLOT_ID, MONTH)]


async def test_get_latest_returns_the_most_recent_stored_month() -> None:
    store = FakeMetrics()
    for month in (date(2026, 7, 1), date(2026, 9, 1), date(2026, 8, 1)):
        store.rows[(PLOT_ID, month)] = PlotMonthlyMetric(
            plot_id=PLOT_ID,
            org_id=ORG_ID,
            month=month,
            components=AdoptionComponents(None, None, None, None),
            digital_adoption_index=None,
            computed_at=COMPUTED_AT,
        )

    latest = await get_latest_plot_month(org_id=ORG_ID, plot_id=PLOT_ID, metrics=store)

    assert latest is not None
    assert latest.month == date(2026, 9, 1)


async def test_get_latest_of_another_organization_is_none() -> None:
    store = FakeMetrics()
    store.rows[(PLOT_ID, MONTH)] = PlotMonthlyMetric(
        plot_id=PLOT_ID,
        org_id=ORG_ID,
        month=MONTH,
        components=AdoptionComponents(Decimal(1), Decimal(1), Decimal(1), Decimal(1)),
        digital_adoption_index=Decimal(100),
        computed_at=COMPUTED_AT,
    )

    assert await get_latest_plot_month(org_id=OTHER_ORG_ID, plot_id=PLOT_ID, metrics=store) is None


async def test_get_latest_of_a_plot_with_no_stored_month_is_none() -> None:
    # D-T0.13: no stored month is not an error and not an index of zero.
    assert (
        await get_latest_plot_month(org_id=ORG_ID, plot_id=PLOT_ID, metrics=FakeMetrics()) is None
    )
