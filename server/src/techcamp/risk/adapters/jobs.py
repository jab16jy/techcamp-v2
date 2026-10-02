"""The daily risk job (docs/06-diseno-detallado.md §8, docs/10-dag.md §3 "cada día
06:00", ADR-0012; D-T6b.2).

One task at 06:00 America/Bogota, not a fan-out of one job per cell like
`weather` and `irrigation`: the risk of a cell needs two provider calls and the
answer is a single row per event, so the run walks the cells itself and an archive
outage skips its cell while the cells after it are still predicted (docs/06 §8,
docs/09-cuellos-de-botella.md: degradación, not one cell's failure cancelling the
run). `POST /dev/jobs/risk:run` answers one `job_id` for the same reason
(docs/04-api.md §Solo perfil seminario (`/dev`)).

The cells come from weather's facade, not from a join of `weather_cell` and
`plot`: docs/05-arquitectura.md §Lecturas cruzadas says a module reads another
module's data through that module's public application facade.

`_predictors()` is empty until T9 registers the promoted model's and the
baselines' (ADR-0020 paso 10). That is not a stub: with no predictor registered a
version gets no prediction, the job logs it and finishes
(docs/06-diseno-detallado.md §8 "Sin modelo promovido"), and nothing in this
module ever answers with a probability of its own.
"""

from __future__ import annotations

import datetime
import logging
from functools import lru_cache

from procrastinate import RetryStrategy

from techcamp.risk.adapters.open_meteo_archive import (
    OpenMeteoArchiveAdapter,
    get_risk_archive_adapter,
    seminar_archive_adapter,
)
from techcamp.risk.adapters.repositories import SqlAlchemyRiskRepository
from techcamp.risk.application.ports import PredictorRegistry, RiskCell
from techcamp.risk.application.run_daily_risk import run_daily_risk
from techcamp.shared.config import is_seminar_profile
from techcamp.shared.dates import local_today
from techcamp.shared.db import async_session_factory
from techcamp.shared.jobs import app
from techcamp.weather.adapters.repositories import SqlAlchemyWeatherRepository

logger = logging.getLogger(__name__)

QUEUE_NAME = "risk"
RUN_ACTIVE_CELLS_TASK_NAME = "risk.predict_active_cells"


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

    Empty until T9 registers them with the artifacts it promotes (ADR-0020
    paso 10). An empty registry is the honest state of the epic: the job then
    predicts nothing and says why in its log, rather than writing a probability
    no model produced.
    """
    return PredictorRegistry()


async def risk_cells(weather: SqlAlchemyWeatherRepository) -> list[RiskCell]:
    """The cells to predict: every cell at least one plot points at, with the
    centre the archive is asked about (docs/06-diseno-detallado.md §6, §8)."""
    return [
        RiskCell(id=cell.id, lat=cell.lat, lon=cell.lon) for cell in await weather.active_cells()
    ]


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
    """
    target = datetime.date.fromisoformat(day) if day else local_today()
    async with async_session_factory() as session:
        cells = await risk_cells(SqlAlchemyWeatherRepository(session))
        run = await run_daily_risk(
            day=target,
            cells=cells,
            versions=SqlAlchemyRiskRepository(session),
            archive=_archive(),
            predictors=_predictors(),
        )
        await session.commit()
    logger.info(
        "risk: %s predictions for %s over %s cells (%s cell/event pairs skipped)",
        run.written,
        run.issue_month,
        len(cells),
        run.skipped,
    )
