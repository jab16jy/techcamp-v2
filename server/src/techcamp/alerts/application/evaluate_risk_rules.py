"""The model rules of docs/06 §3 over the predictions one run just wrote
(docs/06-diseno-detallado.md §8 "Alertas").

The same shape as `evaluate_weather_rules` and for the same reason: the rules are
decided per plot, so every read below keeps the organization's `org_id` (D21,
docs/09-cuellos-de-botella.md#seguridad), and `org_id` is a parameter a caller
cannot leave out.

`predictions` are the rows the caller JUST wrote, and only those. That is what
makes "a cell or event with no new prediction this run leaves the alert
untouched" true by construction instead of by a date comparison: an ERA5 month
that never completed produces no prediction to decide on, and silence is not a
prediction below `alto` (docs/06 §8).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from techcamp.alerts.application.ports import AlertRepository, AlertRuleRepository
from techcamp.alerts.application.use_cases import open_alert, resolve_automatically
from techcamp.alerts.domain import (
    RISK_RULE_CODES,
    AlertAction,
    PredictionEvidence,
    decide_risk_rule,
)
from techcamp.farms.application.ports import FarmRepository, PlotRepository

_MAX_FARMS_PER_ORG = 500
"""ponytail: one `list_for_org` page covers every farm of one organization at
this project's scale (the same reasoning and constant as the two evaluators
already in this package)."""


class RiskRuleEvaluation(Protocol):
    """What the daily risk job calls once per run, after it wrote its predictions.

    A protocol rather than a bare function so the calling module names what it
    needs without importing the concrete repositories that satisfy it (ADR-0002):
    `risk` may depend on `alerts.application` and never `alerts.adapters`
    (docs/05-arquitectura.md §Solo la fachada pública), so the composition root
    builds the implementation and injects it.
    """

    async def __call__(self, *, at: datetime, predictions: Sequence[PredictionEvidence]) -> None:
        """Decide every rule of the predictions `at` decides, and return nothing.

        `predictions` are the rows the caller JUST wrote: nothing else is
        evidence, so an implementation must not read back what is stored.
        """
        ...


async def evaluate_risk_rules(
    *,
    org_id: UUID,
    at: datetime,
    predictions: Sequence[PredictionEvidence],
    rules: AlertRuleRepository,
    farms: FarmRepository,
    plots: PlotRepository,
    alerts: AlertRepository,
) -> None:
    """Decide `flood_risk` / `drought_risk` for every plot of `org_id` from the
    predictions this run wrote.

    A prediction belongs to a CELL (docs/03-modelo-datos.md §`risk_prediction`),
    and docs/06 §8 opens the alert on every plot of that cell, so the plots are
    reached the other way around: the org's own farms and plots, and each plot
    with a prediction about its own cell. That is what keeps the fan-out inside
    the organization — neighbouring plots may share a cell (docs/00 glosario), so
    the cell id alone cannot say whose alert a prediction is about.
    """
    rules_by_code = {rule.code: rule for rule in await rules.list_for_org(org_id)}
    by_cell: dict[int | None, list[PredictionEvidence]] = {}
    for prediction in predictions:
        code = RISK_RULE_CODES.get(prediction.event)
        if code is None or code not in rules_by_code:
            # An event with no rule of its own, or a rule this organization does
            # not read: nothing to decide, never a guessed rule.
            continue
        by_cell.setdefault(prediction.cell_id, []).append(prediction)

    if not by_cell:
        return

    for farm in await farms.list_for_org(org_id, limit=_MAX_FARMS_PER_ORG):
        for plot in await plots.list_for_farm(farm.id, org_id):
            for prediction in by_cell.get(plot.weather_cell_id, ()):
                rule = rules_by_code[RISK_RULE_CODES[prediction.event]]
                # `get_non_resolved_for_target` never returns a resolved alert (the
                # partial unique index's own scope), so it is the "current" alert.
                current = await alerts.get_non_resolved_for_target(
                    rule_id=rule.id, org_id=plot.org_id, plot_id=plot.id, node_id=None
                )
                decision = decide_risk_rule(
                    severity=prediction.severity, current_alert=current, at=at
                )
                match decision.action:
                    case AlertAction.OPEN:
                        await open_alert(
                            rule=rule,
                            at=at,
                            alerts=alerts,
                            plot_id=plot.id,
                            evidence={
                                "event": prediction.event,
                                "severity": prediction.severity.value,
                                "horizon_start": prediction.horizon_start.isoformat(),
                                # docs/06 §8: "toda alerta se puede rastrear hasta
                                # el modelo exacto".
                                "model_version_id": str(prediction.model_version_id),
                            },
                        )
                    case AlertAction.RESOLVE:
                        assert decision.alert is not None, "a resolve decision carries the alert"
                        await resolve_automatically(
                            alert_id=decision.alert.id,
                            org_id=plot.org_id,
                            farm_id=plot.farm_id,
                            at=at,
                            alerts=alerts,
                        )
                    case AlertAction.UPGRADE | AlertAction.NO_ACTION:
                        # The rule's severity is `critical` from the start (docs/06
                        # §3), so there is no upgrade to make, and a prediction that
                        # is still at `alto` changes nothing.
                        pass
