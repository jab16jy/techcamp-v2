"""Procrastinate jobs for the metrics module (E11 T6, docs/10-dag.md:178-180,
docs/04-api.md:261; ADR-0012, D-T0.7, D-T0.8).

One run on day one at 02:00 America/Bogota, doing the two things docs/10's monthly
subgraph draws, in that order: the summary of the closed cycles, then the digital
adoption index of the month **before** the run day, one row per plot.

Both halves are a fan-out, the shape the irrigation and alerts jobs already use:
the run queues one job per item and returns. A plot whose month cannot be computed
is then procrastinate's business alone — it retries on its own and never takes the
other plots' month down with it (E11 lessons: per-item error containment in a batch
job). Every defer sits in a savepoint, so a duplicate `queueing_lock` costs the
duplicate and leaves the sweep's transaction usable.

`crop_cycle` carries no close date (docs/03-modelo-datos.md:128-135), so "the
cycles closed this month" has no column to filter on and the run walks every
`harvested`/`lost` cycle (D-T0.8): the write is an upsert keyed by
`crop_cycle_id`, so re-running lands the same figures (docs/03:442).
"""

from __future__ import annotations

import datetime
import json
import logging
from datetime import timedelta
from uuid import UUID

from asyncpg.exceptions import UniqueViolationError
from procrastinate import RetryStrategy
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import CropCycleRow, PlotRow
from techcamp.farms.adapters.repositories import (
    SqlAlchemyCropCycleRepository,
    SqlAlchemyPlotRepository,
)
from techcamp.metrics.adapters.cycle_summary_repository import SqlAlchemyCycleSummaryRepository
from techcamp.metrics.adapters.monthly_repository import SqlAlchemyMonthlyMetricRepository
from techcamp.metrics.adapters.repositories import SqlAlchemyBaselineRepository
from techcamp.metrics.adapters.source_repository import SqlAlchemyMetricsSourceRepository
from techcamp.metrics.application.adoption import compute_plot_month
from techcamp.metrics.application.cycle_summary import PERSISTED_STATUSES, summarize_cycle
from techcamp.shared.dates import local_today
from techcamp.shared.db import async_session_factory
from techcamp.shared.jobs import app

logger = logging.getLogger(__name__)

QUEUE_NAME = "metrics"
RUN_MONTHLY_METRICS_TASK_NAME = "metrics.run_monthly_metrics"
SUMMARIZE_CLOSED_CYCLE_TASK_NAME = "metrics.summarize_closed_cycle"
COMPUTE_PLOT_MONTH_TASK_NAME = "metrics.compute_plot_month"

QUEUEING_LOCK_INDEX = "procrastinate_jobs_queueing_lock_idx_v1"

_CLOSED_STATUSES = sorted(status.value for status in PERSISTED_STATUSES)
"""Sorted so the statement's SQL text is stable; the set is D-T0.8's, read from
the use case that owns it rather than written again here."""


def previous_month(day: datetime.date) -> datetime.date:
    """The first day of the calendar month before `day`'s.

    Day one is the only day whose previous month is a whole month (D-T0.7), and
    `metrics.domain.adoption.month_bounds` derives the last day from this first
    one, so nothing here adds 30 days to anything.
    """
    first = day.replace(day=1)
    return (first - timedelta(days=1)).replace(day=1)


def _is_queueing_lock_violation(exc: IntegrityError) -> bool:
    """Whether `exc` is the duplicate `queueing_lock` and nothing else.

    Under asyncpg the driver error is chained as `exc.orig.__cause__`, an
    `asyncpg.exceptions.UniqueViolationError` carrying `constraint_name`. Any
    other `IntegrityError` is re-raised: a sweep must not swallow a broken
    statement because it happened to fail inside a defer (E6's review lesson).
    """
    cause = exc.orig.__cause__ if exc.orig is not None else None
    return isinstance(cause, UniqueViolationError) and cause.constraint_name == QUEUEING_LOCK_INDEX


async def _closed_cycles(session: AsyncSession) -> list[tuple[UUID, UUID]]:
    """`(org_id, crop_cycle_id)` of every cycle whose impact `crop_cycle_summary`
    holds, ordered so the sweep is reproducible.

    `crop_cycle` has no `org_id` (docs/03:128-135), so the organization comes from
    the join through its plot — the same path `get_for_orgs` takes. Only ids are
    read here: every other read in the fan-out belongs to a per-cycle job that
    carries its own `org_id`.
    """
    stmt = (
        select(PlotRow.org_id, CropCycleRow.id)
        .join(PlotRow, PlotRow.id == CropCycleRow.plot_id)
        .where(CropCycleRow.status.in_(_CLOSED_STATUSES))
        .order_by(PlotRow.org_id, CropCycleRow.id)
    )
    result = await session.execute(stmt)
    return [(row.org_id, row.id) for row in result]


async def _org_plots(session: AsyncSession) -> list[tuple[UUID, UUID]]:
    """`(org_id, plot_id)` of every plot, ordered.

    No eligibility filter: a plot with no node, no logbook entry and no alert still
    gets a row of null components and a null index (docs/11-metricas.md §2, D-T0.3),
    and a plot missing from the table would answer `None` forever on
    `latest_for_plot` — which is what `GET /plots/{id}/status` reads (D-T0.13).
    """
    result = await session.execute(
        select(PlotRow.org_id, PlotRow.id).order_by(PlotRow.org_id, PlotRow.id)
    )
    return [(row.org_id, row.id) for row in result]


async def _defer_item(
    session: AsyncSession,
    *,
    task_name: str,
    lock: str,
    queueing_lock: str,
    args: dict[str, str],
) -> None:
    """Defer one item job over `session`, inside whatever transaction the caller has open.

    `lock` serializes one item against itself, so two runs of the same item never
    compute it at once. `queueing_lock` collapses a *pending* duplicate
    (procrastinate's partial unique index) and is what makes a second run of the
    same month a no-op; every plot's carries the month, so a new month is never
    swallowed by the previous month's lock.

    The savepoint (`begin_nested`) is what keeps the duplicate from aborting the
    sweep's transaction, so the next item still lands
    (`irrigation/adapters/jobs.py::_defer_plot_job`, same shape).
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
                    "lock": lock,
                    "queueing_lock": queueing_lock,
                    "args": json.dumps(args),
                },
            )
    except IntegrityError as exc:
        if not _is_queueing_lock_violation(exc):
            raise
        logger.warning("metrics: job for %s already queued", queueing_lock, exc_info=True)


# `queue` has to be repeated on `periodic`: it configures the job from scratch
# (procrastinate's `configure_task` reads the options it is handed, never the
# task's own default), and a job left in the default queue would sit where this
# worker does not listen.
@app.periodic(cron="0 2 1 * *", queue=QUEUE_NAME)
@app.task(
    name=RUN_MONTHLY_METRICS_TASK_NAME,
    queue=QUEUE_NAME,
    retry=RetryStrategy(max_attempts=2, linear_wait=30),
)
async def run_monthly_metrics(timestamp: int, day: str | None = None) -> None:
    """The monthly run: day one at 02:00 (docs/10-dag.md:178-180, D-T0.7).

    Queues one summary job per closed cycle and then one index job per plot for
    the month before `day`, which defaults to local today — the month the day-one
    cron closes, and the day a seminar names to re-run an older month
    (docs/04-api.md:261).

    `timestamp` is procrastinate's periodic argument and is not read: the month
    comes from `day` so the cron, the seminar trigger and a test all ask for the
    same month by the same argument.
    """
    month = previous_month(datetime.date.fromisoformat(day) if day else local_today())
    async with async_session_factory() as session:
        for org_id, crop_cycle_id in await _closed_cycles(session):
            lock = f"metrics:cycle:{crop_cycle_id}"
            await _defer_item(
                session,
                task_name=SUMMARIZE_CLOSED_CYCLE_TASK_NAME,
                lock=lock,
                # No month in this lock: the summary is re-derived every month, and
                # only a still-pending duplicate of the same cycle is collapsed.
                queueing_lock=lock,
                args={"org_id": str(org_id), "crop_cycle_id": str(crop_cycle_id)},
            )
        for org_id, plot_id in await _org_plots(session):
            await _defer_item(
                session,
                task_name=COMPUTE_PLOT_MONTH_TASK_NAME,
                lock=f"metrics:plot:{plot_id}",
                queueing_lock=f"metrics:plot:{plot_id}:{month.isoformat()}",
                args={"org_id": str(org_id), "plot_id": str(plot_id), "month": month.isoformat()},
            )
        await session.commit()


@app.task(
    name=SUMMARIZE_CLOSED_CYCLE_TASK_NAME,
    queue=QUEUE_NAME,
    retry=RetryStrategy(max_attempts=2, linear_wait=30),
)
async def summarize_closed_cycle(org_id: str, crop_cycle_id: str) -> None:
    """Summarize one closed cycle and store it (D-T0.8, docs/03-modelo-datos.md:439).

    `now` is read once, here, and stamped on the row (D-T0.7): the store takes the
    instant as an argument so a stored figure always says how fresh it is, and one
    run's rows therefore agree on when it ran.

    `summarize_cycle` stores through a repository that commits
    (`crop_cycle_summary.upsert`, docs/03:442), so this job holds no transaction of
    its own. An exception bubbles up to procrastinate, which retries this cycle
    alone; the other cycles' jobs are unaffected.
    """
    async with async_session_factory() as session:
        await summarize_cycle(
            org_id=UUID(org_id),
            crop_cycle_id=UUID(crop_cycle_id),
            cycles=SqlAlchemyCropCycleRepository(session),
            plots=SqlAlchemyPlotRepository(session),
            baselines=SqlAlchemyBaselineRepository(session),
            sources=SqlAlchemyMetricsSourceRepository(session),
            summaries=SqlAlchemyCycleSummaryRepository(session),
            now=datetime.datetime.now(datetime.UTC),
        )


@app.task(
    name=COMPUTE_PLOT_MONTH_TASK_NAME,
    queue=QUEUE_NAME,
    retry=RetryStrategy(max_attempts=2, linear_wait=30),
)
async def compute_plot_month_index(org_id: str, plot_id: str, month: str) -> None:
    """Compute one plot's adoption index for one calendar month and store it
    (docs/03-modelo-datos.md:438, D-T0.2, D-T0.3).

    `month` is the first day of the month, as the fan-out resolved it; the use
    case derives the last day with `month_bounds`. The index is stored even when
    every component is null: missing evidence is a null metric, never a zero and
    never an absent row.

    The upsert commits (docs/03:442) and an exception bubbles up to procrastinate,
    which retries this plot alone — the containment the monthly run relies on.
    """
    async with async_session_factory() as session:
        await compute_plot_month(
            org_id=UUID(org_id),
            plot_id=UUID(plot_id),
            month=datetime.date.fromisoformat(month),
            plots=SqlAlchemyPlotRepository(session),
            sources=SqlAlchemyMetricsSourceRepository(session),
            metrics=SqlAlchemyMonthlyMetricRepository(session),
            computed_at=datetime.datetime.now(datetime.UTC),
        )
