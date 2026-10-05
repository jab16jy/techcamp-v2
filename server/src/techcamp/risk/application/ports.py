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

    async def insert_version(self, version: ModelVersion) -> ModelVersion:
        """Store one registered model or baseline (ADR-0020 paso 10, docs/08-ml.md §Reglas
        de gobierno "Trazabilidad").

        The row registration (T9) writes after the artifact is in object storage: the
        version a prediction names has to exist before anything points at it. The stored
        row is returned, read back through the same mapper `served_version` uses, so the
        caller reports what is really in the table rather than what it meant to write.
        """
        ...

    async def versions_for(self, name: str) -> Sequence[ModelVersion]:
        """Every registered version of one event, most recent first.

        Registration asks this before writing, so a version string that is already
        registered is never taken twice by a second run (ADR-0020 paso 10). Ordered by
        `created_at` then `id` for the same reason `served_version` orders: `uuid7` sorts
        by creation time, so the order is deterministic without inventing a column the
        docs do not have.
        """
        ...

    async def insert_prediction(self, prediction: RiskPrediction) -> bool: ...

    async def stored_predictions(
        self,
        *,
        horizon_start: date,
        cell_ids: Sequence[int],
        model_version_ids: Sequence[uuid.UUID],
    ) -> list[RiskPrediction]:
        """The predictions stored for one month, over these cells, with any of
        these served versions.

        Read back instead of remembered, because what the alert rules need is the
        MONTH and not one run's own insert: la predicción de una celda, evento y
        mes se escribe una vez (docs/06-diseno-detallado.md §8), so every run
        after the first writes nothing, and the caller that evaluated only the
        rows its own attempt inserted would leave that month without an alert
        whenever the attempt that failed was the alert step (the job retries,
        ADR-0012).

        `model_version_ids` is the served version of each event, so a month that a
        promotion later predicted again brings back the row of the version being
        served, which is the one `GET /plots/{plot_id}/risk` shows too.
        """
        ...

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
    next one instead of failing every cell after it. What that rollback does NOT
    undo is what was already committed: `SqlAlchemyRiskRepository.insert_prediction`
    commits every row of its own, so the rows a cell stored before it failed are
    stored, are counted as written, and are what the alerts are decided on (#242
    `R3-rollback-leaves-written-count-inflated`).
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


type PredictorFactory = Callable[[ModelVersion], Predictor | None]
"""How one event's predictor is built from the `model_version` row that serves it
(ADR-0020 paso 10: el artefacto, su hash y sus umbrales viajan juntos).

An argument rather than a constructor detail because the row is what names the artifact
and carries its `artifact_sha256` (docs/03-modelo-datos.md §Integridad del artefacto): the
factory cannot know which bytes to load before the row that names them has been read.

It may answer `None`, and that is the honest answer rather than a failure: a row whose
artifact does not verify against its registered `artifact_sha256`, or is not a body this
model can read, is a version this run cannot serve (docs/06-diseno-detallado.md §8 "Sin
modelo promovido"). `PredictorRegistry` caches a predictor it gets and caches nothing for
a `None`, so a version fixed by a re-registration is picked up instead of staying
unanswerable for the life of the process.

`type` and not a plain assignment because it names `Predictor`, which is declared below:
an assignment evaluates that name at import time and raises before the class exists.
"""


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

    A registered **factory** is the second way in, and the one serving uses (T9). The job
    resolves one served row per event and never reads the whole registry out of the
    database itself, so what a factory is given is the row that already names the artifact
    and its `artifact_sha256`: the artifact is loaded and verified the first time that row
    is resolved and cached afterwards, so one process fetches it once no matter how many
    cells the run walks (docs/03 §Integridad del artefacto, ADR-0021: the seminar profile
    has no internet and the bucket has to be local).
    """

    def __init__(self, predictors: Mapping[tuple[str, str], Predictor] | None = None) -> None:
        self._predictors: dict[tuple[str, str], Predictor] = dict(predictors or {})
        self._factories: dict[str, PredictorFactory] = {}

    def register(self, name: str, version: str, predictor: Predictor) -> None:
        """Registers the predictor of one `model_version` (T9, step 10)."""
        self._predictors[(name, version)] = predictor

    def register_factory(self, name: str, factory: PredictorFactory) -> None:
        """Registers how any `model_version` of `name` is answered, from its own row.

        A factory is asked only for a version nothing is registered for, and only once:
        the predictor it builds is cached under that version's key like a registered one.
        A factory that cannot answer returns `None` and nothing is cached, so a version
        whose artifact is missing now and present after a re-registration is picked up
        instead of being answered `None` for the life of the process.
        """
        self._factories[name] = factory

    def resolve(self, version: ModelVersion) -> Predictor | None:
        """The predictor of `version`, or `None` when none is registered."""
        found = self._predictors.get((version.name, version.version))
        if found is not None:
            return found
        factory = self._factories.get(version.name)
        if factory is None:
            return None
        built = factory(version)
        if built is None:
            return None
        self._predictors[(version.name, version.version)] = built
        return built
