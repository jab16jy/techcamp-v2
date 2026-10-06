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
from zoneinfo import ZoneInfo

from procrastinate import RetryStrategy
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.repositories import (
    SqlAlchemyAlertRepository,
    SqlAlchemyAlertRuleRepository,
)
from techcamp.alerts.application import (
    escalate_due_alerts,
    evaluate_balance_rules,
    evaluate_node_health,
    evaluate_weather_rules,
)
from techcamp.farms.adapters.orm import PlotRow
from techcamp.farms.adapters.repositories import (
    SqlAlchemyFarmRepository,
    SqlAlchemyPlotRepository,
    SqlAlchemySoilProfileRepository,
)
from techcamp.irrigation.adapters.repositories import SqlAlchemyWaterBalanceRepository
from techcamp.shared.db import async_session_factory
from techcamp.shared.jobs import app
from techcamp.telemetry.adapters.orm import NodeRow
from techcamp.telemetry.adapters.repositories import (
    SqlAlchemyCalibrationRepository,
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
BALANCE_SWEEP_TASK_NAME = "alerts.sweep_balance_rules"
EVALUATE_ORG_BALANCE_TASK_NAME = "alerts.evaluate_org_balance_rules"
ESCALATION_SWEEP_TASK_NAME = "alerts.sweep_escalations"
ESCALATE_ORG_TASK_NAME = "alerts.escalate_org_alerts"


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


async def orgs_with_plots(session: AsyncSession) -> list[UUID]:
    """The organizations that have a plot, the ones a weather rule is about.

    The same ids-only read as `_orgs_with_nodes`, and for the same reason: the
    fan-out exists so that every other read keeps its `org_id` (D21). A plot
    belongs to exactly one organization, so `org_id` set is what "has a plot"
    means. Public because the model rules fan out the same way from outside this
    module (`alerts.adapters.evaluate_risk`, driven by the daily risk job).
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


@app.periodic(cron="*/5 * * * *", queue=QUEUE_NAME)
@app.task(
    name=ESCALATION_SWEEP_TASK_NAME,
    queue=QUEUE_NAME,
    retry=RetryStrategy(max_attempts=2, linear_wait=30),
)
async def sweep_escalations(timestamp: int) -> None:
    """Every 5 min: one escalation job per organization (docs/06 §3 "Reloj de
    escalamiento"; docs/10 §3; D43).

    The hour docs/10 §3 does not name, fixed the way D23 and D28 fixed theirs: the
    same 5 minutes as the node-health sweep, because both are the notice a
    technician gets that something is wrong, and the outbox that carries the SMS
    already sweeps every minute (docs/06 §4), so the delivery does not wait on
    this hour. The cron is read in the worker's own local time, so the `worker`
    service runs in `America/Bogota` (infra/compose.yaml).

    The orgs are the ones that have PLOTS, like the weather sweeps: an alert
    targets a plot or a node, and a node alert belongs to the plot its node hangs
    on, so an organization with a plot covers both. An org with no plot cannot
    hold an alert, so it must not cost a job.
    """
    async with async_session_factory() as session:
        for org_id in await orgs_with_plots(session):
            await _defer_org_job(
                session, task_name=ESCALATE_ORG_TASK_NAME, org_id=org_id, source="escalation"
            )
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
        for org_id in await orgs_with_plots(session):
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
        for org_id in await orgs_with_plots(session):
            await _defer_org_job(
                session, task_name=EVALUATE_ORG_FUNGAL_TASK_NAME, org_id=org_id, source="fungal"
            )
        await session.commit()


@app.periodic(cron="50 4 * * *", queue=QUEUE_NAME)
@app.task(
    name=BALANCE_SWEEP_TASK_NAME,
    queue=QUEUE_NAME,
    retry=RetryStrategy(max_attempts=2, linear_wait=30),
)
async def sweep_balance_rules(timestamp: int) -> None:
    """Daily at 04:50: one balance-rules job per organization (D28).

    The balance branch of `water_stress` runs on its OWN periodic, never called by
    the irrigation job: docs/05 has no `irrigation → alerts` edge and E6 wires no
    call into its job. The hour is the one docs/10 §3 does not name, fixed like
    D23 fixed `fungal_risk`: after the 04:30 balance and the 04:45 fungal rule,
    before the 05:00 morning push, so the day's stress alert is in the tray the
    producer opens. The cron carries one honest assumption, as D23's does: it
    reads whatever `water_balance_daily` holds at 04:50, so a slower balance run
    leaves one round without the newest day; the job is idempotent and the next
    round catches up.
    """
    async with async_session_factory() as session:
        for org_id in await orgs_with_plots(session):
            await _defer_org_job(
                session, task_name=EVALUATE_ORG_BALANCE_TASK_NAME, org_id=org_id, source="balance"
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


@app.task(
    name=ESCALATE_ORG_TASK_NAME,
    queue=QUEUE_NAME,
    retry=RetryStrategy(max_attempts=2, linear_wait=30),
)
async def escalate_org_alerts(org_id: str) -> None:
    """Escalate one organization's criticals whose 2 h are up (docs/06 §3; D12).

    The job decides at its own run time, `datetime.now(UTC)`, for the same reason
    `evaluate_org_node_health` does: a retry decides at a LATER `at` than the
    attempt that failed, and here a later `at` can only bring MORE alerts into
    the window — never undo an escalation, since `escalated_at` is already set.
    The lock inside the use case is what keeps two workers from escalating the
    same alert twice, and the SMS is not sent from here: the row goes through the
    outbox (ADR-0016).
    """
    async with async_session_factory() as session:
        await escalate_due_alerts(
            org_id=UUID(org_id),
            at=datetime.now(UTC),
            alerts=SqlAlchemyAlertRepository(session),
        )
        await session.commit()


_LOCAL = ZoneInfo("America/Bogota")
"""The zone docs/10-dag.md fixes every job hour to, and so the zone a calendar
DAY means to these rules, as opposed to the instant `at` is measured in (UTC).
The same helper exists in `irrigation/domain/models.py::local_today`; a single
`shared/` calendar helper the three modules share is a follow-up, not a reason
to compute a farmer's day in the wrong zone."""


def local_date(now: datetime) -> date:
    """The calendar day `now` falls on in America/Bogota."""
    return now.astimezone(_LOCAL).date()


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
    `at`, this job's own decision time.

    The day is the product's day, not UTC's: docs/10-dag.md fixes every job hour
    to America/Bogota, and from 19:00 to 23:59 local `now().date() + 1` in UTC is
    already two calendar days ahead — the rule would read the wrong forecast.
    The same applies to a run the queue delays across UTC midnight."""
    now = datetime.now(UTC)
    async with async_session_factory() as session:
        await _evaluate_weather(session, UUID(org_id), local_date(now) + timedelta(days=1))


@app.task(
    name=EVALUATE_ORG_FUNGAL_TASK_NAME,
    queue=QUEUE_NAME,
    retry=RetryStrategy(max_attempts=2, linear_wait=30),
)
async def evaluate_org_fungal_risk(org_id: str) -> None:
    """`fungal_risk` for one organization, over the cell-day the 03:00
    consolidation just closed: the day before this job's own (D10, D19). In the
    product's own zone, for the same reason as the forecast job above."""
    now = datetime.now(UTC)
    async with async_session_factory() as session:
        await _evaluate_weather(session, UUID(org_id), local_date(now) - timedelta(days=1))


@app.task(
    name=EVALUATE_ORG_BALANCE_TASK_NAME,
    queue=QUEUE_NAME,
    retry=RetryStrategy(max_attempts=2, linear_wait=30),
)
async def evaluate_org_balance_rules(org_id: str) -> None:
    """The balance branch of `water_stress` for one organization (D28, Q2).

    The day is the product's day, not UTC's (docs/10 §3), and it is the day this
    run decided: the balance job that ran before it wrote the row for D−1
    (docs/06 §5), which is the newest row this job reads.
    """
    now = datetime.now(UTC)
    async with async_session_factory() as session:
        await evaluate_balance_rules(
            org_id=UUID(org_id),
            at=now,
            day=local_date(now),
            rules=SqlAlchemyAlertRuleRepository(session),
            farms=SqlAlchemyFarmRepository(session),
            plots=SqlAlchemyPlotRepository(session),
            soils=SqlAlchemySoilProfileRepository(session),
            balances=SqlAlchemyWaterBalanceRepository(session),
            nodes=SqlAlchemyNodeRepository(session),
            sensors=SqlAlchemySensorRepository(session),
            calibrations=SqlAlchemyCalibrationRepository(session),
            readings=SqlAlchemyReadingRepository(session),
            alerts=SqlAlchemyAlertRepository(session),
        )
        await session.commit()
