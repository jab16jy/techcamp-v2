"""The forecast and daily worker rules (docs/06 §3, "Pronóstico"; D19, D20, D22).

Two rules, two cadences, one shape: `heavy_rain_forecast` is decided on the
forecast day's rain and `fungal_risk` on the previous cell-day's humidity and
mean temperature, both read from `weather_daily`. The domain tests below are
pure; the use-case tests after them run the real Postgres evaluator over real
plots, readings and cells.

The negative assertion is part of every test that asserts an open: a rule that
must not fire, on the same data path, is what keeps T5's two CRITICAL bugs from
repeating.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.orm import AlertRow, AlertRuleRow
from techcamp.alerts.adapters.repositories import (
    SqlAlchemyAlertRepository,
    SqlAlchemyAlertRuleRepository,
)
from techcamp.alerts.application import evaluate_weather_rules
from techcamp.alerts.domain import (
    FUNGAL_MAX_TEMP_C,
    FUNGAL_MIN_TEMP_C,
    Alert,
    AlertAction,
    AlertRule,
    AlertState,
    CellDay,
    CellDayHumidityEvidence,
    ForecastRainEvidence,
    Severity,
    decide_worker_rule,
    mean_daily_temp_c,
    worker_rule_evidence,
    worker_rule_opening_severity,
)
from techcamp.farms.adapters.orm import FarmRow, PlotRow, SoilProfileRow
from techcamp.farms.adapters.repositories import (
    SqlAlchemyFarmRepository,
    SqlAlchemyPlotRepository,
    SqlAlchemySoilProfileRepository,
)
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.domain.models import Role
from techcamp.notifications.adapters.orm import NotificationRow
from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.orm import NodeRow, SensorRow
from techcamp.telemetry.adapters.repositories import (
    SqlAlchemyNodeRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemySensorRepository,
)
from techcamp.telemetry.domain.models import ReadingRecord
from techcamp.weather.adapters.orm import WeatherCellRow, WeatherDailyRow
from techcamp.weather.adapters.repositories import SqlAlchemyWeatherRepository

pytestmark = pytest.mark.anyio

_AT = datetime(2026, 9, 27, 13, 10, tzinfo=UTC)  # 08:10 Bogotá, outside quiet hours
_FORECAST_DAY = date(2026, 9, 28)  # the forecast day `heavy_rain_forecast` reads
_CELL_DAY = date(2026, 9, 26)  # the cell-day `fungal_risk` reads
_FETCHED_AT = _AT - timedelta(minutes=10)  # the 3 h refresh, ten minutes before
_AGGREGATE_MAX_GAP = timedelta(days=1)
"""`evaluate_weather_rules._AGGREGATE_MAX_GAP`, the freshness margin a day
aggregate is read with."""

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)


_SEEDED: dict[str, dict[str, object]] = {
    "heavy_rain_forecast": {
        "metric": "rain",
        "operator": ">",
        "threshold": 50.0,
        "hysteresis": 0.0,
    },
    "fungal_risk": {"metric": "air_rh", "operator": ">", "threshold": 85.0, "hysteresis": 5.0},
}


def _rule(code: str, **overrides: object) -> AlertRule:
    """The seeded factory rule as the repository maps it, plus the overrides a
    test needs to stand for a rule this module never evaluates."""
    columns: dict[str, object] = {
        "metric": None,
        "operator": None,
        "threshold": None,
        "hysteresis": 0.0,
        **_SEEDED.get(code, {}),
        **overrides,
    }
    return AlertRule(code=code, id=uuid7(), **columns)  # type: ignore[arg-type]


# -- the domain: what each worker rule is decided on, and how it resolves --


def test_the_worker_evidence_is_the_forecast_rain_or_the_cell_day_humidity() -> None:
    """D19, D20: `heavy_rain_forecast` reads the forecast day's rain, and
    `fungal_risk` the previous cell-day's humidity with the mean temperature
    derived from its two ends. Same two rows, opposite answers per rule."""
    observed = CellDay(
        fetched_at=_FETCHED_AT,
        rain_mm=80.0,  # yesterday's real rain: not what the forecast rule reads
        rh_mean_pct=90.0,
        tmin_c=21.0,
        tmax_c=25.0,
    )
    forecast = CellDay(fetched_at=_FETCHED_AT, rain_mm=62.0, rh_mean_pct=70.0)

    rain = worker_rule_evidence(_rule("heavy_rain_forecast"), observed=observed, forecast=forecast)
    assert rain == ForecastRainEvidence(observed_at=_FETCHED_AT, rain_mm=62.0)
    assert rain is not None and rain.value == 62.0
    # The forecast rule has no second condition, so its evidence is always mild:
    # the rule-code discrimination stays in the domain, not in the caller.
    assert rain is not None and rain.is_mild is True

    humidity = worker_rule_evidence(_rule("fungal_risk"), observed=observed, forecast=forecast)
    assert humidity == CellDayHumidityEvidence(
        observed_at=_FETCHED_AT, rh_mean_pct=90.0, mean_temp_c=23.0
    )
    assert humidity is not None and humidity.value == 90.0
    assert humidity is not None and humidity.is_mild is True

    # A cell-day without humidity is not evidence, and a forecast is not an
    # observation of the day that already happened (D19).
    assert (
        worker_rule_evidence(
            _rule("fungal_risk"), observed=CellDay(fetched_at=_FETCHED_AT), forecast=forecast
        )
        is None
    )
    # ... and the forecast rule has no forecast row to read either.
    assert (
        worker_rule_evidence(_rule("heavy_rain_forecast"), observed=observed, forecast=None) is None
    )


def test_the_mean_temperature_is_the_midpoint_of_the_two_stored_ends() -> None:
    """D19: `(tmin_c + tmax_c) / 2` is the only form a stored `weather_daily` row
    carries temperature in, and a day missing one of the two ends has no mean."""
    assert mean_daily_temp_c(21.0, 25.0) == 23.0
    assert mean_daily_temp_c(21.0, None) is None
    assert mean_daily_temp_c(None, 25.0) is None


def test_the_evidence_selector_answers_none_for_every_rule_of_another_source() -> None:
    """D17, in the shape `plot_rule_metric` already has: the two selectors
    cannot disagree about which rules this module's plot evaluator owns."""
    observed = CellDay(fetched_at=_FETCHED_AT, rain_mm=80.0, rh_mean_pct=90.0)
    forecast = CellDay(fetched_at=_FETCHED_AT, rain_mm=62.0)
    for code, metric in (
        ("heat_stress", "air_temp"),
        ("water_stress", "soil_moisture"),
        ("node_offline", None),
        ("node_battery_low", "battery_v"),
    ):
        assert (
            worker_rule_evidence(
                _rule(code, metric=metric, operator=">" if metric else None, threshold=1.0),
                observed=observed,
                forecast=forecast,
            )
            is None
        ), code


def test_a_humid_cell_day_that_is_too_hot_never_opens_a_fungal_risk_alert() -> None:
    """D19: the rule is a humid **and mild** day. RH 95 % at 34 °C is not one, and
    the rule's own columns (`air_rh > 85`) cannot express the temperature half,
    so the evidence carries it and the decision reads it."""
    hot = CellDayHumidityEvidence(observed_at=_FETCHED_AT, rh_mean_pct=95.0, mean_temp_c=34.0)
    assert hot.value == 95.0
    assert hot.is_mild is False

    decision = decide_worker_rule(
        _rule("fungal_risk"),
        [(hot.observed_at, hot.value)],
        _AT,
        max_gap=_AGGREGATE_MAX_GAP,
        is_mild=hot.is_mild,
    )
    assert decision.action is AlertAction.NO_ACTION
    assert decision.alert is None

    # A day with no stored temperature is not a mild day either: `None` means the
    # provider never said, and an unsaid half cannot open the rule.
    assert (
        CellDayHumidityEvidence(observed_at=_FETCHED_AT, rh_mean_pct=95.0, mean_temp_c=None).is_mild
        is False
    )


def test_a_humid_mild_cell_day_opens_the_fungal_risk_alert() -> None:
    """D19: RH 90 % with a mean temperature of 23 °C is the day the rule names.
    Both halves of the condition hold, so the humidity alone decides it."""
    mild = CellDayHumidityEvidence(observed_at=_FETCHED_AT, rh_mean_pct=90.0, mean_temp_c=23.0)
    assert mild.is_mild is True

    decision = decide_worker_rule(
        _rule("fungal_risk"),
        [(mild.observed_at, mild.value)],
        _AT,
        max_gap=_AGGREGATE_MAX_GAP,
        is_mild=mild.is_mild,
    )
    assert decision.action is AlertAction.OPEN
    assert decision.alert is None

    # The ends of the range are inside it, not outside it.
    for temp in (FUNGAL_MIN_TEMP_C, FUNGAL_MAX_TEMP_C):
        assert CellDayHumidityEvidence(
            observed_at=_FETCHED_AT, rh_mean_pct=90.0, mean_temp_c=temp
        ).is_mild


def test_an_open_fungal_risk_alert_resolves_on_the_first_day_that_is_not_mild() -> None:
    """D22: the clear half of the condition is the temperature, exactly as it is
    the temperature that opened it, so a hot day resolves an open alert even
    though the humidity alone still violates the rule."""
    alert = _open_alert("fungal_risk")

    decision = decide_worker_rule(
        _rule("fungal_risk"),
        [(_FETCHED_AT, 95.0)],
        _AT,
        max_gap=_AGGREGATE_MAX_GAP,
        current_alert=alert,
        is_mild=False,
    )
    assert decision.action is AlertAction.RESOLVE
    assert decision.alert is not None
    assert decision.alert.state is AlertState.RESOLVED
    assert decision.alert.resolved_at == _AT

    # A day that is still humid and still mild changes nothing.
    assert (
        decide_worker_rule(
            _rule("fungal_risk"),
            [(_FETCHED_AT, 95.0)],
            _AT,
            max_gap=_AGGREGATE_MAX_GAP,
            current_alert=alert,
            is_mild=True,
        ).action
        is AlertAction.NO_ACTION
    )


def test_a_worker_rule_opens_on_its_aggregate_and_resolves_on_the_first_clear_day() -> None:
    """D22: 60 minutes of clear evidence is not computable at a 3 h or daily
    cadence, so these rules resolve on the FIRST false evaluation. The freshness
    rule still applies: an aggregate older than the margin is not evidence."""
    rule = _rule("heavy_rain_forecast")
    violating = [(_FETCHED_AT, 62.0)]

    opened = decide_worker_rule(rule, violating, _AT, max_gap=_AGGREGATE_MAX_GAP)
    assert opened.action is AlertAction.OPEN
    assert opened.alert is None

    # The same violating aggregate with an open alert open keeps it open, and a
    # day that cleared the condition resolves it at once.
    alert = _open_alert("heavy_rain_forecast")
    still = decide_worker_rule(
        rule, violating, _AT, max_gap=_AGGREGATE_MAX_GAP, current_alert=alert
    )
    assert still.action is AlertAction.NO_ACTION
    assert still.alert is alert

    # `heavy_rain_forecast` has no hysteresis, so the clear band is strict: a
    # forecast of exactly the threshold no longer violates but does not clear
    # either, and the alert waits for the first day under it.
    cleared = decide_worker_rule(
        rule, [(_FETCHED_AT, 49.0)], _AT, max_gap=_AGGREGATE_MAX_GAP, current_alert=alert
    )
    assert cleared.action is AlertAction.RESOLVE
    assert cleared.alert is not None
    assert cleared.alert.state is AlertState.RESOLVED
    assert cleared.alert.resolved_at == _AT

    # Nothing to decide on: no row, or one too old to be evidence.
    assert (
        decide_worker_rule(rule, [], _AT, max_gap=_AGGREGATE_MAX_GAP).action
        is AlertAction.NO_ACTION
    )
    assert (
        decide_worker_rule(
            rule,
            [(_FETCHED_AT - _AGGREGATE_MAX_GAP - timedelta(seconds=1), 62.0)],
            _AT,
            max_gap=_AGGREGATE_MAX_GAP,
        ).action
        is AlertAction.NO_ACTION
    )
    # A rule this source cannot compare never opens, whatever the evidence says.
    assert (
        decide_worker_rule(
            _rule("node_offline", operator=None, threshold=None),
            violating,
            _AT,
            max_gap=_AGGREGATE_MAX_GAP,
        ).action
        is AlertAction.NO_ACTION
    )


def test_the_forecast_rule_opens_critical_only_when_the_soil_is_saturated() -> None:
    """D20: the severity is decided when the alert OPENS, not by opening a
    warning and upgrading it, so the evaluator passes it to `open_alert`."""
    rule = _rule("heavy_rain_forecast")

    assert worker_rule_opening_severity(rule, saturated=True) is Severity.CRITICAL
    assert worker_rule_opening_severity(rule, saturated=False) is None
    # `fungal_risk` has no severity of its own in docs/06 §3.
    assert worker_rule_opening_severity(_rule("fungal_risk"), saturated=True) is None


def _open_alert(code: str) -> Alert:
    return Alert(
        state=AlertState.OPEN,
        severity=Severity.WARNING,
        opened_at=_AT - timedelta(hours=3),
        id=uuid7(),
        org_id=uuid7(),
        rule_id=uuid7(),
        rule_code=code,
        plot_id=uuid7(),
    )


# -- the use case over real plots, cells and readings --


@dataclass(frozen=True, slots=True)
class Org:
    org_id: UUID
    farm_id: UUID
    owner_id: UUID


@dataclass(frozen=True, slots=True)
class Plot:
    org_id: UUID
    farm_id: UUID
    plot_id: UUID
    sensor_id: int


async def _make_org(db_session: AsyncSession) -> Org:
    """One organization with a farm and the owner who receives its alerts (D4)."""
    org_id, farm_id, owner_id = uuid7(), uuid7(), uuid7()
    db_session.add(
        AppUserRow(id=owner_id, phone=f"+57{uuid7().int % 10**13:013d}", full_name="Owner")
    )
    await db_session.commit()
    db_session.add(OrganizationRow(id=org_id, name="Test Org", kind="individual"))
    await db_session.commit()
    db_session.add(MembershipRow(org_id=org_id, user_id=owner_id, role=Role.OWNER.value))
    db_session.add(
        FarmRow(
            id=farm_id,
            org_id=org_id,
            name="Finca Principal",
            municipality_code="47001",
            location=_POINT,
        )
    )
    await db_session.commit()
    return Org(org_id, farm_id, owner_id)


async def _make_plot(
    db_session: AsyncSession,
    *,
    org: Org | None = None,
    field_capacity_pct: float | None = 30.0,
    soil_moisture: float | None = None,
) -> Plot:
    """A plot of `org` (a fresh one when not given) on its own weather cell, with
    a node, a `soil_moisture` sensor and the newest reading saturation reads."""
    org = org or await _make_org(db_session)
    org_id, farm_id, plot_id, node_id = org.org_id, org.farm_id, uuid7(), uuid7()
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=_BOUNDARY,
            irrigation_system="drip",
            weather_cell_id=await _cell(db_session),
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
            claimed_at=_AT - timedelta(days=30),
            status="online",
        )
    )
    sensor = SensorRow(node_id=node_id, channel_key="sm_0", metric="soil_moisture", unit="pct")
    db_session.add(sensor)
    await db_session.commit()
    await db_session.refresh(sensor)
    if soil_moisture is not None:
        await _store_soil_moisture(db_session, sensor.id, value=soil_moisture)
    return Plot(org_id, farm_id, plot_id, sensor.id)


async def _cell(db_session: AsyncSession) -> int:
    """The 0.1° cell every plot of these tests shares (D23: one cell, one read)."""
    existing = (
        await db_session.execute(select(WeatherCellRow.id).where(WeatherCellRow.lat == 10.9))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = WeatherCellRow(lat=10.9, lon=-74.1)
    db_session.add(row)
    await db_session.commit()
    return row.id


async def _store_soil_moisture(db_session: AsyncSession, sensor_id: int, *, value: float) -> None:
    await SqlAlchemyReadingRepository(db_session).insert_batch(
        [
            ReadingRecord(
                sensor_id=sensor_id,
                time=_AT - timedelta(minutes=5),
                raw_value=value,
                value=value,
                received_at=_AT - timedelta(minutes=5),
                quality=0,
            )
        ]
    )


async def _store_weather(
    db_session: AsyncSession,
    *,
    cell_id: int,
    day: date,
    is_forecast: bool,
    rain_mm: float | None = None,
    rh_mean_pct: float | None = None,
    tmin_c: float | None = None,
    tmax_c: float | None = None,
) -> None:
    """The provider's latest word on one cell-day: a stored row is overwritten,
    which is what the next 3 h refresh does to the same primary key."""
    row = await db_session.get(WeatherDailyRow, (cell_id, day, is_forecast))
    if row is None:
        row = WeatherDailyRow(cell_id=cell_id, day=day, is_forecast=is_forecast, et0_mm=4.2)
        db_session.add(row)
    row.rain_mm = rain_mm
    row.rh_mean_pct = rh_mean_pct
    row.tmin_c = tmin_c
    row.tmax_c = tmax_c
    row.fetched_at = _FETCHED_AT
    await db_session.commit()


async def _evaluate(
    db_session: AsyncSession, *, org_id: UUID, day: date, at: datetime = _AT
) -> None:
    await evaluate_weather_rules(
        org_id=org_id,
        at=at,
        day=day,
        rules=SqlAlchemyAlertRuleRepository(db_session),
        farms=SqlAlchemyFarmRepository(db_session),
        plots=SqlAlchemyPlotRepository(db_session),
        soils=SqlAlchemySoilProfileRepository(db_session),
        weather=SqlAlchemyWeatherRepository(db_session),
        nodes=SqlAlchemyNodeRepository(db_session),
        sensors=SqlAlchemySensorRepository(db_session),
        readings=SqlAlchemyReadingRepository(db_session),
        alerts=SqlAlchemyAlertRepository(db_session),
    )


async def _alerts(db_session: AsyncSession, plot: Plot) -> list[tuple[str, str, str]]:
    """`(rule_code, state, severity)` of the plot's alerts."""
    result = await db_session.execute(
        select(AlertRuleRow.code, AlertRow.state, AlertRow.severity)
        .join(AlertRow, AlertRow.rule_id == AlertRuleRow.id)
        .where(AlertRow.plot_id == plot.plot_id)
        .order_by(AlertRuleRow.code)
    )
    return [(code, state, severity) for code, state, severity in result]


async def _notification_channels(db_session: AsyncSession, alert_id: UUID) -> list[str]:
    """The channels of the alert's outbox rows: one write means one notice, and
    an alert opened `warning` and upgraded afterwards would leave two (D20)."""
    result = await db_session.execute(
        select(NotificationRow.channel).where(NotificationRow.alert_id == alert_id)
    )
    return [channel for (channel,) in result]


async def test_a_heavy_forecast_opens_a_warning_and_a_saturated_plot_opens_critical(
    db_session: AsyncSession,
) -> None:
    """D20: the same forecast over two plots of one cell — the dry one opens
    `warning`, the saturated one opens `critical` in ONE write (a single alert
    row and a single critical notification, never a warning upgraded later)."""
    org = await _make_org(db_session)
    dry = await _make_plot(db_session, org=org, soil_moisture=20.0)  # θ 20 < θFC 30
    wet = await _make_plot(db_session, org=org, soil_moisture=32.0)  # θ 32 ≥ θFC 30
    await _store_weather(db_session, cell_id=1, day=_FORECAST_DAY, is_forecast=True, rain_mm=62.0)

    await _evaluate(db_session, org_id=dry.org_id, day=_FORECAST_DAY)

    assert await _alerts(db_session, dry) == [("heavy_rain_forecast", "open", "warning")]
    assert await _alerts(db_session, wet) == [("heavy_rain_forecast", "open", "critical")]

    critical_id = (
        await db_session.execute(select(AlertRow.id).where(AlertRow.plot_id == wet.plot_id))
    ).scalar_one()
    assert await _notification_channels(db_session, critical_id) == ["push"]


async def test_a_humid_mild_cell_day_opens_a_fungal_risk_alert(db_session: AsyncSession) -> None:
    """D19: RH 90 % with a mean temperature of 23 °C is the day the rule names."""
    plot = await _make_plot(db_session, soil_moisture=20.0)
    await _store_weather(
        db_session,
        cell_id=1,
        day=_CELL_DAY,
        is_forecast=False,
        rh_mean_pct=90.0,
        tmin_c=21.0,
        tmax_c=25.0,
    )

    await _evaluate(db_session, org_id=plot.org_id, day=_CELL_DAY)

    assert await _alerts(db_session, plot) == [("fungal_risk", "open", "warning")]


async def test_neither_a_low_forecast_nor_a_dry_or_a_hot_cell_day_opens_anything(
    db_session: AsyncSession,
) -> None:
    """The negative half of both rules, on the same data path: a forecast under
    the rule's threshold, a dry day, and a humid day that is too hot to be a
    fungal-risk day (D19's 20-30 °C half)."""
    plot = await _make_plot(db_session, soil_moisture=20.0)
    await _store_weather(db_session, cell_id=1, day=_FORECAST_DAY, is_forecast=True, rain_mm=49.0)
    await _store_weather(
        db_session,
        cell_id=1,
        day=_CELL_DAY,
        is_forecast=False,
        rh_mean_pct=40.0,
        tmin_c=21.0,
        tmax_c=25.0,
    )
    await _store_weather(
        db_session,
        cell_id=1,
        day=date(2026, 9, 25),
        is_forecast=False,
        rh_mean_pct=92.0,
        tmin_c=30.0,
        tmax_c=34.0,
    )

    await _evaluate(db_session, org_id=plot.org_id, day=_FORECAST_DAY)
    await _evaluate(db_session, org_id=plot.org_id, day=_CELL_DAY)
    await _evaluate(db_session, org_id=plot.org_id, day=date(2026, 9, 25))

    assert await _alerts(db_session, plot) == []


async def test_an_open_forecast_alert_resolves_on_the_first_forecast_under_the_threshold(
    db_session: AsyncSession,
) -> None:
    """D22: at a 3 h cadence a 60 minute sustained clear run is not computable,
    so the first false evaluation resolves — the hysteresis band still applies,
    which is why the clearing forecast has to be under 50 mm and not on it."""
    plot = await _make_plot(db_session, soil_moisture=20.0)
    await _store_weather(db_session, cell_id=1, day=_FORECAST_DAY, is_forecast=True, rain_mm=62.0)
    await _evaluate(db_session, org_id=plot.org_id, day=_FORECAST_DAY)
    opened = (
        await db_session.execute(select(AlertRow.id).where(AlertRow.plot_id == plot.plot_id))
    ).scalar_one()

    await _store_weather(db_session, cell_id=1, day=_FORECAST_DAY, is_forecast=True, rain_mm=49.0)
    later = _AT + timedelta(hours=3)
    await _evaluate(db_session, org_id=plot.org_id, day=_FORECAST_DAY, at=later)

    resolved = await db_session.get_one(AlertRow, opened)
    assert resolved.state == AlertState.RESOLVED.value
    assert resolved.resolved_at.replace(tzinfo=UTC) == later
    # Still exactly one alert: a resolved one is not a second one (docs/06 §3).
    assert len(await _alerts(db_session, plot)) == 1


async def test_another_orgs_plot_is_never_evaluated(db_session: AsyncSession) -> None:
    """The fan-out is per organization (D21), so a plot that shares the same
    weather cell and the same forecast is not read by another org's job."""
    mine = await _make_plot(db_session, soil_moisture=20.0)
    other = await _make_plot(db_session, soil_moisture=20.0)
    await _store_weather(db_session, cell_id=1, day=_FORECAST_DAY, is_forecast=True, rain_mm=62.0)

    await _evaluate(db_session, org_id=mine.org_id, day=_FORECAST_DAY)

    assert [code for code, _, _ in await _alerts(db_session, mine)] == ["heavy_rain_forecast"]
    assert await _alerts(db_session, other) == []
