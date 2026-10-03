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

import techcamp.alerts.adapters.evaluate_risk as evaluate_risk_module
from techcamp.alerts.adapters.evaluate_risk import build_risk_evaluation
from techcamp.alerts.adapters.orm import AlertRow, AlertRuleRow
from techcamp.alerts.adapters.repositories import (
    SqlAlchemyAlertRepository,
    SqlAlchemyAlertRuleRepository,
)
from techcamp.alerts.application import acknowledge, evaluate_risk_rules, resolve_manually
from techcamp.alerts.domain import (
    RISK_RULE_CODES,
    RISK_RULE_EVENTS,
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
from techcamp.identity.adapters.repositories import SqlAlchemyMembershipRepository
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
_ISSUED_AT = datetime(2026, 10, 2, 11, tzinfo=UTC)
"""When the stored prediction was issued: the daily run writes the rows at 06:00
and evaluates them minutes later, so a prediction is always a little older than
the alert it opens."""


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


def _evidence(
    *,
    cell_id: int,
    severity: str = "high",
    event: str = "flood",
    horizon_start: date = _ISSUE_MONTH,
    model_version_id: UUID = _VERSION_ID,
    issued_at: datetime = _ISSUED_AT,
) -> PredictionEvidence:
    """A stored `risk_prediction` as the caller of the rule reads it."""
    return PredictionEvidence.from_stored(
        cell_id=cell_id,
        event=event,
        severity=severity,
        horizon_start=horizon_start,
        model_version_id=model_version_id,
        issued_at=issued_at,
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
    # The evaluator walks the rules and asks the other direction, so the two
    # mappings cannot say different things.
    assert RISK_RULE_EVENTS == {code: event for event, code in RISK_RULE_CODES.items()}
    assert RISK_RULE_EVENTS.get("heat_stress") is None


# -- the use case over real plots of the cell a prediction is about --


@dataclass(frozen=True, slots=True)
class Org:
    org_id: UUID
    farm_id: UUID
    owner_id: UUID


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


async def _close_by_hand(
    db_session: AsyncSession, *, plot_id: UUID, org: Org, at: datetime
) -> None:
    """The user closes the alert: acknowledge, then resolve with a note.

    docs/06-diseno-detallado.md §3 (`Acknowledged --> Resolved: condición falsa +
    histéresis o cierre manual`) and `POST /alerts/{id}:resolve` (docs/04).
    """
    alerts = SqlAlchemyAlertRepository(db_session)
    memberships = SqlAlchemyMembershipRepository(db_session)
    alert_id = (
        await db_session.execute(
            select(AlertRow.id).where(AlertRow.plot_id == plot_id, AlertRow.state == "open")
        )
    ).scalar_one()
    acknowledged = await acknowledge(
        user_id=org.owner_id,
        alert_id=alert_id,
        at=at,
        alerts=alerts,
        memberships=memberships,
    )
    await resolve_manually(
        user_id=org.owner_id,
        alert_id=acknowledged.id,
        note="Revisado en campo",
        at=at + timedelta(minutes=1),
        alerts=alerts,
        memberships=memberships,
    )


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


async def _own_rule(
    db_session: AsyncSession, *, org_id: UUID, code: str, severity: str = "critical"
) -> None:
    """One rule of the organization itself.

    `uq_alert_rule_factory_code` is unique on `code` where `org_id IS NULL`, so an
    org MAY have its own row of a factory code (`test_schema.py`
    `test_a_second_factory_rule_with_an_existing_code_is_rejected`), which is the
    case this evaluator has to decide like `evaluate_weather_rules` does.
    """
    db_session.add(
        AlertRuleRow(
            id=uuid7(),
            org_id=org_id,
            code=code,
            metric="probability",
            operator=">",
            threshold=0.5,
            hysteresis=0.0,
            min_duration_min=0,
            severity=severity,
            crop_id=None,
        )
    )
    await db_session.commit()


async def _alerts(db_session: AsyncSession, plot_id: UUID) -> list[tuple[str, str, str]]:
    """`(rule_code, state, severity)` of the plot's alerts, ordered by rule and
    state so two rows of the same rule (the factory one and the org's own, or a
    resolved one and the one that replaced it) always come back the same way."""
    result = await db_session.execute(
        select(AlertRuleRow.code, AlertRow.state, AlertRow.severity)
        .join(AlertRow, AlertRow.rule_id == AlertRuleRow.id)
        .where(AlertRow.plot_id == plot_id)
        .order_by(AlertRuleRow.code, AlertRow.state)
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
        "issued_at": _ISSUED_AT.isoformat(),
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


async def test_a_new_prediction_below_high_resolves_the_open_alert(
    db_session: AsyncSession,
) -> None:
    """docs/06 §8: "la resuelve en la primera predicción nueva por debajo de
    `alto`", with no window to wait out.

    "Nueva" is a new ROW: the stored prediction of a month is immutable
    (docs/03 §Unicidad de la predicción), so what resolves the alert is the next
    month's row at `bajo`. The same month at another severity would be a
    prediction that changed after it was stored, which cannot happen."""
    org = await _make_org(db_session)
    cell_id = await _cell(db_session)
    _, plot_id = await _make_plot(db_session, org=org, cell_id=cell_id)
    later = _AT + timedelta(days=1)
    await _evaluate(db_session, org_id=org.org_id, predictions=[_evidence(cell_id=cell_id)])

    await _evaluate(
        db_session,
        org_id=org.org_id,
        predictions=[_evidence(cell_id=cell_id, severity="low", horizon_start=date(2026, 11, 1))],
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


async def test_every_rule_of_the_org_that_is_a_model_rule_is_decided(
    db_session: AsyncSession,
) -> None:
    """The evaluator walks the organization's rules like
    `evaluate_weather_rules` does (`alerts/application/evaluate_weather_rules.py:138`),
    so a rule the organization added for itself is decided next to the factory one
    — `uq_alert_rule_factory_code` only covers `org_id IS NULL`, so both rows exist
    and only keeping one per code would silently drop the org's own.

    The negative half is on the same walk: the org's own `heat_stress` is not a
    model rule, so a prediction is never evidence for it."""
    org = await _make_org(db_session)
    cell_id = await _cell(db_session)
    _, plot_id = await _make_plot(db_session, org=org, cell_id=cell_id)
    await _own_rule(db_session, org_id=org.org_id, code="flood_risk")
    await _own_rule(db_session, org_id=org.org_id, code="heat_stress", severity="warning")

    await _evaluate(
        db_session,
        org_id=org.org_id,
        predictions=[_evidence(cell_id=cell_id, severity="critical")],
    )

    assert await _alerts(db_session, plot_id) == [
        ("flood_risk", "open", "critical"),
        ("flood_risk", "open", "critical"),
    ]


async def test_a_prediction_the_user_closed_by_hand_is_never_decided_again(
    db_session: AsyncSession,
) -> None:
    """docs/06 §3: "Acknowledged --> Resolved: condición falsa + histéresis o
    **cierre manual**". The daily run of the same month hands the SAME stored
    prediction over every morning (docs/06 §8, la predicción de una celda, evento
    y mes se escribe una vez), and the evidence did not change, so re-deciding it
    would reopen the alert the farmer just closed — every day until the month
    ends.

    A prediction is therefore decided at most once per plot and rule."""
    org = await _make_org(db_session)
    cell_id = await _cell(db_session)
    _, plot_id = await _make_plot(db_session, org=org, cell_id=cell_id)
    prediction = _evidence(cell_id=cell_id, severity="critical")
    await _evaluate(db_session, org_id=org.org_id, predictions=[prediction])
    assert await _alerts(db_session, plot_id) == [("flood_risk", "open", "critical")]

    await _close_by_hand(db_session, plot_id=plot_id, org=org, at=_AT + timedelta(hours=1))
    assert await _alerts(db_session, plot_id) == [("flood_risk", "resolved", "critical")]

    # The next two mornings, over the same stored row.
    for day in (1, 2):
        await _evaluate(
            db_session,
            org_id=org.org_id,
            predictions=[prediction],
            at=_AT + timedelta(days=day),
        )

    assert await _alerts(db_session, plot_id) == [("flood_risk", "resolved", "critical")]


async def test_a_prediction_an_open_alert_absorbed_never_reopens_it_after_the_close(
    db_session: AsyncSession,
) -> None:
    """#246 R3-decided-once-only-recorded-on-open: only an OPENED alert carries a
    prediction's evidence, so a prediction that ended in NO_ACTION — another
    month's alert already open, which is the intended behaviour of docs/06 §8 —
    left no record of having been judged.

    Month M opens the alert; month M+1 is also `alto` and is judged against the
    open one; the farmer closes that alert during M+1. The next morning the run
    passes M+1's stored row again, and with no record and no open alert the
    decision returned OPEN — the alert the farmer closed came back from evidence
    the run had already judged.

    An alert that was already open when a prediction was ISSUED absorbed that
    prediction's decision, and the alert's own window (`opened_at`..
    `resolved_at`) is what keeps that true after the farmer closed it."""
    org = await _make_org(db_session)
    cell_id = await _cell(db_session)
    _, plot_id = await _make_plot(db_session, org=org, cell_id=cell_id)

    await _evaluate(
        db_session,
        org_id=org.org_id,
        predictions=[_evidence(cell_id=cell_id, horizon_start=date(2026, 10, 1))],
    )
    assert await _alerts(db_session, plot_id) == [("flood_risk", "open", "critical")]

    # Month M+1, also `alto`: the open alert is left open and nothing is recorded.
    next_month = _AT + timedelta(days=30)
    absorbed = _evidence(cell_id=cell_id, horizon_start=date(2026, 11, 1), issued_at=next_month)
    await _evaluate(db_session, org_id=org.org_id, predictions=[absorbed], at=next_month)
    assert await _alerts(db_session, plot_id) == [("flood_risk", "open", "critical")]

    await _close_by_hand(db_session, plot_id=plot_id, org=org, at=next_month + timedelta(days=4))
    # The next morning, over the same stored row of M+1.
    await _evaluate(
        db_session,
        org_id=org.org_id,
        predictions=[absorbed],
        at=next_month + timedelta(days=5),
    )

    assert await _alerts(db_session, plot_id) == [("flood_risk", "resolved", "critical")]


async def test_a_prediction_issued_after_a_close_still_opens_its_own_alert(
    db_session: AsyncSession,
) -> None:
    """The other side of the window: a prediction written AFTER the alert was
    closed was never judged against it, so it decides on its own — the flood is
    real and unnotified."""
    org = await _make_org(db_session)
    cell_id = await _cell(db_session)
    _, plot_id = await _make_plot(db_session, org=org, cell_id=cell_id)
    await _evaluate(db_session, org_id=org.org_id, predictions=[_evidence(cell_id=cell_id)])
    await _close_by_hand(db_session, plot_id=plot_id, org=org, at=_AT + timedelta(hours=1))

    after = _ISSUED_AT + timedelta(days=40)
    await _evaluate(
        db_session,
        org_id=org.org_id,
        predictions=[_evidence(cell_id=cell_id, horizon_start=date(2026, 11, 1), issued_at=after)],
        at=_AT + timedelta(days=41),
    )

    assert await _alerts(db_session, plot_id) == [
        ("flood_risk", "open", "critical"),
        ("flood_risk", "resolved", "critical"),
    ]


async def test_another_versions_prediction_of_the_same_month_is_new_evidence(
    db_session: AsyncSession,
) -> None:
    """docs/06 §8: promoting a version adds its own row for the same cell, event and
    month (docs/03 §Unicidad de la predicción), and that row is a prediction the
    rules have not decided: the identity of a decided prediction is the month AND
    the model that produced it, so a promotion is judged and not skipped."""
    org = await _make_org(db_session)
    cell_id = await _cell(db_session)
    _, plot_id = await _make_plot(db_session, org=org, cell_id=cell_id)
    await _evaluate(db_session, org_id=org.org_id, predictions=[_evidence(cell_id=cell_id)])
    await _close_by_hand(db_session, plot_id=plot_id, org=org, at=_AT + timedelta(hours=1))

    promoted = _evidence(cell_id=cell_id, severity="critical", model_version_id=uuid7())
    await _evaluate(db_session, org_id=org.org_id, predictions=[promoted], at=_AT)

    assert await _alerts(db_session, plot_id) == [
        ("flood_risk", "open", "critical"),
        ("flood_risk", "resolved", "critical"),
    ]


# -- the composition the daily risk job calls, over one session --


async def test_one_organization_that_fails_does_not_stop_the_others(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#246 R3-one-org-failure-aborts-every-org: one failing organization must not
    abandon the ones after it, and must not take their work with it. The
    containment is the one every other evaluator has (one cell's failure is its
    own, docs/06 §6 "Degradación"; `run_daily_risk` contains it per cell) and the
    fan-out here is per organization, so the containment has to be too.

    The failing organization is created FIRST, so its `uuid7` sorts first and the
    healthy one runs after the failure — which is what proves the session was
    recovered (D24), not just that the loop went on."""
    failing = await _make_org(db_session)
    healthy = await _make_org(db_session)
    cell_id = await _cell(db_session)
    _, failing_plot = await _make_plot(db_session, org=failing, cell_id=cell_id)
    _, healthy_plot = await _make_plot(db_session, org=healthy, cell_id=cell_id)
    decide = evaluate_risk_module.evaluate_risk_rules

    async def failing_on_one_org(**kwargs: object) -> None:
        if kwargs["org_id"] == failing.org_id:
            raise RuntimeError("this organization's alert write failed")
        await decide(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(evaluate_risk_module, "evaluate_risk_rules", failing_on_one_org)

    await build_risk_evaluation(db_session)(
        at=_AT, predictions=[_evidence(cell_id=cell_id, severity="critical")]
    )

    assert await _alerts(db_session, failing_plot) == []
    assert await _alerts(db_session, healthy_plot) == [("flood_risk", "open", "critical")]
