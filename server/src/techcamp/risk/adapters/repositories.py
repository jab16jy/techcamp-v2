"""Postgres repository for climate risk (docs/03-modelo-datos.md §`model_version`
y `risk_prediction`; docs/08-ml.md §M2 "Línea base servida"; docs/04 §Riesgo,
métricas y asistente).

No `org_id` filter anywhere: a prediction belongs to a `weather_cell`, and a cell
is shared reference data, the same row for every organization
(docs/03-modelo-datos.md §`municipality`, `model_version` y `risk_prediction`,
fila `org_id`). Isolation is enforced where this data
leaves the server, at the plot endpoint (T6b) — a read here is scoped to the
cell, the event and the version instead, which is what keeps a plot from being
served another cell's risk.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import Row, case, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.risk.adapters.orm import ModelVersionRow, RiskPredictionRow
from techcamp.risk.domain.models import EventType, ModelVersion, RiskPrediction, Severity
from techcamp.shared.dates import local_today

_VERSION_COLUMNS = (
    ModelVersionRow.id,
    ModelVersionRow.name,
    ModelVersionRow.version,
    ModelVersionRow.artifact_uri,
    ModelVersionRow.metrics,
    ModelVersionRow.baseline_metrics,
    ModelVersionRow.is_baseline,
    ModelVersionRow.artifact_sha256,
    ModelVersionRow.dataset_hash,
    ModelVersionRow.git_commit,
    ModelVersionRow.thresholds,
    ModelVersionRow.promoted,
    ModelVersionRow.promotion_reason,
    ModelVersionRow.created_at,
)

_PREDICTION_COLUMNS = (
    RiskPredictionRow.id,
    RiskPredictionRow.cell_id,
    RiskPredictionRow.model_version_id,
    RiskPredictionRow.event_type,
    RiskPredictionRow.horizon_start,
    RiskPredictionRow.horizon_days,
    RiskPredictionRow.probability,
    RiskPredictionRow.severity,
    RiskPredictionRow.top_factors,
    RiskPredictionRow.created_at,
)


def _version_from_row(row: Row[Any]) -> ModelVersion:
    return ModelVersion(
        id=row.id,
        name=row.name,
        version=row.version,
        artifact_uri=row.artifact_uri,
        metrics=row.metrics,
        baseline_metrics=row.baseline_metrics,
        is_baseline=row.is_baseline,
        artifact_sha256=row.artifact_sha256,
        dataset_hash=row.dataset_hash,
        git_commit=row.git_commit,
        thresholds=row.thresholds,
        promoted=row.promoted,
        promotion_reason=row.promotion_reason,
        created_at=row.created_at,
    )


def _prediction_from_row(row: Row[Any]) -> RiskPrediction:
    return RiskPrediction(
        id=row.id,
        cell_id=row.cell_id,
        model_version_id=row.model_version_id,
        event_type=EventType(row.event_type),
        horizon_start=row.horizon_start,
        horizon_days=row.horizon_days,
        probability=row.probability,
        severity=Severity(row.severity),
        top_factors=row.top_factors,
        created_at=row.created_at,
    )


class SqlAlchemyRiskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def served_version(self, name: str) -> ModelVersion | None:
        """The version the risk job must serve for this event, or `None`.

        The promoted version first: it is the one that passed the gate
        (docs/08 §M2 "Línea base servida"). Only when no model is promoted does
        a registered baseline (`is_baseline`) serve — and `None` when neither
        exists, which is what keeps the job from writing predictions of an event
        nothing is registered for (docs/06 §8).

        Two promoted versions of one name cannot exist (the partial unique
        index), but two registered baselines can, and the docs name no winner.
        The most recently registered one serves, `id` breaking the tie: `uuid7`
        sorts by creation time, so the order is deterministic without inventing
        a column the docs do not have.
        """
        # A registered but unpromoted candidate has not passed the gate: it never
        # serves, neither over the promoted version nor over the baseline.
        for served_rule in (
            ModelVersionRow.promoted.is_(True),
            ModelVersionRow.is_baseline.is_(True),
        ):
            result = await self._session.execute(
                select(*_VERSION_COLUMNS)
                .where(ModelVersionRow.name == name, served_rule)
                .order_by(ModelVersionRow.created_at.desc(), ModelVersionRow.id.desc())
                .limit(1)
            )
            row = result.one_or_none()
            if row is not None:
                return _version_from_row(row)
        return None

    async def insert_version(self, version: ModelVersion) -> ModelVersion:
        """Store one registered model or baseline and return the row as it reads back.

        ADR-0020 paso 10, docs/08-ml.md §Reglas de gobierno "Trazabilidad". The row is
        written after its artifact is already in object storage, so a version a
        prediction names exists before anything points at it.

        There is no `ON CONFLICT` clause and there is no unique index on
        `(name, version)` to hang one on: `served_version` is what decides which row
        serves an event, and it breaks a tie deterministically (most recent first). Two
        rows claiming the same version string would therefore be decided by
        `created_at`, not refused here, so `insert_version` returns what it wrote and
        leaves that choice to the caller that asked to register twice — registration
        checks `versions_for` first and refuses on its own.
        """
        row_id = version.id
        result = await self._session.execute(
            pg_insert(ModelVersionRow)
            .values(
                id=row_id,
                name=version.name,
                version=version.version,
                artifact_uri=version.artifact_uri,
                metrics=version.metrics,
                baseline_metrics=version.baseline_metrics,
                is_baseline=version.is_baseline,
                artifact_sha256=version.artifact_sha256,
                dataset_hash=version.dataset_hash,
                git_commit=version.git_commit,
                thresholds=dict(version.thresholds),
                promoted=version.promoted,
                promotion_reason=version.promotion_reason,
                created_at=version.created_at,
            )
            .returning(ModelVersionRow.id)
        )
        await self._session.commit()
        stored = await self._session.execute(
            select(*_VERSION_COLUMNS).where(ModelVersionRow.id == result.scalar_one())
        )
        return _version_from_row(stored.one())

    async def versions_for(self, name: str) -> list[ModelVersion]:
        """Every registered version of one event, most recent first.

        Registration reads this before writing, so a version string already registered is
        never taken twice by a second run (ADR-0020 paso 10): docs/08 §Reglas de gobierno
        "Reversión" keeps the artifacts, so re-registering is a new version string, never
        an overwrite.

        The order is `created_at` then `id`, the same one `served_version` uses, so
        "most recent" means one thing in both reads: `uuid7` sorts by creation time, which
        is why no extra column is needed.
        """
        result = await self._session.execute(
            select(*_VERSION_COLUMNS)
            .where(ModelVersionRow.name == name)
            .order_by(ModelVersionRow.created_at.desc(), ModelVersionRow.id.desc())
        )
        return [_version_from_row(row) for row in result]

    async def insert_prediction(self, prediction: RiskPrediction) -> bool:
        """Store one prediction, reporting whether this call wrote it.

        `ON CONFLICT DO NOTHING` on `(cell_id, event_type, horizon_start,
        model_version_id)` is the idempotence the docs ask for: la predicción de
        una celda, evento y mes se escribe una vez; las corridas siguientes del
        mismo mes no la repiten (docs/06 §8). The rerun is not a correction —
        a different probability for a month already predicted is a different
        month's job, or a promotion, which has its own row — so the stored
        values are left untouched and the caller sees `False`.
        """
        result = await self._session.execute(
            pg_insert(RiskPredictionRow)
            .values(
                id=prediction.id,
                cell_id=prediction.cell_id,
                model_version_id=prediction.model_version_id,
                event_type=prediction.event_type.value,
                horizon_start=prediction.horizon_start,
                horizon_days=prediction.horizon_days,
                probability=prediction.probability,
                severity=prediction.severity.value,
                top_factors=prediction.top_factors,
                created_at=prediction.created_at,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    RiskPredictionRow.cell_id,
                    RiskPredictionRow.event_type,
                    RiskPredictionRow.horizon_start,
                    RiskPredictionRow.model_version_id,
                ]
            )
            .returning(RiskPredictionRow.id)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def stored_predictions(
        self,
        *,
        horizon_start: date,
        cell_ids: Sequence[int],
        model_version_ids: Sequence[uuid.UUID],
    ) -> list[RiskPrediction]:
        """The stored predictions of one month, over `cell_ids` and the served
        versions.

        One read for the whole month, the shape `active_cells` already has on the
        weather side: a `risk_prediction` belongs to a cell and not to an
        organization (docs/03-modelo-datos.md §`risk_prediction`), and the fan-out
        that keeps every alert inside an organization is the one the caller does
        after this — the plots of each organization, filtered to their own cell.

        Ordered by cell and event so two runs of the same month hand the caller the
        same rows in the same order.
        """
        if not cell_ids or not model_version_ids:
            return []
        result = await self._session.execute(
            select(*_PREDICTION_COLUMNS)
            .where(
                RiskPredictionRow.horizon_start == horizon_start,
                RiskPredictionRow.cell_id.in_(cell_ids),
                RiskPredictionRow.model_version_id.in_(model_version_ids),
            )
            .order_by(RiskPredictionRow.cell_id, RiskPredictionRow.event_type)
        )
        return [_prediction_from_row(row) for row in result]

    async def latest_prediction(
        self, cell_id: int, event_type: EventType, model_version_id: uuid.UUID
    ) -> RiskPrediction | None:
        """The cell's current prediction for one event, or its most recent one.

        The current month is the month of the day in America/Bogota
        (`local_today`), never the day the request arrives in UTC: ERA5 arrives
        with ~5 days of delay, so a cell has no prediction for the new month
        until the month it predicts is already under way, and until then the
        most recent prediction is what the plot endpoint serves
        (docs/04 §Riesgo, métricas y asistente; docs/06 §8).

        `model_version_id` is the version being served, so a promotion does not
        show a cell a month the new version has not predicted yet. Ordered by
        `horizon_start`, then `created_at`: two predictions for the same month
        are the same month, and the newest row is the one that answered last.
        """
        month_start = local_today().replace(day=1)
        result = await self._session.execute(
            select(*_PREDICTION_COLUMNS)
            .where(
                RiskPredictionRow.cell_id == cell_id,
                RiskPredictionRow.event_type == event_type.value,
                RiskPredictionRow.model_version_id == model_version_id,
            )
            .order_by(
                case((RiskPredictionRow.horizon_start == month_start, 0), else_=1),
                RiskPredictionRow.horizon_start.desc(),
                RiskPredictionRow.created_at.desc(),
            )
            .limit(1)
        )
        row = result.one_or_none()
        return None if row is None else _prediction_from_row(row)
