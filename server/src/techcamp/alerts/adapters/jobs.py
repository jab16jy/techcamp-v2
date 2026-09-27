"""Procrastinate jobs for the alerts module (E7 T6a, T6b, docs/06 §3,
docs/10 §3; ADR-0012, D10, D21, D23).

Every sweep here fans one job out per organization and each of those pages that
organization's own data (`telemetry.NodeRepository.list_for_org` and
`farms.FarmRepository.list_for_org` keep requiring `org_id`, which is what
docs/09 org isolation asks of every repository), so no read here crosses an
organization. The per-org `lock` and `queueing_lock` strings are the same
fan-out the weather and irrigation jobs already use: one sweep per org at a
time, a second enqueue collapsed into the waiting one. The two weather rules
take a lock suffix of their own, so a 3 h forecast run and the daily cell-day
run of the same organization never wait on each other.

The forecast rules run on their OWN periodic, ten minutes after each 3 h weather
refresh, and are never called by the weather job (D10: docs/05 has no
`weather -> alerts` edge). That cron carries one honest assumption, recorded
here: it reads whatever `weather_daily` holds ten minutes after the refresh
began, so a provider slower than that leaves one round reading the previous
forecast. The job is idempotent and the next round catches up, which is why D10
does not prefer a hook inside the refresh.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from procrastinate import RetryStrategy
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.repositories import (
    SqlAlchemyAlertRepository,
    SqlAlchemyAlertRuleRepository,
)
from techcamp.alerts.application import evaluate_node_health, evaluate_weather_rules
from techcamp.farms.adapters.orm import PlotRow
from techcamp.farms.adapters.repositories import (
    SqlAlchemyFarmRepository,
    SqlAlchemyPlotRepository,
    SqlAlchemySoilProfileRepository,
)
from techcamp.shared.db import async_session_factory
from techcamp.shared.jobs import app
from techcamp.telemetry.adapters.orm import NodeRow
from techcamp.telemetry.adapters.repositories import (
    SqlAlchemyNodeRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemySensorRepository,
)
from techcamp.weather.adapters.repositories import SqlAlchemyWeatherRepository

logger = logging.getLogger(__name__)

QUEUE_NAME = "alerts"
SWEEP_TASK_NAME = "alerts.sweep_node_health"
EVALUATE_ORG_TASK_NAME = "alerts.evaluate_org_node_health"
FORECAST_SWEEP_TASK_NAME = "alerts.sweep_forecast_rules"
EVALUATE_ORG_FORECAST_TASK_NAME = "alerts.evaluate_org_forecast_rules"
FUNGAL_SWEEP_TASK_NAME = "alerts.sweep_fungal_risk"
EVALUATE_ORG_FUNGAL_TASK_NAME = "alerts.evaluate_org_fungal_risk"


async def _orgs_with_nodes(session: AsyncSession) -> list[UUID]:
    """The organizations that have a claimed node, the ones the sweep must judge.

    The one read that is not org-scoped, and it reads ids only: the fan-out
    exists exactly so every other read keeps its `org_id` (D21). A node is
    claimed or it is not (`ck_node_ownership_all_or_nothing`), so `org_id` set
    is what "has a node" means.
    """
    result = await session.execute(
        select(NodeRow.org_id)
        .where(NodeRow.org_id.is_not(None))
        .distinct()
        .order_by(NodeRow.org_id)
    )
    return [org_id for org_id in result.scalars() if org_id is not None]


async def _orgs_with_plots(session: AsyncSession) -> list[UUID]:
    """The organizations that have a plot, the ones a weather rule is about.

    The same ids-only read as `_orgs_with_nodes`, and for the same reason: the
    fan-out exists so that every other read keeps its `org_id` (D21). A plot
    belongs to exactly one organization, so `org_id` set is what "has a plot"
    means.
    """
    result = await session.execute(select(PlotRow.org_id).distinct().order_by(PlotRow.org_id))
    return [org_id for org_id in result.scalars() if org_id is not None]


async def _defer_org_job(
    session: AsyncSession, *, task_name: str, org_id: UUID, source: str | None = None
) -> None:
    """Defers one per-org job over `session`, inside whatever transaction the
    caller already has open.

    Runs inside a savepoint (`begin_nested`) so the duplicate `queueing_lock` a
    second sweep of the same org raises as an `IntegrityError` is caught and
    logged without aborting the caller's transaction, and every other org's job
    still lands (the weather and irrigation fan-outs, same shape).

    `source` names the rule source inside the lock, so the two weather sweeps of
    one organization serialize against themselves and not against each other.
    The node-health sweep passes none and keeps the plain per-org lock it has
    always had.
    """
    lock = f"alerts:org:{org_id}" if source is None else f"alerts:org:{org_id}:{source}"
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
                    "queueing_lock": lock,
                    "args": json.dumps({"org_id": str(org_id)}),
                },
            )
    except IntegrityError:
        logger.warning("alerts: job for org %s already queued", org_id, exc_info=True)


# `queue` has to be repeated on `periodic`: it configures the job from scratch
# (procrastinate's `configure_task` reads the options it is handed, never the
# task's own default), and a job left in the default queue would sit where this
# worker does not listen.
@app.periodic(cron="*/5 * * * *", queue=QUEUE_NAME)
@app.task(
    name=SWEEP_TASK_NAME, queue=QUEUE_NAME, retry=RetryStrategy(max_attempts=2, linear_wait=30)
)
async def sweep_node_health(timestamp: int) -> None:
    """Every 5 min: one node-health job per organization (docs/06 §3 "Salud del
    nodo", docs/10 §3 `m[cada 5 min: salud de nodos]`).

    The cron is read in the worker's own local time (croniter on a naive local
    clock), so the `worker` service runs in `America/Bogota` (infra/compose.yaml),
    the zone docs/10 §3 fixes every job hour to.
    """
    async with async_session_factory() as session:
        for org_id in await _orgs_with_nodes(session):
            await _defer_org_job(session, task_name=EVALUATE_ORG_TASK_NAME, org_id=org_id)
        await session.commit()


@app.periodic(cron="10 */3 * * *", queue=QUEUE_NAME)
@app.task(
    name=FORECAST_SWEEP_TASK_NAME,
    queue=QUEUE_NAME,
    retry=RetryStrategy(max_attempts=2, linear_wait=30),
)
async def sweep_forecast_rules(timestamp: int) -> None:
    """Every 3 h, ten minutes in: one forecast-rules job per organization
    (D10, D23; docs/10 §3 `k --> l`).

    The ten minutes are what the weather refresh is given to finish; a slower
    provider leaves this round reading the previous forecast, which the next
    round corrects (ADR-0012: the job is idempotent). The cron is read in the
    worker's own local time, so the `worker` service runs in `America/Bogota`
    (infra/compose.yaml), the zone docs/10 §3 fixes every job hour to.
    """
    async with async_session_factory() as session:
        for org_id in await _orgs_with_plots(session):
            await _defer_org_job(
                session, task_name=EVALUATE_ORG_FORECAST_TASK_NAME, org_id=org_id, source="forecast"
            )
        await session.commit()


@app.periodic(cron="45 4 * * *", queue=QUEUE_NAME)
@app.task(
    name=FUNGAL_SWEEP_TASK_NAME,
    queue=QUEUE_NAME,
    retry=RetryStrategy(max_attempts=2, linear_wait=30),
)
async def sweep_fungal_risk(timestamp: int) -> None:
    """Daily at 04:45: one `fungal_risk` job per organization (D23).

    The hour is the one docs/10 §3 leaves unnamed for this rule, and it sits
    where the DAG puts it: after the 04:30 balance and before the 05:00 morning
    push, so the day's alert is in the tray the producer opens.
    """
    async with async_session_factory() as session:
        for org_id in await _orgs_with_plots(session):
            await _defer_org_job(
                session, task_name=EVALUATE_ORG_FUNGAL_TASK_NAME, org_id=org_id, source="fungal"
            )
        await session.commit()


@app.task(
    name=EVALUATE_ORG_TASK_NAME,
    queue=QUEUE_NAME,
    retry=RetryStrategy(max_attempts=2, linear_wait=30),
)
async def evaluate_org_node_health(org_id: str) -> None:
    """Decides `node_offline` for one organization's nodes, all of them at the
    same `at`: this job's own run time, read with `datetime.now(UTC)` when the
    job starts (ADR-0012). A retry of the same job therefore decides at a LATER
    `at` than the attempt that failed — which is the point of the margin rather
    than a defect: the silence is a duration, so re-reading the clock can only
    make an alert fire later, never undo one."""
    async with async_session_factory() as session:
        await evaluate_node_health(
            org_id=UUID(org_id),
            at=datetime.now(UTC),
            rules=SqlAlchemyAlertRuleRepository(session),
            nodes=SqlAlchemyNodeRepository(session),
            sensors=SqlAlchemySensorRepository(session),
            readings=SqlAlchemyReadingRepository(session),
            alerts=SqlAlchemyAlertRepository(session),
        )
        await session.commit()


async def _evaluate_weather(session: AsyncSession, org_id: UUID, day: date) -> None:
    await evaluate_weather_rules(
        org_id=org_id,
        at=datetime.now(UTC),
        day=day,
        rules=SqlAlchemyAlertRuleRepository(session),
        farms=SqlAlchemyFarmRepository(session),
        plots=SqlAlchemyPlotRepository(session),
        soils=SqlAlchemySoilProfileRepository(session),
        weather=SqlAlchemyWeatherRepository(session),
        nodes=SqlAlchemyNodeRepository(session),
        sensors=SqlAlchemySensorRepository(session),
        readings=SqlAlchemyReadingRepository(session),
        alerts=SqlAlchemyAlertRepository(session),
    )
    await session.commit()


@app.task(
    name=EVALUATE_ORG_FORECAST_TASK_NAME,
    queue=QUEUE_NAME,
    retry=RetryStrategy(max_attempts=2, linear_wait=30),
)
async def evaluate_org_forecast_rules(org_id: str) -> None:
    """`heavy_rain_forecast` for one organization, over the forecast day the
    warning is about: the next forecast day, the first complete one (D20: a day
    IS the 24 h the rule names). All of the org's plots are decided at the same
    `at`, this job's own decision time."""
    now = datetime.now(UTC)
    async with async_session_factory() as session:
        await _evaluate_weather(session, UUID(org_id), now.date() + timedelta(days=1))


@app.task(
    name=EVALUATE_ORG_FUNGAL_TASK_NAME,
    queue=QUEUE_NAME,
    retry=RetryStrategy(max_attempts=2, linear_wait=30),
)
async def evaluate_org_fungal_risk(org_id: str) -> None:
    """`fungal_risk` for one organization, over the cell-day the 03:00
    consolidation just closed: the day before this job's own (D10, D19)."""
    now = datetime.now(UTC)
    async with async_session_factory() as session:
        await _evaluate_weather(session, UUID(org_id), now.date() - timedelta(days=1))
