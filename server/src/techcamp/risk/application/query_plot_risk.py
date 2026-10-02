"""`GET /plots/{plot_id}/risk` use case (docs/04-api.md §Riesgo, métricas y asistente;
docs/06-diseno-detallado.md §8).

The plot's current prediction per event: the one of the month in course, or the
most recent one when the current month has none — ERA5 arrives with ~5 days of
delay, so a cell has no prediction for the new month until it is already under way
(docs/04 §Riesgo, docs/06 §8 "Datos de entrada"). Which rows are current is the
repository's decision (`SqlAlchemyRiskRepository.latest_prediction`, T6a); this use
case decides *whose*: the version being served for each event, and the plot's own
cell.

Access is the plot's, through `farms`' facade: a plot that does not exist or
belongs to another organization raises `PlotNotFoundError` (docs/04 §Riesgo: mismo
control de acceso que `/plots/{plot_id}`; docs/09-cuellos-de-botella.md
§Seguridad). Neither `risk_prediction` nor `model_version` carries `org_id` — a
prediction belongs to a `weather_cell`, which is shared reference data for every
organization (docs/03-modelo-datos.md §`municipality`, `model_version` y
`risk_prediction`, fila `org_id`) — so this check is the whole isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from techcamp.farms.application.manage_plots import resolve_plot_access
from techcamp.farms.application.ports import PlotRepository
from techcamp.identity.application.ports import MembershipRepository
from techcamp.risk.application.ports import RiskRepository
from techcamp.risk.domain.models import EventType, ModelVersion, Severity


@dataclass(frozen=True, slots=True)
class PlotRiskPrediction:
    """One event's prediction for a plot's cell, with the version that produced it
    (docs/04-api.md §Riesgo: `{ event_type, horizon_start, horizon_days,
    probability, severity, top_factors, model_version, created_at }`).

    `version_name`, `version_version`, `is_baseline` and `metrics` are the nested
    `model_version` object, flattened because a dataclass cannot nest another one
    here without inventing a type the adapter does not need.
    """

    event_type: EventType
    horizon_start: date
    horizon_days: int
    probability: float
    severity: Severity
    top_factors: list[dict[str, Any]]
    version_name: str
    version_version: str
    is_baseline: bool
    metrics: dict[str, Any] | None
    created_at: datetime


def _metrics_for(version: ModelVersion) -> dict[str, Any] | None:
    """The numbers this endpoint reports as `metrics`.

    docs/04 §Riesgo names the field `metrics` and docs/03-modelo-datos.md:319-333
    stores two columns: a model's own `metrics` and the `baseline_metrics` of a
    registered baseline. A served baseline would otherwise report `null` where the
    number that justified serving it lives, so `metrics` wins when it is there and
    `baseline_metrics` answers for a baseline.
    """
    if version.metrics is not None:
        return version.metrics
    return version.baseline_metrics if version.is_baseline else None


async def query_plot_risk(
    *,
    user_id: UUID,
    plot_id: UUID,
    plots: PlotRepository,
    memberships: MembershipRepository,
    risk: RiskRepository,
) -> list[PlotRiskPrediction]:
    """The plot's risk predictions, one per event that has one.

    A plot without a cell answers `[]` (una parcela sin celda responde `[]`,
    docs/04 §Riesgo), and so does an event with no registered version or no
    prediction yet (un evento sin predicción no aparece): the client shows what
    exists instead of a `low` invented for an event nobody predicted.
    """
    plot, _role = await resolve_plot_access(
        user_id=user_id, plot_id=plot_id, plots=plots, memberships=memberships
    )
    if plot.weather_cell_id is None:
        return []

    predictions: list[PlotRiskPrediction] = []
    for event in EventType:
        version = await risk.served_version(event.version_name)
        if version is None:
            continue
        prediction = await risk.latest_prediction(plot.weather_cell_id, event, version.id)
        if prediction is None:
            continue
        predictions.append(
            PlotRiskPrediction(
                event_type=prediction.event_type,
                horizon_start=prediction.horizon_start,
                horizon_days=prediction.horizon_days,
                probability=prediction.probability,
                severity=prediction.severity,
                top_factors=prediction.top_factors,
                version_name=version.name,
                version_version=version.version,
                is_baseline=version.is_baseline,
                metrics=_metrics_for(version),
                created_at=prediction.created_at,
            )
        )
    return predictions
