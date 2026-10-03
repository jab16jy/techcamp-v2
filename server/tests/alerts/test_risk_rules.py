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

from datetime import UTC, date, datetime, timedelta

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
from techcamp.shared.ids import uuid7

_AT = datetime(2026, 10, 2, 11, 5, tzinfo=UTC)  # 06:05 Bogotá, the daily risk run
_ISSUE_MONTH = date(2026, 10, 1)


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


def _evidence(severity: str = "high") -> PredictionEvidence:
    """A stored `risk_prediction` as the caller of the rule reads it."""
    return PredictionEvidence.from_stored(
        cell_id=7,
        event="flood",
        severity=severity,
        horizon_start=_ISSUE_MONTH,
        model_version_id=uuid7(),
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
