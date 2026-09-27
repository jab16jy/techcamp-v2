"""Procrastinate jobs for the irrigation module (E6 T4, docs/06 §5, ADR-0012).

Orchestrates the daily water balance and recommendation runs:
- 04:30 America/Bogota periodic cron fans out one per-plot job for eligible plots
  (plots with an active cycle plus rainfed plots without one).
- Per-plot task runs `run_daily_balance(plot_id, day, ...)` with all repositories,
  commits the transaction, and logs any skip reason.
- Lock and queueing_lock prevent duplicate execution and duplicate enqueuing for the
  same plot on the same day.
"""

from __future__ import annotations

import datetime
import json
import logging
from uuid import UUID

from asyncpg.exceptions import UniqueViolationError
from procrastinate import RetryStrategy
from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import CropCycleRow, PlotRow
from techcamp.farms.adapters.repositories import (
    SqlAlchemyCropCycleRepository,
    SqlAlchemyCropRepository,
    SqlAlchemyPlotRepository,
    SqlAlchemySoilProfileRepository,
)
from techcamp.farms.domain.models import CropCycleStatus, IrrigationSystem
from techcamp.irrigation.adapters.repositories import (
    SqlAlchemyIrrigationRecommendationRepository,
    SqlAlchemyWaterBalanceRepository,
)
from techcamp.irrigation.application.run_daily_balance import run_daily_balance
from techcamp.irrigation.domain.models import local_today as local_day_in_bogota
from techcamp.shared.db import async_session_factory
from techcamp.shared.jobs import app
from techcamp.telemetry.adapters.repositories import (
    SqlAlchemyCalibrationRepository,
    SqlAlchemyNodeRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemySensorRepository,
)
from techcamp.weather.adapters.repositories import SqlAlchemyWeatherRepository

logger = logging.getLogger(__name__)

QUEUE_NAME = "irrigation"
RUN_DAILY_PLOTS_TASK_NAME = "irrigation.run_daily_plots"
RUN_PLOT_BALANCE_TASK_NAME = "irrigation.run_plot_balance"
QUEUEING_LOCK_INDEX = "procrastinate_jobs_queueing_lock_idx_v1"


def _is_queueing_lock_violation(exc: IntegrityError) -> bool:
    """Check if the IntegrityError is caused by duplicate queueing_lock.

    Under asyncpg, the driver error is chained as `exc.orig.__cause__`, an
    `asyncpg.exceptions.UniqueViolationError` carrying `constraint_name`.
    """
    cause = exc.orig.__cause__ if exc.orig is not None else None
    return isinstance(cause, UniqueViolationError) and cause.constraint_name == QUEUEING_LOCK_INDEX


def local_today() -> datetime.date:
    """Today in the DAG's zone, which is America/Bogota (docs/10-dag.md:156;
    docs/06 §5).

    The zone and the conversion belong to the domain, so the job entry points
    read the clock and hand the instant over rather than owning a second copy
    of the same rule."""
    return local_day_in_bogota(datetime.datetime.now(datetime.UTC))


async def eligible_plot_ids(session: AsyncSession) -> list[UUID]:
    """Retrieve plot IDs eligible for daily water balance and recommendation.

    Eligible plots are:
    - Plots with an active crop cycle (irrigated or rainfed).
    - Rainfed plots without an active cycle (they receive delay_sowing advice).
    Irrigated plots without an active cycle are excluded.
    """
    stmt = (
        select(PlotRow.id)
        .where(
            or_(
                PlotRow.irrigation_system == IrrigationSystem.NONE.value,
                PlotRow.id.in_(
                    select(CropCycleRow.plot_id).where(
                        CropCycleRow.status == CropCycleStatus.ACTIVE.value
                    )
                ),
            )
        )
        .order_by(PlotRow.id)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _defer_plot_job(
    session: AsyncSession,
    *,
    task_name: str,
    plot_id: UUID,
    args: dict[str, object],
    queueing_suffix: str = "",
) -> None:
    """Defers one per-plot job over `session`, inside whatever transaction the caller has open.

    `lock` is per-plot (`irrigation:plot:<id>`), ensuring that two runs for the same plot do
    not execute concurrently.
    `queueing_lock` has a per-day suffix (`irrigation:plot:<id>:<day>`), so a second enqueue
    for the same day while one is already pending is deduplicated by Postgres's partial unique
    index (`procrastinate_jobs_queueing_lock_idx_v1`).
    Runs inside a savepoint (`begin_nested`) so that an IntegrityError on duplicate enqueue is
    caught and logged without aborting the caller's transaction; any unrelated IntegrityError
    is re-raised.
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
                    "task_name": task_name,
                    "priority": 0,
                    "lock": f"irrigation:plot:{plot_id}",
                    "queueing_lock": f"irrigation:plot:{plot_id}{queueing_suffix}",
                    "args": json.dumps(args),
                },
            )
    except IntegrityError as exc:
        if not _is_queueing_lock_violation(exc):
            raise
        logger.warning(
            "irrigation: job for plot %s with suffix %s already queued",
            plot_id,
            queueing_suffix,
            exc_info=True,
        )


async def enqueue_plot_balance(session: AsyncSession, plot_id: UUID, day: datetime.date) -> None:
    """Defers `run_plot_balance` for one plot and target day."""
    await _defer_plot_job(
        session,
        task_name=RUN_PLOT_BALANCE_TASK_NAME,
        plot_id=plot_id,
        args={"plot_id": str(plot_id), "day": day.isoformat()},
        queueing_suffix=f":{day.isoformat()}",
    )


@app.periodic(cron="30 4 * * *", queue=QUEUE_NAME)
@app.task(
    name=RUN_DAILY_PLOTS_TASK_NAME,
    queue=QUEUE_NAME,
    retry=RetryStrategy(max_attempts=2, linear_wait=30),
)
async def run_daily_plots(timestamp: int, day: str | None = None) -> None:
    """The daily run at 04:30 America/Bogota (docs/06 §5, docs/10-dag.md §3).

    Fans out one balance job per eligible plot for target day D (defaults to local today).
    """
    target = datetime.date.fromisoformat(day) if day else local_today()
    async with async_session_factory() as session:
        for plot_id in await eligible_plot_ids(session):
            await enqueue_plot_balance(session, plot_id, target)
        await session.commit()


@app.task(name=RUN_PLOT_BALANCE_TASK_NAME, queue=QUEUE_NAME)
async def run_plot_balance(plot_id: str, day: str) -> None:
    """Runs daily water balance and recommendation for one plot on local day D.

    Opens a session, constructs all domain repositories, executes `run_daily_balance`,
    and commits. If the plot calculation is skipped, logs the skip reason structured
    and stores nothing. Any exception bubbles up to procrastinate for retry/failure tracking.
    """
    target_plot_id = UUID(plot_id)
    target_day = datetime.date.fromisoformat(day)
    async with async_session_factory() as session:
        result = await run_daily_balance(
            plot_id=target_plot_id,
            day=target_day,
            plots=SqlAlchemyPlotRepository(session),
            crop_cycles=SqlAlchemyCropCycleRepository(session),
            crops=SqlAlchemyCropRepository(session),
            soil_profiles=SqlAlchemySoilProfileRepository(session),
            weather=SqlAlchemyWeatherRepository(session),
            water_balances=SqlAlchemyWaterBalanceRepository(session),
            recommendations=SqlAlchemyIrrigationRecommendationRepository(session),
            nodes=SqlAlchemyNodeRepository(session),
            sensors=SqlAlchemySensorRepository(session),
            calibrations=SqlAlchemyCalibrationRepository(session),
            readings=SqlAlchemyReadingRepository(session),
        )
        if result.skipped:
            logger.info(
                "irrigation: plot %s skipped for day %s: %s",
                target_plot_id,
                target_day,
                result.skip_reason,
                extra={
                    "plot_id": str(target_plot_id),
                    "day": target_day.isoformat(),
                    "skip_reason": result.skip_reason,
                },
            )
            return
        await session.commit()
