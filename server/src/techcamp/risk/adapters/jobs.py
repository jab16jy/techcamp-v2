"""The daily risk job (docs/06-diseno-detallado.md §8, docs/10-dag.md §3 "cada día
06:00", ADR-0012; D-T6b.2).

One task at 06:00 America/Bogota, not a fan-out of one job per cell like
`weather` and `irrigation`: the risk of a cell needs two provider calls and the
answer is a single row per event, so the run walks the cells itself and an archive
outage skips its cell while the cells after it are still predicted (docs/06 §8,
docs/09-cuellos-de-botella.md: degradación, not one cell's failure cancelling the
run). `POST /dev/jobs/risk:run` answers one `job_id` for the same reason
(docs/04-api.md §Solo perfil seminario (`/dev`)).

The cells come from weather's public application port, not from a join of
`weather_cell` and `plot` and not from `weather`'s adapters: docs/05-arquitectura.md
§Lecturas cruzadas says a module reads another module's data through that module's
public application facade, and §Solo la fachada pública says it never imports
another module's `domain` or `adapters`. So this module names
`weather.application.ports.WeatherRepository` and the concrete repository is built
in the composition root, `techcamp/worker.py`, which hands it in through
`configure_weather_cells` — the same shape as `techcamp.ingestor` composing the
`alerts` evaluator into `telemetry` (`alerts/adapters/evaluate_readings.py`).

procrastinate calls a task with the JSON arguments of its row and offers no
startup injection point in 3.10 (`App.task` takes no dependency callback), which is
why the factory is a module seam configured once by the composition root instead of
a parameter. Unconfigured is an error, never an empty run: a job that quietly
predicted nothing would look like a month with no risk.

`_predictors()` is the registry T9 registers the served version with: the
`risk_flood` baseline the gate left serving, loaded from object storage and verified
against the `artifact_sha256` its `model_version` row registered (ADR-0020 paso 10).
An event with no registered version, or a version whose artifact does not verify, is a
skip the job logs and continues past, never a probability nobody produced
(docs/06-diseno-detallado.md §8 "Sin modelo promovido").
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Callable, Sequence
from functools import lru_cache

from procrastinate import RetryStrategy
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.application import PredictionEvidence, RiskRuleEvaluation
from techcamp.risk.adapters.open_meteo_archive import (
    OpenMeteoArchiveAdapter,
    get_risk_archive_adapter,
    seminar_archive_adapter,
)
from techcamp.risk.adapters.registry import build_registry, s3_artifact_store
from techcamp.risk.adapters.repositories import SqlAlchemyRiskRepository
from techcamp.risk.application.ports import CellTransactions, PredictorRegistry, RiskCell
from techcamp.risk.application.run_daily_risk import run_daily_risk
from techcamp.risk.domain.models import RiskPrediction
from techcamp.shared.config import is_seminar_profile
from techcamp.shared.dates import local_today
from techcamp.shared.db import async_session_factory
from techcamp.shared.jobs import app
from techcamp.weather.application.ports import WeatherRepository

logger = logging.getLogger(__name__)

QUEUE_NAME = "risk"
RUN_ACTIVE_CELLS_TASK_NAME = "risk.predict_active_cells"

type WeatherCellsFactory = Callable[[AsyncSession], WeatherRepository]
"""How the composition root builds weather's cell reader on the session this task
opened (docs/05 §Solo la fachada pública): `risk` may name
`weather.application`'s port and never `weather.adapters`, so `techcamp/worker.py`
builds the concrete repository and injects it here."""

type AlertEvaluationFactory = Callable[[AsyncSession], RiskRuleEvaluation]
"""How the composition root builds the model-rule evaluation on the session this
task opened, for the same reason: `risk` may name `alerts.application`'s protocol
and never `alerts.adapters`, so `techcamp/worker.py` builds the concrete
evaluator and injects it here (docs/06-diseno-detallado.md §8 "Alertas")."""

_weather_cells: WeatherCellsFactory | None = None
_alert_evaluation: AlertEvaluationFactory | None = None


def configure_weather_cells(factory: WeatherCellsFactory) -> None:
    """Inject weather's cell reader. Called once by `techcamp/worker.py` before the
    worker starts (docs/05 §Solo la fachada pública)."""
    global _weather_cells
    _weather_cells = factory


def configure_alert_evaluation(factory: AlertEvaluationFactory) -> None:
    """Inject the evaluation of `flood_risk` / `drought_risk`. Called once by
    `techcamp/worker.py` before the worker starts, for the same reason as the cell
    reader (docs/05 §Solo la fachada pública).

    Not optional: the run writes predictions and nothing else would turn them into
    alerts, so a worker that never called this would store a month of risk and
    raise no alert at all (docs/06 §8 "Alertas")."""
    global _alert_evaluation
    _alert_evaluation = factory


def _cells_source(session: AsyncSession) -> WeatherRepository:
    if _weather_cells is None:
        raise RuntimeError(
            "risk: no weather cell reader configured. techcamp/worker.py must call "
            "configure_weather_cells() before the worker starts (docs/05-arquitectura.md "
            "§Solo la fachada pública)."
        )
    return _weather_cells(session)


def _alerts_source(session: AsyncSession) -> RiskRuleEvaluation:
    if _alert_evaluation is None:
        raise RuntimeError(
            "risk: no alert evaluation configured. techcamp/worker.py must call "
            "configure_alert_evaluation() before the worker starts (docs/05-arquitectura.md "
            "§Solo la fachada pública)."
        )
    return _alert_evaluation(session)


def _as_evidence(predictions: Sequence[RiskPrediction]) -> list[PredictionEvidence]:
    """The run's own rows as the alert rules read them.

    The values cross into `alerts` through its application facade, which is the
    only package of that module `risk` may import (docs/05 §Solo la fachada
    pública). The stored `severity` code is turned into `alerts`' own vocabulary
    there, so this module never names it.
    """
    return [
        PredictionEvidence.from_stored(
            cell_id=prediction.cell_id,
            event=prediction.event_type.value,
            severity=prediction.severity.value,
            horizon_start=prediction.horizon_start,
            model_version_id=prediction.model_version_id,
            issued_at=prediction.created_at,
        )
        for prediction in predictions
    ]


@lru_cache(maxsize=1)
def _archive() -> OpenMeteoArchiveAdapter:
    """The one adapter the whole worker process fetches through.

    ADR-0021: the seminar profile replays the recorded responses T6a captured
    (no internet in the seminar room); every other profile calls Open-Meteo. One
    instance per process because the circuit breaker's counters live in the
    instance, the same reason `weather`'s adapter is one per process.
    """
    return seminar_archive_adapter() if is_seminar_profile() else get_risk_archive_adapter()


@lru_cache(maxsize=1)
def _predictors() -> PredictorRegistry:
    """The predictors this worker serves, keyed by `(name, version)`
    (docs/06-diseno-detallado.md §8).

    The registry carries a FACTORY per event, not one predictor per version, and that is
    the shape T9 needed: `build_registry` reads no database and touches no object storage,
    so building it at import time would be harmless — but the artifact it will load is
    named by the `model_version` row that `run_daily_risk` resolves through
    `served_version(name)`, and that row is read per run, inside the transaction this
    task closes before the first provider call. The factory therefore receives the row,
    verifies its `artifact_sha256` against the bytes in the bucket and caches what it
    built, so each artifact is fetched once per process no matter how many cells the run
    walks (docs/03-modelo-datos.md §Integridad del artefacto, ADR-0012 for the retries).

    Nothing here contacts MinIO: the first fetch happens on the first `resolve`, inside
    the run. That is also what keeps this import-safe — the tests monkeypatch
    `_predictors` with their own registry, and registering an artifact at import time
    would reach the bucket before any of them ran (#240 R3-global-seam-leak).
    """
    return build_registry(s3_artifact_store())


async def risk_cells(cells: WeatherRepository) -> list[RiskCell]:
    """The cells to predict: every cell at least one plot points at, with the centre
    the archive is asked about (docs/06-diseno-detallado.md §6, §8).

    Typed against weather's application port, so the query is weather's
    (`WeatherRepository.active_cells`) and this module never joins another module's
    tables (docs/05-arquitectura.md §Lecturas cruzadas)."""
    return [RiskCell(id=cell.id, lat=cell.lat, lon=cell.lon) for cell in await cells.active_cells()]


# `queue` has to be repeated on `periodic`: it configures the job from scratch
# (procrastinate's `configure_task` reads the options it is handed, never the
# task's own default), and a job left in the default queue would sit where this
# worker does not listen.
@app.periodic(cron="0 6 * * *", queue=QUEUE_NAME)
@app.task(
    name=RUN_ACTIVE_CELLS_TASK_NAME,
    queue=QUEUE_NAME,
    # A rerun costs one provider call per cell and is free on the rows: a month
    # already predicted is not written again (docs/06 §8). The retry therefore only
    # covers a database hiccup mid-run, which would otherwise leave the remaining
    # cells unpredicted for a whole day.
    retry=RetryStrategy(max_attempts=2, linear_wait=30),
)
async def predict_active_cells(timestamp: int, day: str | None = None) -> None:
    """The daily run: predict every active cell for the month of `day`.

    `day` is a plain ISO string because it travels as a job argument and
    `procrastinate_jobs.args` is JSON, which has no date type (the same reason
    `weather`'s consolidation takes one). The cron hands over only `timestamp`, so
    it stays optional and the run resolves the Bogota day itself; `POST
    /dev/jobs/risk:run` is what passes one (docs/04-api.md §Solo perfil seminario).

    The cron is read in the worker's own local time (procrastinate evaluates it
    with `croniter` on a naive local clock), so the `worker` service runs in
    `America/Bogota`, the zone docs/10-dag.md §3 fixes every job hour to.

    The run is followed by the model rules, in the same task and the same session:
    docs/06-diseno-detallado.md §8 "Alertas" puts the evaluation after the
    predictions are written, and the row it needs is the one this run just wrote
    (`DailyRiskRun.predictions`), never a read back of the month.
    """
    target = datetime.date.fromisoformat(day) if day else local_today()
    async with async_session_factory() as session:
        cells = await risk_cells(_cells_source(session))
        # Both composition seams are resolved before the first write, so a worker
        # that was never configured fails before it stores a month of risk it
        # could never alert on.
        alerts = _alerts_source(session)
        # Close the read transaction before the first provider call: a transaction
        # must not stay open across the archive calls of the cells, which are two
        # HTTP requests each with a 10 s timeout and up to three retries. The run
        # then commits per cell, so a failure late in it loses that cell only
        # instead of every prediction already written (#240
        # R3-long-transaction-across-http).
        await session.commit()
        run = await run_daily_risk(
            day=target,
            cells=cells,
            versions=SqlAlchemyRiskRepository(session),
            archive=_archive(),
            predictors=_predictors(),
            transactions=CellTransactions(commit=session.commit, rollback=session.rollback),
        )
        await alerts(
            at=datetime.datetime.now(datetime.UTC), predictions=_as_evidence(run.predictions)
        )
    logger.info(
        "risk: %s predictions for %s over %s cells (%s cell/event pairs skipped)",
        run.written,
        run.issue_month,
        len(cells),
        run.skipped,
    )
