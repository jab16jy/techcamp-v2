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
    RISK_RULE_EVENTS,
    AlertAction,
    PredictionEvidence,
    decide_risk_rule,
    prediction_evidence,
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
    predictions stored for a month.

    A prediction belongs to a CELL (docs/03-modelo-datos.md §`risk_prediction`),
    and docs/06 §8 opens the alert on every plot of that cell, so the plots are
    reached the other way around: the org's own farms and plots, and each plot
    with the predictions about its own cell. That is what keeps the fan-out inside
    the organization — neighbouring plots may share a cell (docs/00 glosario), so
    the cell id alone cannot say whose alert a prediction is about.
    """
    org_rules = await rules.list_for_org(org_id)
    by_cell_event: dict[tuple[int | None, str], list[PredictionEvidence]] = {}
    for prediction in predictions:
        by_cell_event.setdefault((prediction.cell_id, prediction.event), []).append(prediction)

    if not by_cell_event:
        return

    for farm in await farms.list_for_org(org_id, limit=_MAX_FARMS_PER_ORG):
        for plot in await plots.list_for_farm(farm.id, org_id):
            # The organization's rules one by one, like `evaluate_weather_rules`:
            # `uq_alert_rule_factory_code` covers only `org_id IS NULL`, so a rule
            # the organization added for itself sits next to the factory one and
            # both are decided. An event with no rule, or a rule that is not a
            # model rule, is never decided on a prediction.
            for rule in org_rules:
                event = RISK_RULE_EVENTS.get(rule.code)
                if event is None:
                    continue
                for prediction in by_cell_event.get((plot.weather_cell_id, event), ()):
                    # The decision comes FIRST. A stored prediction is decided once
                    # per plot and rule (docs/06 §8): the run hands the same row
                    # over every morning of its month, so without a record the
                    # morning after a farmer closed the alert by hand
                    # (docs/06 §3 "cierre manual") would open it again from
                    # evidence that was already judged — either by the alert that
                    # carries this prediction's identity, or by the alert that was
                    # open when the prediction was issued and answered it with
                    # NO_ACTION, leaving no other record.
                    #
                    # That record suppresses OPEN and NO_ACTION only. A RESOLVE is
                    # never suppressed: the daily run STORES a prediction and
                    # evaluates it minutes later, so the resolving row of the next
                    # month is always issued while the alert it has to resolve is
                    # still open — exactly the absorbed case — and honouring the
                    # record there would leave the alert open for the rest of the
                    # month (docs/06 §8: "la resuelve en la primera predicción
                    # nueva por debajo de `alto`").
                    #
                    # `get_non_resolved_for_target` never returns a resolved alert
                    # (the partial unique index's own scope), so it is the "current"
                    # alert.
                    current = await alerts.get_non_resolved_for_target(
                        rule_id=rule.id, org_id=plot.org_id, plot_id=plot.id, node_id=None
                    )
                    decision = decide_risk_rule(
                        severity=prediction.severity, current_alert=current, at=at
                    )
                    if decision.action is not AlertAction.RESOLVE:
                        decided = await alerts.get_decided_for_target(
                            rule_id=rule.id,
                            org_id=plot.org_id,
                            plot_id=plot.id,
                            prediction=prediction,
                        )
                        if decided is not None:
                            continue
                    match decision.action:
                        case AlertAction.OPEN:
                            await open_alert(
                                rule=rule,
                                at=at,
                                alerts=alerts,
                                plot_id=plot.id,
                                evidence=prediction_evidence(prediction),
                            )
                        case AlertAction.RESOLVE:
                            assert decision.alert is not None, "a resolve carries the alert"
                            await resolve_automatically(
                                alert_id=decision.alert.id,
                                org_id=plot.org_id,
                                farm_id=plot.farm_id,
                                at=at,
                                alerts=alerts,
                            )
                        case AlertAction.UPGRADE | AlertAction.NO_ACTION:
                            # The rule's severity is `critical` from the start
                            # (docs/06 §3), so there is no upgrade to make, and a
                            # prediction that is still at `alto` changes nothing.
                            pass
