"""Composition of the model-rule evaluation (docs/06-diseno-detallado.md §8
"Alertas").

`risk` writes the predictions and may import `alerts.application` but never
`alerts.adapters` (docs/05-arquitectura.md §Solo la fachada pública), so the
concrete repositories are built here and injected into the daily job by the
composition root (`techcamp/worker.py`) — the way `techcamp/ingestor.py`
composes the reading rules into the telemetry flush.

The fan-out is the one every alerts sweep does: one call per organization that
has a plot, which is what lets every read inside it keep its `org_id` (D21,
docs/09-cuellos-de-botella.md#seguridad).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.jobs import orgs_with_plots
from techcamp.alerts.adapters.repositories import (
    SqlAlchemyAlertRepository,
    SqlAlchemyAlertRuleRepository,
)
from techcamp.alerts.application.evaluate_risk_rules import (
    RiskRuleEvaluation,
    evaluate_risk_rules,
)
from techcamp.alerts.domain.models import PredictionEvidence
from techcamp.farms.adapters.repositories import SqlAlchemyFarmRepository, SqlAlchemyPlotRepository

logger = logging.getLogger(__name__)


def build_risk_evaluation(session: AsyncSession) -> RiskRuleEvaluation:
    """The evaluation the daily risk job runs right after it wrote its
    predictions, over the organizations that have a plot.

    Nothing is decided here: the use case keeps every read inside the
    organization it was given, and the repositories are the same `AsyncSession`
    the run opened, so the alerts join the run's unit of work and the commit at
    the end is what publishes them.
    """

    async def evaluate(*, at: datetime, predictions: Sequence[PredictionEvidence]) -> None:
        rules = SqlAlchemyAlertRuleRepository(session)
        for org_id in await orgs_with_plots(session):
            try:
                await evaluate_risk_rules(
                    org_id=org_id,
                    at=at,
                    predictions=predictions,
                    rules=rules,
                    farms=SqlAlchemyFarmRepository(session),
                    plots=SqlAlchemyPlotRepository(session),
                    alerts=SqlAlchemyAlertRepository(session),
                )
                # One commit per organization: an organization that fails takes
                # only its own uncommitted work with it, and the ones decided
                # before it are published.
                await session.commit()
            except Exception:
                # One organization's failure is its own (docs/06 §6
                # "Degradación"): the run logs it, puts the shared unit of work
                # back in a usable state (D24 — a failed statement leaves the
                # session in a failed-transaction state, so every read after it
                # would raise) and the organizations after it are still decided.
                logger.warning(
                    "alerts: the model rules of org %s could not be evaluated, the others go on",
                    org_id,
                    exc_info=True,
                )
                await session.rollback()

    return evaluate
