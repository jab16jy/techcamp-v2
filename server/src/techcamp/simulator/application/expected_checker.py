"""The `expected` checker: a scenario's oracle, judged against live state
(docs/06-diseno-detallado.md:485-486; feature doc `techcamp-v2-e16-simulator.md`
task T4).

`expected` turns each scenario into an end-to-end test, so the checker is the
assertion engine the harness (T5) and the scenario wirings (T6) consume. Two
rules shape it:

- **The comparison is exact.** docs/06:473-480 states what each scenario
  demonstrates, block by block, so an alert the block does not name is a broken
  wiring, not a bonus — and a missing one is a demo that would stand silent in
  front of the room. Both directions are findings.
- **Judging is pure.** `check_expected` opens no socket and no session (asserted
  the same way the scenario library's own no-socket test does); the one async
  function, `collect_observed_state`, is the only I/O edge, and it reads through
  the repositories the stack already serves — nothing new is queried about the
  domain, because the oracle may only name what exists (scenario_schema's own
  rule, docs/06:485).

Node-scoped alerts (`node_offline`, docs/06:477) live on the node, and the plot
listing never returns them, so the adapter asks each of the plot's own nodes per
rule with the same per-target read the evaluators decide on — the node ids are
the scenario's own, which the loader's `202` already answers.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from techcamp.alerts.application.ports import AlertRepository, AlertRuleRepository
from techcamp.irrigation.application.ports import IrrigationRecommendationRepository
from techcamp.simulator.scenario_schema import Scenario

_MISSING_RECOMMENDATION = "no recommendation stored"
"""What a verdict reports when the day the caller asked has no stored
recommendation: every block carries `irrigation`, so this is a failed
assertion, never a silent pass."""


@dataclass(frozen=True, slots=True)
class ObservedScenarioState:
    """What the stack serves for one scenario plot, as the checker judges it.

    `irrigation_kind` is `None` when the asked day has no stored recommendation;
    `alert_codes` is already unique and non-resolved only, so a resolved alert
    is no longer part of what a scenario asserts.
    """

    alert_codes: tuple[str, ...]
    irrigation_kind: str | None
    advice: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExpectedFinding:
    """One failed assertion, named the way a report prints it."""

    kind: str
    """`missing_alert` | `unexpected_alert` | `irrigation_kind` |
    `missing_advice` | `unexpected_advice`."""
    expected: str
    observed: str


@dataclass(frozen=True, slots=True)
class ExpectedVerdict:
    """The checker's answer: pass or every finding, named."""

    scenario: str
    passed: bool
    findings: tuple[ExpectedFinding, ...]

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe: T5's harness prints this, so only str/bool/list cross it."""
        return {
            "scenario": self.scenario,
            "passed": self.passed,
            "findings": [
                {"kind": finding.kind, "expected": finding.expected, "observed": finding.observed}
                for finding in self.findings
            ],
        }


def check_expected(scenario: Scenario, observed: ObservedScenarioState) -> ExpectedVerdict:
    """Judge one scenario's `expected` block against the observed state.

    The block's own order drives the report: a missing alert is named in the
    order the scenario promises it, so the printed findings read like the YAML.
    """
    expected = scenario.expected
    findings: list[ExpectedFinding] = []

    expected_alerts = set(expected.alerts)
    observed_alerts = set(observed.alert_codes)
    findings.extend(
        ExpectedFinding("missing_alert", code, _observed_alerts_summary(observed.alert_codes))
        for code in expected.alerts
        if code not in observed_alerts
    )
    findings.extend(
        ExpectedFinding("unexpected_alert", _expected_alerts_summary(expected.alerts), code)
        for code in observed.alert_codes
        if code not in expected_alerts
    )

    if observed.irrigation_kind is None or observed.irrigation_kind != expected.irrigation:
        findings.append(
            ExpectedFinding(
                "irrigation_kind",
                expected.irrigation,
                observed.irrigation_kind
                if observed.irrigation_kind is not None
                else _MISSING_RECOMMENDATION,
            )
        )

    expected_advice = set(expected.advice)
    observed_advice = set(observed.advice)
    findings.extend(
        ExpectedFinding("missing_advice", code, _observed_advice_summary(observed.advice))
        for code in expected.advice
        if code not in observed_advice
    )
    findings.extend(
        ExpectedFinding("unexpected_advice", _expected_advice_summary(expected.advice), code)
        for code in observed.advice
        if code not in expected_advice
    )

    return ExpectedVerdict(scenario=scenario.name, passed=not findings, findings=tuple(findings))


def _observed_alerts_summary(codes: tuple[str, ...]) -> str:
    return ", ".join(codes) if codes else "no alert open"


def _expected_alerts_summary(codes: tuple[str, ...]) -> str:
    return ", ".join(codes) if codes else "no alert expected"


def _observed_advice_summary(codes: tuple[str, ...]) -> str:
    return ", ".join(codes) if codes else "no advice given"


def _expected_advice_summary(codes: tuple[str, ...]) -> str:
    return ", ".join(codes) if codes else "no advice expected"


async def collect_observed_state(
    *,
    org_id: UUID,
    plot_id: UUID,
    day: date,
    node_ids: Sequence[UUID] = (),
    alerts: AlertRepository,
    rules: AlertRuleRepository,
    recommendations: IrrigationRecommendationRepository,
) -> ObservedScenarioState:
    """Gather the live state one scenario plot serves, org-scoped throughout.

    The plot listing answers the plot-scoped alerts (critical first, then
    newest); the node-scoped ones never pass its `plot_id` filter, so each of
    the plot's own nodes is asked per rule with `get_non_resolved_for_target` —
    the same read `open_alert` idempotence and every evaluator's resume path
    use. A day without a stored recommendation is `None`, and the caller — not
    this function — decides which day that is (the scenario's own anchor, T5/T6).
    """
    open_alerts = await alerts.list_open_for_plots((plot_id,), (org_id,))
    codes = [alert.rule_code for alert in open_alerts]

    for rule in await rules.list_for_org(org_id):
        for node_id in node_ids:
            node_alert = await alerts.get_non_resolved_for_target(
                rule_id=rule.id, org_id=org_id, plot_id=None, node_id=node_id
            )
            if node_alert is not None:
                codes.append(node_alert.rule_code)

    recommendation = await recommendations.get_for_plot(plot_id=plot_id, day=day, org_id=org_id)
    return ObservedScenarioState(
        alert_codes=tuple(dict.fromkeys(codes)),
        irrigation_kind=recommendation.kind.value if recommendation is not None else None,
        advice=tuple(advice.value for advice in recommendation.advice)
        if recommendation is not None
        else (),
    )


__all__ = [
    "ExpectedFinding",
    "ExpectedVerdict",
    "ObservedScenarioState",
    "check_expected",
    "collect_observed_state",
]
