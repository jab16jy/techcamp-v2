"""Procrastinate jobs for the alerts module (E7 T6a, docs/06 §3, docs/10 §3;
ADR-0012, D21).

The 5 min node-health sweep fans one job out per organization and each of those
pages that organization's own nodes (`telemetry.NodeRepository.list_for_org`
keeps requiring `org_id`, which is what docs/09 org isolation asks of every
repository), so no read here crosses an organization. The per-org `lock` and
`queueing_lock` strings are the same fan-out the weather and irrigation jobs
already use: one sweep per org at a time, a second enqueue collapsed into the
waiting one.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import UUID

from procrastinate import RetryStrategy
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.repositories import (
    SqlAlchemyAlertRepository,
    SqlAlchemyAlertRuleRepository,
)
from techcamp.alerts.application import evaluate_node_health
from techcamp.shared.db import async_session_factory
from techcamp.shared.jobs import app
from techcamp.telemetry.adapters.orm import NodeRow
from techcamp.telemetry.adapters.repositories import (
    SqlAlchemyNodeRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemySensorRepository,
)

logger = logging.getLogger(__name__)

QUEUE_NAME = "alerts"
SWEEP_TASK_NAME = "alerts.sweep_node_health"
EVALUATE_ORG_TASK_NAME = "alerts.evaluate_org_node_health"


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


async def _defer_org_job(session: AsyncSession, *, task_name: str, org_id: UUID) -> None:
    """Defers one per-org job over `session`, inside whatever transaction the
    caller already has open.

    Runs inside a savepoint (`begin_nested`) so the duplicate `queueing_lock` a
    second sweep of the same org raises as an `IntegrityError` is caught and
    logged without aborting the caller's transaction, and every other org's job
    still lands (the weather and irrigation fan-outs, same shape).
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
                    "lock": f"alerts:org:{org_id}",
                    "queueing_lock": f"alerts:org:{org_id}",
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


@app.task(
    name=EVALUATE_ORG_TASK_NAME,
    queue=QUEUE_NAME,
    retry=RetryStrategy(max_attempts=2, linear_wait=30),
)
async def evaluate_org_node_health(org_id: str) -> None:
    """Decides `node_offline` for one organization's nodes, all of them at the
    same `at`: the sweep's own decision time (ADR-0012, jobs are idempotent, so
    a retry of the same job decides the same thing)."""
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
