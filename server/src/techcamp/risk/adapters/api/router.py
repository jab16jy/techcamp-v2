"""Climate-risk REST endpoints (docs/04-api.md §Riesgo, métricas y asistente;
docs/06-diseno-detallado.md §8)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from techcamp.farms.adapters.api.deps import PlotRepoDep
from techcamp.farms.domain.errors import PlotNotFoundError
from techcamp.identity.adapters.api.deps import CurrentUserId, MembershipRepoDep
from techcamp.risk.adapters.api.deps import RiskRepoDep
from techcamp.risk.application.query_plot_risk import query_plot_risk
from techcamp.shared.errors import ProblemError

router = APIRouter(tags=["risk"])


class TopFactorView(BaseModel):
    """One entry of `top_factors` (docs/04-api.md §Riesgo): `{ feature, value,
    contribution }`, the three features with the largest contribution and their
    value (docs/08-ml.md §M2 "Factores")."""

    feature: str
    value: float
    contribution: float


class ModelVersionView(BaseModel):
    """The nested `model_version` (docs/04-api.md §Riesgo): which registered
    version answered, and whether it is a baseline (docs/08-ml.md §M2 "Línea base
    servida"). Every prediction is traceable to one row of `model_version`
    (docs/06-diseno-detallado.md §8)."""

    name: str
    version: str
    is_baseline: bool
    metrics: dict[str, Any] | None


class RiskPredictionView(BaseModel):
    """`RiskPrediction` (docs/04-api.md §Riesgo)."""

    event_type: str
    horizon_start: date
    horizon_days: int
    probability: float
    severity: str
    top_factors: list[TopFactorView]
    model_version: ModelVersionView
    created_at: datetime


@router.get("/plots/{plot_id}/risk", response_model=list[RiskPredictionView])
async def get_plot_risk(
    plot_id: UUID,
    user_id: CurrentUserId,
    plots: PlotRepoDep,
    memberships: MembershipRepoDep,
    risk: RiskRepoDep,
) -> list[RiskPredictionView]:
    """docs/04-api.md §Riesgo: `GET /plots/{plot_id}/risk → RiskPrediction[]`.

    The plot's current prediction per event (`flood`, `drought`) of the served
    version, or the most recent one when the current month has none. Unknown plot
    or another organization's plot answers 404, never 403
    (docs/09-cuellos-de-botella.md §Seguridad); a plot without a cell answers `[]`.
    """
    try:
        items = await query_plot_risk(
            user_id=user_id,
            plot_id=plot_id,
            plots=plots,
            memberships=memberships,
            risk=risk,
        )
    except PlotNotFoundError as exc:
        raise ProblemError(status=404, title="Plot not found") from exc

    return [
        RiskPredictionView(
            event_type=item.event_type.value,
            horizon_start=item.horizon_start,
            horizon_days=item.horizon_days,
            probability=item.probability,
            severity=item.severity.value,
            top_factors=[TopFactorView(**factor) for factor in item.top_factors],
            model_version=ModelVersionView(
                name=item.version_name,
                version=item.version_version,
                is_baseline=item.is_baseline,
                metrics=item.metrics,
            ),
            created_at=item.created_at,
        )
        for item in items
    ]
