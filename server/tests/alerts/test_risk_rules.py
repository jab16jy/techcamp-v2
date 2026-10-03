"""The model rules of docs/06 §3, "flood_risk" / "drought_risk"
(docs/06-diseno-detallado.md §8 "Alertas"; docs/03-modelo-datos.md
§`risk_prediction`).

One rule source, one shape: the severity of a new `risk_prediction` decides
`flood_risk` or `drought_risk` per plot of the cell the prediction is about. The
domain tests below are pure; the use-case tests after them run the real Postgres
evaluator over real plots and cells.

Every test that asserts an open carries its negative: a prediction below `alto`
neither opens nor resolves, which is the other half of docs/06 §8 and the thing a
regression would silently lose.
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
from techcamp.alerts.application import evaluate_risk_rules
from techcamp.alerts.domain import (
    RISK_RULE_CODES,
    Alert,
    AlertAction,
    AlertState,
    PredictionEvidence,
    PredictionSeverity,
    Severity,
    decide_risk_rule,
)
from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.farms.adapters.repositories import SqlAlchemyFarmRepository, SqlAlchemyPlotRepository
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.domain.models import Role
from techcamp.shared.ids import uuid7
from techcamp.weather.adapters.orm import WeatherCellRow

pytestmark = pytest.mark.anyio

_AT = datetime(2026, 10, 2, 11, 5, tzinfo=UTC)  # 06:05 Bogotá, the daily risk run
_ISSUE_MONTH = date(2026, 10, 1)
_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)
_VERSION_ID = uuid7()


def _open_alert(code: str) -> Alert:
    return Alert(
        state=AlertState.OPEN,
        severity=Severity.CRITICAL,
        opened_at=_AT - timedelta(days=1),
        id=uuid7(),
        org_id=uuid7(),
        rule_id=uuid7(),
        rule_code=code,
        plot_id=uuid7(),
    )


def _evidence(*, cell_id: int, severity: str = "high", event: str = "flood") -> PredictionEvidence:
    """A stored `risk_prediction` as the caller of the rule reads it."""
    return PredictionEvidence.from_stored(
        cell_id=cell_id,
        event=event,
        severity=severity,
        horizon_start=_ISSUE_MONTH,
        model_version_id=_VERSION_ID,
    )


# -- the domain: what a new prediction decides, and what it never decides --


def test_a_new_prediction_at_high_or_critical_opens_the_rule() -> None:
    """docs/06 §3 "Severidad del modelo >= alto" and §8 "Alertas": both severities
    at or above `alto` open the alert, and the alert the rule opens is critical
    (the severity the seeded rule carries)."""
    for severity in ("high", "critical"):
        decision = decide_risk_rule(
            severity=PredictionSeverity(severity), current_alert=None, at=_AT
        )
        assert decision.action is AlertAction.OPEN
        assert decision.alert is None


def test_a_prediction_below_high_resolves_the_open_rule() -> None:
    """docs/06 §8: "la resuelve en la primera predicción nueva por debajo de
    `alto`". There is no 60-minute window and no hysteresis band to wait out: one
    prediction per run is the evidence, and its own cadence is the cadence the
    rule is decided at (the same reasoning as D19/D22 for the forecast rules)."""
    alert = _open_alert("flood_risk")

    decision = decide_risk_rule(severity=PredictionSeverity.LOW, current_alert=alert, at=_AT)

    assert decision.action is AlertAction.RESOLVE
    assert decision.alert is not None
    assert decision.alert.state is AlertState.RESOLVED
    assert decision.alert.resolved_at == _AT


def test_a_prediction_still_at_high_keeps_the_open_alert() -> None:
    """A month that stays at `alto` is no evidence that the condition cleared, so
    the alert stays open and no second one opens (docs/06 §3 "Una sola alerta
    abierta" por (`rule_id`, `plot_id`))."""
    alert = _open_alert("flood_risk")

    decision = decide_risk_rule(severity=PredictionSeverity.CRITICAL, current_alert=alert, at=_AT)

    assert decision.action is AlertAction.NO_ACTION
    assert decision.alert is alert


def test_a_prediction_below_high_opens_nothing_and_resolves_nothing() -> None:
    """The negative of both halves on one path: a `low` prediction with no alert
    opens none, and the same severity is the ONLY thing that resolves — so it must
    not also resolve an alert that is not there."""
    assert (
        decide_risk_rule(severity=PredictionSeverity.LOW, current_alert=None, at=_AT).action
        is AlertAction.NO_ACTION
    )
    # A resolved alert holds its (rule, target) no more, so it is decided as no
    # alert at all and a low prediction cannot "resolve" it twice.
    resolved = _open_alert("drought_risk").resolve_automatically(_AT)
    assert (
        decide_risk_rule(severity=PredictionSeverity.HIGH, current_alert=resolved, at=_AT).alert
        is None
    )


def test_the_two_events_name_their_own_rule_and_only_those_events_are_risk() -> None:
    """docs/03-modelo-datos.md: `risk_prediction.event_type` is `flood|drought`
    and docs/06 §3 names the rules `flood_risk` / `drought_risk`. An event this
    mapping does not name is no rule, so it is never decided."""
    assert RISK_RULE_CODES == {"flood": "flood_risk", "drought": "drought_risk"}
    assert RISK_RULE_CODES.get("suitability") is None
    assert RISK_RULE_CODES.get("flood_risk") is None


# -- the use case over real plots of the cell a prediction is about --


@dataclass(frozen=True, slots=True)
class Org:
    org_id: UUID
    farm_id: UUID


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
    return Org(org_id, farm_id)


async def _cell(db_session: AsyncSession) -> int:
    """The 0.1° cell these tests predict, so a plot's `weather_cell_id` and the
    prediction's `cell_id` name the same cell the way the job does."""
    existing = (
        await db_session.execute(select(WeatherCellRow.id).where(WeatherCellRow.lat == 10.9))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = WeatherCellRow(lat=10.9, lon=-74.1)
    db_session.add(row)
    await db_session.commit()
    return row.id


async def _make_plot(
    db_session: AsyncSession, *, org: Org | None = None, cell_id: int | None = None
) -> tuple[UUID, UUID]:
    """A plot of `org` (a fresh one when not given) on the shared cell.

    No node, no sensor and no soil profile: the model rules are decided on the
    prediction's severity alone, so a plot is only ever a target here.
    """
    org = org or await _make_org(db_session)
    plot_id = uuid7()
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org.org_id,
            farm_id=org.farm_id,
            name="Lote 1",
            boundary=_BOUNDARY,
            irrigation_system="drip",
            weather_cell_id=cell_id if cell_id is not None else await _cell(db_session),
        )
    )
    await db_session.commit()
    return org.org_id, plot_id


async def _evaluate(
    db_session: AsyncSession,
    *,
    org_id: UUID,
    predictions: list[PredictionEvidence],
    at: datetime = _AT,
) -> None:
    await evaluate_risk_rules(
        org_id=org_id,
        at=at,
        predictions=predictions,
        rules=SqlAlchemyAlertRuleRepository(db_session),
        farms=SqlAlchemyFarmRepository(db_session),
        plots=SqlAlchemyPlotRepository(db_session),
        alerts=SqlAlchemyAlertRepository(db_session),
    )


async def _alerts(db_session: AsyncSession, plot_id: UUID) -> list[tuple[str, str, str]]:
    """`(rule_code, state, severity)` of the plot's alerts, ordered by rule."""
    result = await db_session.execute(
        select(AlertRuleRow.code, AlertRow.state, AlertRow.severity)
        .join(AlertRow, AlertRow.rule_id == AlertRuleRow.id)
        .where(AlertRow.plot_id == plot_id)
        .order_by(AlertRuleRow.code)
    )
    return [(code, state, severity) for code, state, severity in result]


async def _alert_evidence(db_session: AsyncSession, plot_id: UUID) -> dict[str, object]:
    result = await db_session.execute(
        select(AlertRow).where(AlertRow.plot_id == plot_id, AlertRow.state == "open")
    )
    return result.scalar_one().evidence


async def test_a_high_flood_prediction_opens_a_critical_alert_on_every_plot_of_the_cell(
    db_session: AsyncSession,
) -> None:
    """docs/06 §8 "Alertas": "cada parcela de la celda abre la alerta si la
    severidad es `alto` o `crítico`". The cell is the unit, so both plots of the
    cell get one — and the severity is the `crítica` of the docs/06 §3 rule row,
    decided in the same write rather than upgraded afterwards."""
    org = await _make_org(db_session)
    cell_id = await _cell(db_session)
    first = await _make_plot(db_session, org=org, cell_id=cell_id)
    second = await _make_plot(db_session, org=org, cell_id=cell_id)

    await _evaluate(
        db_session, org_id=org.org_id, predictions=[_evidence(cell_id=cell_id, severity="high")]
    )

    assert await _alerts(db_session, first[1]) == [("flood_risk", "open", "critical")]
    assert await _alerts(db_session, second[1]) == [("flood_risk", "open", "critical")]
    # docs/06 §8: "Cada predicción guarda `model_version_id`: toda alerta se
    # puede rastrear hasta el modelo exacto", and the month it is about.
    assert await _alert_evidence(db_session, first[1]) == {
        "event": "flood",
        "severity": "high",
        "horizon_start": _ISSUE_MONTH.isoformat(),
        "model_version_id": str(_VERSION_ID),
    }


async def test_a_low_prediction_and_a_prediction_of_another_cell_open_nothing(
    db_session: AsyncSession,
) -> None:
    """The negative half on the real path: a prediction below `alto` opens no
    alert, and neither does a prediction about a cell the plot is not in — the
    cell is what decides which plots the alert is about."""
    org = await _make_org(db_session)
    cell_id = await _cell(db_session)
    other_cell = WeatherCellRow(lat=11.9, lon=-74.2)
    db_session.add(other_cell)
    await db_session.commit()
    _, plot_id = await _make_plot(db_session, org=org, cell_id=cell_id)

    await _evaluate(
        db_session,
        org_id=org.org_id,
        predictions=[
            _evidence(cell_id=cell_id, severity="low"),
            _evidence(cell_id=other_cell.id, severity="critical"),
        ],
    )

    assert await _alerts(db_session, plot_id) == []


async def test_the_second_high_prediction_of_the_same_month_opens_no_second_alert(
    db_session: AsyncSession,
) -> None:
    """docs/06 §3 "Una sola alerta abierta" por (`rule_id`, `plot_id`), and the
    run is re-evaluated every day: a month that stays at `alto` must not notify
    twice, and the alert keeps the instant it opened."""
    org = await _make_org(db_session)
    cell_id = await _cell(db_session)
    _, plot_id = await _make_plot(db_session, org=org, cell_id=cell_id)
    later = _AT + timedelta(days=1)

    await _evaluate(db_session, org_id=org.org_id, predictions=[_evidence(cell_id=cell_id)])
    await _evaluate(
        db_session,
        org_id=org.org_id,
        predictions=[_evidence(cell_id=cell_id, severity="critical")],
        at=later,
    )

    assert await _alerts(db_session, plot_id) == [("flood_risk", "open", "critical")]
    opened = (
        await db_session.execute(
            select(AlertRow).where(AlertRow.plot_id == plot_id, AlertRow.state == "open")
        )
    ).scalar_one()
    assert opened.opened_at.replace(tzinfo=UTC) == _AT


async def test_the_first_low_prediction_resolves_the_open_alert(
    db_session: AsyncSession,
) -> None:
    """docs/06 §8: "la resuelve en la primera predicción nueva por debajo de
    `alto`", with no window to wait out."""
    org = await _make_org(db_session)
    cell_id = await _cell(db_session)
    _, plot_id = await _make_plot(db_session, org=org, cell_id=cell_id)
    later = _AT + timedelta(days=1)
    await _evaluate(db_session, org_id=org.org_id, predictions=[_evidence(cell_id=cell_id)])

    await _evaluate(
        db_session,
        org_id=org.org_id,
        predictions=[_evidence(cell_id=cell_id, severity="low")],
        at=later,
    )

    assert await _alerts(db_session, plot_id) == [("flood_risk", "resolved", "critical")]
    resolved = await db_session.get_one(
        AlertRow,
        (
            await db_session.execute(select(AlertRow.id).where(AlertRow.plot_id == plot_id))
        ).scalar_one(),
    )
    assert resolved.resolved_at.replace(tzinfo=UTC) == later


async def test_a_run_with_no_new_prediction_leaves_the_open_alert_open(
    db_session: AsyncSession,
) -> None:
    """Missing evidence is a third state, never a resolution: ERA5 can leave a
    cell without a prediction for a whole month (docs/06 §8 "Datos de entrada"),
    and a month the model never spoke about says nothing about the flood risk."""
    org = await _make_org(db_session)
    cell_id = await _cell(db_session)
    _, plot_id = await _make_plot(db_session, org=org, cell_id=cell_id)
    await _evaluate(db_session, org_id=org.org_id, predictions=[_evidence(cell_id=cell_id)])

    await _evaluate(
        db_session,
        org_id=org.org_id,
        predictions=[],
        at=_AT + timedelta(days=1),
    )

    assert await _alerts(db_session, plot_id) == [("flood_risk", "open", "critical")]


async def test_the_two_events_open_their_own_rule_and_not_the_other_one(
    db_session: AsyncSession,
) -> None:
    """The two rules are independent: a cell at `alto` for the flood and `low` for
    the drought opens `flood_risk` only (docs/06 §3 names them as two rules and
    docs/08 §M2/§M3 as two models)."""
    org = await _make_org(db_session)
    cell_id = await _cell(db_session)
    _, plot_id = await _make_plot(db_session, org=org, cell_id=cell_id)

    await _evaluate(
        db_session,
        org_id=org.org_id,
        predictions=[
            _evidence(cell_id=cell_id, severity="high", event="flood"),
            _evidence(cell_id=cell_id, severity="low", event="drought"),
        ],
    )

    assert await _alerts(db_session, plot_id) == [("flood_risk", "open", "critical")]


async def test_a_prediction_of_one_org_never_opens_an_alert_on_another_orgs_plot(
    db_session: AsyncSession,
) -> None:
    """docs/09-cuellos-de-botella.md §Seguridad: neighbouring plots share a cell
    (docs/00 glosario), so the cell alone cannot say whose alert this is. Every
    read below keeps its `org_id`, and a cell of one organization opens nothing on
    a plot of another."""
    cell_id = await _cell(db_session)
    mine_org_id, my_plot = await _make_plot(db_session, cell_id=cell_id)
    _, their_plot = await _make_plot(db_session, cell_id=cell_id)

    await _evaluate(
        db_session,
        org_id=mine_org_id,
        predictions=[_evidence(cell_id=cell_id, severity="critical")],
    )

    assert await _alerts(db_session, my_plot) == [("flood_risk", "open", "critical")]
    assert await _alerts(db_session, their_plot) == []
