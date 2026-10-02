"""Ports of the climate-risk application layer (docs/05-arquitectura.md §Estructura
hexagonal de cada módulo; docs/06-diseno-detallado.md §8).

The daily job (T6b) needs three things it does not own: the archive window of a
cell, the rows risk is stored in, and the predictor of the version being served.
Each is a protocol here and an adapter elsewhere, and none of them is a
speculative abstraction — the first two are external I/O (ADR-0002) and the third
is the model artifact of a registered version (docs/08-ml.md §M2 "Línea base
servida").

`PredictorRegistry` is here rather than in an adapter because resolving which
predictor answers for a version is application policy: a version nothing is
registered for has no prediction this run, never a fabricated probability
(docs/06-diseno-detallado.md §8 "Sin modelo promovido").
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from techcamp.risk.domain.models import ArchiveDay, EventType, ModelVersion, RiskPrediction


@dataclass(frozen=True, slots=True)
class RiskCell:
    """One 0.1° cell at least one plot points at, with the centre the archive is
    asked about (docs/06-diseno-detallado.md §6 "celdas de 0,1°"; §8 "Datos de
    entrada").

    `id` is the `weather_cell` the prediction belongs to (docs/03-modelo-datos.md
    §Unicidad de la predicción: one row per cell, event, month and version), and
    `(lat, lon)` are that cell's rounded coordinates — the same point the soil
    and the weather queries use, so a plot's risk and its weather never
    contradict each other.
    """

    id: int
    lat: float
    lon: float


class RiskArchivePort(Protocol):
    """The Open-Meteo inputs of one cell (docs/06-diseno-detallado.md §8 "Datos
    de entrada"; docs/08-ml.md §M2 "Clima", "Elevación y pendiente").

    ADR-0002: external I/O needs a test double. `risk/adapters/
    open_meteo_archive.py` (T6a) satisfies this protocol structurally, and
    `models=era5` and `timezone=America/Bogota` are that adapter's own
    invariants rather than arguments a caller could get wrong (D-T0.1, D-T0.6).
    """

    async def fetch_daily(
        self, lat: float, lon: float, *, start_day: date, end_day: date
    ) -> list[ArchiveDay]: ...

    async def fetch_elevations(
        self, points: Sequence[tuple[float, float]]
    ) -> list[float | None]: ...


class RiskRepository(Protocol):
    """The registered versions and the predictions of a cell (docs/03-modelo-datos.md
    §`model_version` y `risk_prediction`).

    ADR-0002: the database is external I/O, so the use case depends on this and
    not on `adapters/repositories.py` (T6a).
    """

    async def served_version(self, name: str) -> ModelVersion | None: ...

    async def insert_prediction(self, prediction: RiskPrediction) -> bool: ...

    async def latest_prediction(
        self, cell_id: int, event_type: EventType, model_version_id: uuid.UUID
    ) -> RiskPrediction | None: ...


@dataclass(frozen=True, slots=True)
class CellTransactions:
    """What the run does with the caller's transaction (docs/05 §Estructura
    hexagonal de cada módulo: the application layer handles the transactions).

    **Nothing open across a provider call.** A run walks every cell that has a plot,
    and each one costs two provider calls with a 10 s timeout and up to three
    retries. A `SELECT` autobegins a transaction, so the reads the run makes are
    closed before it reaches for the archive: a transaction still open during
    `fetch_daily` pins a pooled connection for the length of the HTTP call and its
    retries (docs/09-cuellos-de-botella.md). The run therefore commits once after
    resolving the served versions, before the first cell.

    **Commit per cell.** One transaction around the whole run would discard every
    prediction already written when a drop comes late in it. Per cell, the loss is
    that cell's. `SqlAlchemyRiskRepository.insert_prediction` also commits its own
    row, so the isolation the run relies on is finer still; the per-cell commit is
    what keeps the count and the rollback below meaningful for any other adapter.

    **Rollback before continuing.** A statement the database rejected leaves its
    transaction aborted, and every later statement of an aborted transaction fails
    too — so after a cell's failure the run undoes it and keeps going with the
    next one instead of failing every cell after it.
    """

    commit: Callable[[], Awaitable[None]]
    rollback: Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class PredictionOutcome:
    """What a predictor answers for one cell and month: the probability of the
    event, and the features that contributed most to it with their value
    (docs/08-ml.md §M2 "Factores": the three features with the largest
    contribution).

    `probability` is the model's own output in `[0, 1]`; the severity is the
    reading of it against the version's thresholds (`severity_for`), never
    something the predictor chooses (docs/08 §M2 "Severidad", D-T0.4).
    """

    probability: float
    top_factors: list[dict[str, Any]]


class Predictor(Protocol):
    """One registered version's own inference (docs/06-diseno-detallado.md §8
    "predict_proba → calibrador → probabilidad"; ADR-0020 paso 10).

    `version` is an argument rather than a constructor detail because the served
    version is what decides the artifact to load, its calibration and its
    thresholds: one predictor answers for one version, and the registry is what
    pairs them (T9 registers the real ones).

    Synchronous on purpose: inference is CPU work on an artifact already in
    memory, and the feature vector it reads is already computed.
    """

    def predict(
        self, version: ModelVersion, features: Mapping[str, float | None]
    ) -> PredictionOutcome: ...


class PredictorRegistry:
    """Which predictor answers for a served version, keyed by `(name, version)`
    (docs/06-diseno-detallado.md §8).

    Both parts of the key are the version's own: the artifact, the calibration
    and the thresholds of `risk_flood@2026-10-02` belong together, and a
    predictor of another version would answer with a probability whose operating
    points were never validated. A version nothing is registered for resolves to
    `None`, which the job reports as "no prediction for that cell this run"
    instead of inventing a probability (docs/06 §8 "Sin modelo promovido").
    """

    def __init__(self, predictors: Mapping[tuple[str, str], Predictor] | None = None) -> None:
        self._predictors: dict[tuple[str, str], Predictor] = dict(predictors or {})

    def register(self, name: str, version: str, predictor: Predictor) -> None:
        """Registers the predictor of one `model_version` (T9, step 10)."""
        self._predictors[(name, version)] = predictor

    def resolve(self, version: ModelVersion) -> Predictor | None:
        """The predictor of `version`, or `None` when none is registered."""
        return self._predictors.get((version.name, version.version))
