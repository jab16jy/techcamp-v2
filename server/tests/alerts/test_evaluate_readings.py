"""Reading-threshold rules evaluated in the ingestor (docs/06 §3, §1; D1, D9, D16).

Real Postgres, no doubles: the evaluator is composed exactly as
`techcamp/ingestor.py` composes it (one session for the whole unit of work), and
what matters is the alert the stored readings of the window produce.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.evaluate_readings import build_evaluator
from techcamp.alerts.adapters.orm import AlertRow, AlertRuleRow
from techcamp.alerts.adapters.repositories import (
    SqlAlchemyAlertRepository,
    SqlAlchemyAlertRuleRepository,
)
from techcamp.alerts.application import evaluate_landed_readings
from techcamp.alerts.domain import AlertRule, AlertState, Severity
from techcamp.farms.adapters.orm import FarmRow, PlotRow, SoilProfileRow
from techcamp.farms.adapters.repositories import (
    SqlAlchemyPlotRepository,
    SqlAlchemySoilProfileRepository,
)
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.domain.models import Role
from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.orm import NodeRow, SensorRow
from techcamp.telemetry.adapters.repositories import (
    SqlAlchemyNodeRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemySensorRepository,
)
from techcamp.telemetry.application.ingest_uplinks import AfterFlush
from techcamp.telemetry.domain.models import ReadingEvent, ReadingRecord, Sensor

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)
_START = datetime(2026, 9, 26, 15, 0, tzinfo=UTC)  # 10:00 Bogotá, outside quiet hours
_STEP = timedelta(minutes=15)
"""The node reports every `interval_s = 300`, and `max_gap` is 3 × that, so a
15-minute series is consecutive evidence (`sustained_run`, docs/06 §3)."""


@dataclass(frozen=True, slots=True)
class Plot:
    org_id: object
    farm_id: object
    plot_id: object
    node_id: object
    sensor_id: int


async def _make_plot(
    db_session: AsyncSession,
    *,
    metric: str = "air_temp",
    field_capacity_pct: float | None = None,
    with_member: bool = True,
) -> Plot:
    org_id, farm_id, plot_id, node_id = uuid7(), uuid7(), uuid7(), uuid7()
    if with_member:
        user_id = uuid7()
        db_session.add(
            AppUserRow(
                id=user_id,
                phone=f"+57{uuid7().int % 10**13:013d}",
                full_name="Producer",
            )
        )
        await db_session.commit()
    db_session.add(OrganizationRow(id=org_id, name="Test Org", kind="individual"))
    await db_session.commit()
    if with_member:
        db_session.add(MembershipRow(org_id=org_id, user_id=user_id, role=Role.PRODUCER.value))
    db_session.add(
        FarmRow(
            id=farm_id,
            org_id=org_id,
            name="Finca Principal",
            municipality_code="47001",
            location=_POINT,
        )
    )
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=_BOUNDARY,
            irrigation_system="drip",
        )
    )
    if field_capacity_pct is not None:
        db_session.add(
            SoilProfileRow(
                plot_id=plot_id,
                source="lab",
                field_capacity_pct=field_capacity_pct,
                wilting_point_pct=10.0,
            )
        )
    db_session.add(
        NodeRow(
            id=node_id,
            org_id=org_id,
            plot_id=plot_id,
            transport="wifi",
            claim_code=f"claim-{uuid7().hex}",
            credential_hash="hash",
            interval_s=300,
            claimed_at=_START,
            status="online",
        )
    )
    sensor = SensorRow(node_id=node_id, channel_key=f"{metric}_0", metric=metric, unit="pct")
    db_session.add(sensor)
    await db_session.commit()
    await db_session.refresh(sensor)
    return Plot(org_id, farm_id, plot_id, node_id, sensor.id)


async def _store_series(
    db_session: AsyncSession,
    plot: Plot,
    *,
    end: datetime,
    values: list[float],
    qualities: dict[int, int] | None = None,
) -> datetime:
    """A backdated series of readings, one every `_STEP` up to `end` (D1: the
    evaluation is stateless over the stored readings, so the window is read back
    from `reading`, not carried in memory). `qualities` overrides the `quality`
    flag of the sample at that index (docs/03 `reading.quality`)."""
    start = end - _STEP * (len(values) - 1)
    records = [
        ReadingRecord(
            sensor_id=plot.sensor_id,
            time=start + _STEP * index,
            raw_value=value,
            value=value,
            received_at=start + _STEP * index,
            quality=(qualities or {}).get(index, 0),
        )
        for index, value in enumerate(values)
    ]
    await SqlAlchemyReadingRepository(db_session).insert_batch(records)
    return end


async def _landed(db_session: AsyncSession, plot: Plot, *, at: datetime, metric: str) -> AfterFlush:
    """The hook the ingestor awaits with the readings of the batch (D9)."""
    evaluator = build_evaluator(db_session)
    await evaluator([_event(plot, at=at, metric=metric)])
    return evaluator


def _event(plot: Plot, *, at: datetime, metric: str) -> ReadingEvent:
    return ReadingEvent(
        org_id=plot.org_id,
        farm_id=plot.farm_id,
        plot_id=plot.plot_id,
        metric=metric,
        value=0.0,
        at=at,
    )


async def _alerts(db_session: AsyncSession, plot: Plot) -> list[tuple[str, str, datetime | None]]:
    """`(rule_code, state, resolved_at)` of the plot's alerts, by code."""
    result = await db_session.execute(
        select(AlertRuleRow.code, AlertRow.state, AlertRow.resolved_at)
        .join(AlertRow, AlertRow.rule_id == AlertRuleRow.id)
        .where(AlertRow.plot_id == plot.plot_id)
        .order_by(AlertRuleRow.code)
    )
    return [(code, state, resolved_at) for code, state, resolved_at in result]


async def _rule(
    db_session: AsyncSession, *, org_id: object, code: str, metric: str, operator: str
) -> AlertRule:
    row = (
        await db_session.execute(select(AlertRuleRow).where(AlertRuleRow.code == code))
    ).scalar_one()
    assert row.org_id == org_id
    return AlertRule(
        code=row.code,
        id=row.id,
        org_id=row.org_id,
        metric=row.metric,
        operator=row.operator,
        threshold=float(row.threshold) if row.threshold is not None else None,
        hysteresis=float(row.hysteresis),
        min_duration=timedelta(minutes=row.min_duration_min),
        severity=Severity(row.severity),
    )


class _SensorsFailingForOneNode(SqlAlchemySensorRepository):
    """The real sensor read, except that it raises for one node.

    The failure is injected at a port the evaluator already uses, so the batch
    is otherwise exactly what the ingestor hands the hook.
    """

    def __init__(self, session: AsyncSession, *, failing_node_id: UUID) -> None:
        super().__init__(session)
        self._failing_node_id = failing_node_id

    async def list_for_node(self, node_id: UUID, org_id: UUID) -> list[Sensor]:
        if node_id == self._failing_node_id:
            raise RuntimeError("sensor read failed")
        return await super().list_for_node(node_id, org_id)


# -- heat_stress: air_temp > 35 °C sustained 3 h (docs/06 §3) --


async def test_a_heat_run_of_three_hours_opens_one_heat_stress_alert(
    db_session: AsyncSession,
) -> None:
    plot = await _make_plot(db_session, metric="air_temp")
    # 13 readings every 15 min = exactly the 180 min the rule asks for.
    at = await _store_series(db_session, plot, end=_START, values=[38.0] * 13)

    await _landed(db_session, plot, at=at, metric="air_temp")

    alerts = await _alerts(db_session, plot)
    assert [(code, state) for code, state, _ in alerts] == [("heat_stress", AlertState.OPEN)]
    assert alerts[0][2] is None


async def test_a_heat_run_shorter_than_the_minimum_duration_opens_nothing(
    db_session: AsyncSession,
) -> None:
    plot = await _make_plot(db_session, metric="air_temp")
    # 7 readings = 90 min: the `Pending` state is not persisted as an alert.
    at = await _store_series(db_session, plot, end=_START, values=[38.0] * 7)

    await _landed(db_session, plot, at=at, metric="air_temp")

    assert await _alerts(db_session, plot) == []


# -- waterlogging: field capacity + 5 sustained 24 h (docs/06 §3; ADR-0022) --


async def test_a_saturated_soil_run_of_24_hours_opens_a_waterlogging_alert(
    db_session: AsyncSession,
) -> None:
    plot = await _make_plot(db_session, metric="soil_moisture", field_capacity_pct=30.0)
    # θFC + 5 = 35 %; `water_stress` reads the same metric but has no threshold
    # yet (T10), so exactly one alert opens.
    at = await _store_series(db_session, plot, end=_START, values=[38.0] * 97)

    await _landed(db_session, plot, at=at, metric="soil_moisture")

    alerts = await _alerts(db_session, plot)
    assert [(code, state) for code, state, _ in alerts] == [("waterlogging", AlertState.OPEN)]


async def test_a_plot_without_a_soil_profile_evaluates_no_waterlogging(
    db_session: AsyncSession,
) -> None:
    plot = await _make_plot(db_session, metric="soil_moisture")
    at = await _store_series(db_session, plot, end=_START, values=[38.0] * 97)

    await _landed(db_session, plot, at=at, metric="soil_moisture")

    assert await _alerts(db_session, plot) == []
    # `get_for_plot` is the port the evaluator uses; the row is simply absent.
    assert await SqlAlchemySoilProfileRepository(db_session).get_for_plot(plot.plot_id) is None


# -- the rules are data: one org's own threshold rule, and no other org's --


async def test_an_org_threshold_rule_opens_on_its_own_metric_and_operator(
    db_session: AsyncSession,
) -> None:
    plot = await _make_plot(db_session, metric="soil_moisture")
    await SqlAlchemyAlertRuleRepository(db_session).create(
        AlertRule(
            code="custom_humidity",
            id=uuid7(),
            org_id=plot.org_id,
            metric="soil_moisture",
            operator=">",
            threshold=20.0,
            min_duration=timedelta(0),
            severity=Severity.WARNING,
        )
    )
    at = await _store_series(db_session, plot, end=_START, values=[25.0])

    await _landed(db_session, plot, at=at, metric="soil_moisture")

    alerts = await _alerts(db_session, plot)
    assert [(code, state) for code, state, _ in alerts] == [("custom_humidity", AlertState.OPEN)]


async def test_another_orgs_rule_never_opens_for_this_plot(
    db_session: AsyncSession,
) -> None:
    plot = await _make_plot(db_session, metric="air_temp")
    other_org_id = uuid7()
    db_session.add(OrganizationRow(id=other_org_id, name="Other Org", kind="individual"))
    await db_session.commit()
    await SqlAlchemyAlertRuleRepository(db_session).create(
        AlertRule(
            code="foreign_hot",
            id=uuid7(),
            org_id=other_org_id,
            metric="air_temp",
            operator=">",
            threshold=30.0,
            min_duration=timedelta(0),
            severity=Severity.CRITICAL,
        )
    )
    at = await _store_series(db_session, plot, end=_START, values=[38.0] * 13)

    await _landed(db_session, plot, at=at, metric="air_temp")

    alerts = await _alerts(db_session, plot)
    assert [code for code, _, _ in alerts] == ["heat_stress"]
    assert all(state == AlertState.OPEN for _, state, _ in alerts)


# -- only the "Umbral sobre lecturas" row of docs/06 §3 is decided here (D17) --


async def test_the_rules_of_the_other_docs_sources_never_open_from_the_ingestor(
    db_session: AsyncSession,
) -> None:
    # `fungal_risk` also needs a 20-30 °C mean, `node_battery_low` reads a node
    # column and `heavy_rain_forecast` the weather cell: none of them is a plot
    # reading threshold, so a window full of qualifying readings opens nothing.
    for metric, value in (("air_rh", 90.0), ("battery_v", 3.0), ("rain", 60.0)):
        plot = await _make_plot(db_session, metric=metric)
        at = await _store_series(db_session, plot, end=_START, values=[value] * 61)

        await _landed(db_session, plot, at=at, metric=metric)

        assert await _alerts(db_session, plot) == []


# -- one batch, two plots: each is decided at its own newest reading (R3) --


async def test_a_batch_decides_each_plot_at_its_own_newest_reading(
    db_session: AsyncSession,
) -> None:
    fresh = await _make_plot(db_session, metric="air_temp")
    stale = await _make_plot(db_session, metric="air_temp")
    fresh_at = await _store_series(db_session, fresh, end=_START, values=[38.0] * 13)
    # The other plot's readings are 3 h older: judged at the batch's newest
    # reading, its series is stale (`sustained_run` drops it) and it would wait
    # for the next batch instead of opening now.
    stale_at = await _store_series(
        db_session, stale, end=_START - timedelta(hours=3), values=[38.0] * 13
    )

    evaluator = build_evaluator(db_session)
    await evaluator(
        [
            _event(fresh, at=fresh_at, metric="air_temp"),
            _event(stale, at=stale_at, metric="air_temp"),
        ]
    )

    assert [(code, state) for code, state, _ in await _alerts(db_session, fresh)] == [
        ("heat_stress", AlertState.OPEN)
    ]
    assert [(code, state) for code, state, _ in await _alerts(db_session, stale)] == [
        ("heat_stress", AlertState.OPEN)
    ]


# -- one plot's failure is isolated: the plots after it are still decided --


async def test_a_plot_whose_evaluation_raises_does_not_silence_the_next_plot(
    db_session: AsyncSession,
) -> None:
    failing = await _make_plot(db_session, metric="air_temp")
    following = await _make_plot(db_session, metric="air_temp")
    # Both plots carry the same 3 h heat run, so the only reason the first one
    # opens nothing is the failure injected at its sensor read.
    failing_at = await _store_series(db_session, failing, end=_START, values=[38.0] * 13)
    following_at = await _store_series(db_session, following, end=_START, values=[38.0] * 13)

    # The hook is awaited inside the ingestor's flush: an exception here would
    # re-queue the whole batch and fail identically on every attempt (a poison
    # batch), silencing every plot that shares it. The evaluation of each plot is
    # isolated, so this call must not raise.
    await evaluate_landed_readings(
        events=[
            _event(failing, at=failing_at, metric="air_temp"),
            _event(following, at=following_at, metric="air_temp"),
        ],
        rules=SqlAlchemyAlertRuleRepository(db_session),
        readings=SqlAlchemyReadingRepository(db_session),
        sensors=_SensorsFailingForOneNode(db_session, failing_node_id=failing.node_id),
        nodes=SqlAlchemyNodeRepository(db_session),
        plots=SqlAlchemyPlotRepository(db_session),
        soils=SqlAlchemySoilProfileRepository(db_session),
        alerts=SqlAlchemyAlertRepository(db_session),
    )

    # The negative assertion too: silence is the dangerous failure here, so the
    # plot that failed must show no alert AND the next one must show its own.
    assert await _alerts(db_session, failing) == []
    assert [(code, state) for code, state, _ in await _alerts(db_session, following)] == [
        ("heat_stress", AlertState.OPEN)
    ]


# -- a reading outside the physical range does not trigger alerts (docs/06 §1) --


async def test_only_the_out_of_range_flag_is_kept_out_of_the_window(
    db_session: AsyncSession,
) -> None:
    plot = await _make_plot(db_session, metric="air_temp")
    # The run reaches the 180 min the rule asks for only because of the last
    # sample, and that one is out of the physical range (`quality` flag 2,
    # docs/06 §1 "no dispara alertas"), so nothing opens.
    at = await _store_series(
        db_session, plot, end=_START, values=[38.0] * 12 + [150.0], qualities={12: 2}
    )

    await _landed(db_session, plot, at=at, metric="air_temp")

    assert await _alerts(db_session, plot) == []

    # A `quality` flag 1 (a `ts` corrected by `received_at`) is valid evidence:
    # the same 3 h run opens, so the flags are read bit by bit and not as a
    # whole.
    later = _START + timedelta(hours=6)
    at = await _store_series(db_session, plot, end=later, values=[38.0] * 13, qualities={0: 1})

    await _landed(db_session, plot, at=at, metric="air_temp")

    assert [(code, state) for code, state, _ in await _alerts(db_session, plot)] == [
        ("heat_stress", AlertState.OPEN)
    ]


# -- resolution: clear beyond the hysteresis band sustained 60 min (D2) --


async def test_a_clear_run_beyond_the_hysteresis_band_resolves_the_open_alert(
    db_session: AsyncSession,
) -> None:
    plot = await _make_plot(db_session, metric="air_temp")
    # 3 h above 35 °C, then 75 min below the 34 °C band (35 − 1 hysteresis).
    values = [38.0] * 13 + [30.0] * 5
    end = await _store_series(db_session, plot, end=_START, values=values)
    opened_at = end - _STEP * 5  # the last hot sample

    evaluator = await _landed(db_session, plot, at=opened_at, metric="air_temp")
    assert [(code, state) for code, state, _ in await _alerts(db_session, plot)] == [
        ("heat_stress", AlertState.OPEN)
    ]

    await evaluator(
        [
            ReadingEvent(
                org_id=plot.org_id,
                farm_id=plot.farm_id,
                plot_id=plot.plot_id,
                metric="air_temp",
                value=30.0,
                at=end,
            )
        ]
    )

    alerts = await _alerts(db_session, plot)
    assert [(code, state) for code, state, _ in alerts] == [("heat_stress", AlertState.RESOLVED)]
    assert alerts[0][2] == end
