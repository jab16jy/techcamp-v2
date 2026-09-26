"""Forecast refresh jobs (docs/06-diseno-detallado.md §6, docs/10-dag.md §3,
ADR-0012): every 3 h the forecast of the active cells is refreshed, and a
provider outage leaves the last stored data in place, served as `stale` with
its own `fetched_at`.

The 3 h run only *fans out*: one job per active cell, so the worker refreshes
the cells in parallel (docs/09-cuellos-de-botella.md:29) and one slow provider
call does not hold the others up. Each cell's own job then does the provider
call and the write.

`enqueue_forecast_refresh` is a separate, SQLAlchemy-session-based helper, not
a call through this module's `app`, for the reason `shared/jobs.py` spells out
(asyncpg vs. psycopg cannot share a transaction). The cold start that
`farms`' plot write path triggers goes through it, so it rides that plot's own
transaction (ADR-0012); the fan-out above uses the same call, one transaction
for all the cells of a run.
"""

from __future__ import annotations

import datetime
import json
import logging
from functools import lru_cache

from procrastinate import RetryStrategy
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.shared.db import async_session_factory
from techcamp.shared.jobs import app
from techcamp.weather.adapters.open_meteo import (
    OpenMeteoAdapter,
    get_weather_forecast_adapter,
)
from techcamp.weather.adapters.orm import WeatherCellRow
from techcamp.weather.adapters.repositories import SqlAlchemyWeatherRepository
from techcamp.weather.application.ports import WeatherUnavailableError
from techcamp.weather.domain.models import WeatherDay

logger = logging.getLogger(__name__)

QUEUE_NAME = "weather"
REFRESH_TASK_NAME = "weather.refresh_cell"
ACTIVE_CELLS_TASK_NAME = "weather.refresh_active_cells"

FORECAST_DAYS = 16
"""The whole window a refresh rewrites: Open-Meteo's maximum forecast, and the
largest `days` `GET /plots/{id}/weather` accepts (docs/01-requisitos.md:34), so
one fetch is enough for every `days` any consumer can ask for."""


async def enqueue_forecast_refresh(session: AsyncSession, cell_id: int) -> None:
    """Defers `weather.refresh_cell` for one cell over `session`, inside whatever
    transaction the caller already has open.

    `lock` and `queueing_lock` are the same per-cell string, which is what keeps
    a cell from refreshing twice at once: `procrastinate_fetch_job_v2` refuses a
    job whose `lock` a `doing` job already holds, and `procrastinate_jobs_
    queueing_lock_idx_v1` (unique, partial on `status = 'todo'`) refuses a
    second enqueue while one is still waiting — so a cold start, a plot write
    and the 3 h fan-out racing on the same cell collapse into one job. Cells
    stay independent, so they still run in parallel
    (docs/09-cuellos-de-botella.md:29).

    That index is also why this runs inside a savepoint. procrastinate's own
    `Task.defer` catches the duplicate and raises `AlreadyEnqueued`, which a
    caller reads as "already queued"; the SQL function `telemetry`'s
    `enqueue_recalibration` also calls lets the unique violation out instead,
    and inside a caller's transaction that aborts the whole thing — a 3 h run
    would lose every other cell's job, and a plot write would fail because a
    cell it had nothing to do with was already queued. Rolling back to the
    savepoint leaves the caller's transaction usable, and a cell that already
    has a job waiting needs nothing more.
    """
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    "SELECT procrastinate_defer_jobs_v1("
                    "ARRAY[ROW(:queue_name, :task_name, :priority, :lock, :queueing_lock, :args, "
                    "NULL::timestamptz)]::procrastinate_job_to_defer_v1[])"
                ),
                {
                    "queue_name": QUEUE_NAME,
                    "task_name": REFRESH_TASK_NAME,
                    "priority": 0,
                    "lock": f"refresh:cell:{cell_id}",
                    "queueing_lock": f"refresh:cell:{cell_id}",
                    "args": json.dumps({"cell_id": cell_id}),
                },
            )
    except IntegrityError:
        # Logged, not swallowed silently: the statement is one insert into
        # `procrastinate_jobs` with every value bound above, so the duplicate
        # queueing lock is the integrity error to expect, and anything else
        # still has to show up in the worker's log rather than pass as a no-op.
        logger.warning("weather: refresh for cell %s already queued", cell_id, exc_info=True)


@lru_cache(maxsize=1)
def _adapter() -> OpenMeteoAdapter:
    """The one adapter the whole worker process fetches through.

    One instance, not one per task: the circuit breaker's counters live in the
    instance (T3b), so a fresh adapter per task would spend the full retry budget
    on every cell while Open-Meteo is down and the breaker would never trip.
    """
    return get_weather_forecast_adapter()


async def _cell_coordinates(session: AsyncSession, cell_id: int) -> tuple[float, float] | None:
    result = await session.execute(
        select(WeatherCellRow.lat, WeatherCellRow.lon).where(WeatherCellRow.id == cell_id)
    )
    row = result.one_or_none()
    return None if row is None else (float(row.lat), float(row.lon))


# `queue` has to be repeated on `periodic`: it configures the job from scratch
# (procrastinate's `configure_task` reads the options it is handed, never the
# task's own default), and a job left in the default queue would sit where this
# worker does not listen.
@app.periodic(cron="0 */3 * * *", queue=QUEUE_NAME)
@app.task(
    name=ACTIVE_CELLS_TASK_NAME,
    queue=QUEUE_NAME,
    retry=RetryStrategy(max_attempts=2, linear_wait=30),
)
async def refresh_active_cells(timestamp: int) -> None:
    """The 3 h run: one job per active cell (docs/06-diseno-detallado.md §6,
    docs/10-dag.md §3 "cada 3 h: pronóstico de celdas activas").

    The cron is read in the worker's own local time (procrastinate evaluates it
    with `croniter` on a naive local clock), so the `worker` service runs in
    `America/Bogota` — the zone docs/10-dag.md:3 § fixes every job hour to.

    `timestamp` is procrastinate's own periodic argument, the unix time the
    slot was scheduled for. The run needs no clock of its own: the forecast days
    are the provider's, not ours.

    The retry covers this task alone, a database hiccup between reading the
    cells and enqueueing them, which would otherwise leave every cell
    unrefreshed for three hours. Re-running is free: a cell that already has a
    job waiting is skipped by its queueing lock.
    """
    async with async_session_factory() as session:
        for cell_id in await SqlAlchemyWeatherRepository(session).active_cell_ids():
            await enqueue_forecast_refresh(session, cell_id)
        await session.commit()


@app.task(name=REFRESH_TASK_NAME, queue=QUEUE_NAME)
async def refresh_cell(cell_id: int) -> None:
    """Refreshes one cell's 16-day forecast window (docs/06-diseno-detallado.md
    §6). No `RetryStrategy`: the only thing that can fail here after the
    adapter's own retries is the write, and re-running it would just repeat a
    provider call against an outage the breaker has already noticed. A failed
    cell waits for the next 3 h run with its stored data intact.

    On a provider outage it returns instead of raising, leaving the stored rows
    and their `fetched_at` untouched: that is the degradation rule (docs/06 §6,
    docs/09-cuellos-de-botella.md:15) — the last data keeps being served, marked
    `stale` once it is older than `STALE_AFTER` (T1b).
    """
    async with async_session_factory() as session:
        coordinates = await _cell_coordinates(session, cell_id)
        if coordinates is None:
            return  # cell gone by the time the worker ran; nothing to refresh
        try:
            rows = await _adapter().fetch_daily(
                *coordinates, past_days=0, forecast_days=FORECAST_DAYS
            )
        except WeatherUnavailableError:
            logger.warning(
                "weather: Open-Meteo unavailable for cell %s, keeping its stored rows",
                cell_id,
                exc_info=True,
            )
            return
        # One `fetched_at` for the whole window, the clock the `stale` rule
        # reads per cell rather than per day.
        fetched_at = datetime.datetime.now(datetime.UTC)
        await SqlAlchemyWeatherRepository(session).upsert_daily(
            cell_id,
            [
                WeatherDay(
                    day=row.day,
                    is_forecast=True,
                    et0_mm=row.et0_mm,
                    rain_mm=row.rain_mm,
                    tmin_c=row.tmin_c,
                    tmax_c=row.tmax_c,
                    rh_mean_pct=row.rh_mean_pct,
                    fetched_at=fetched_at,
                )
                for row in rows
            ],
        )
