"""The outbox dispatch job (docs/06-diseno-detallado.md §4; ADR-0012, ADR-0016; D7).

Registered on the shared procrastinate `app` (`shared/jobs.py`) and run by the
`worker` process (`techcamp/worker.py`, docs/05-arquitectura.md:78-83). It is
both the job the outbox write defers and the minute sweep, which is what D7
asks for: the defer carries a critical alert's latency (RNF-05, p95 < 2 min)
and the sweep is what picks up a retry whose backoff has elapsed, a row whose
dispatch job was lost, and the 05:00 quiet-hours release.

`enqueue_dispatch` is a separate, SQLAlchemy-session-based helper, not a call
through this module's `app` — see `shared/jobs.py`'s module docstring for why
(asyncpg vs. psycopg). `SqlAlchemyNotificationRepository.insert_drafts` calls it
right before the alert's own commit, so the enqueue is part of the same
Postgres transaction as the notification rows (ADR-0016's outbox guarantee).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from procrastinate import RetryStrategy
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.notifications.adapters.circuits import provider_circuits
from techcamp.notifications.adapters.outbox import SqlAlchemyOutboxRepository
from techcamp.notifications.adapters.senders import build_senders
from techcamp.notifications.adapters.subscriptions import SqlAlchemyPushSubscriptionRepository
from techcamp.notifications.application import dispatch_due_notifications
from techcamp.shared.db import async_session_factory
from techcamp.shared.jobs import app

logger = logging.getLogger(__name__)

QUEUE_NAME = "notifications"
DISPATCH_TASK_NAME = "notifications.dispatch_outbox"

_DISPATCH_LOCK = "notifications:dispatch"
"""One lock, not one per row or per organization: the dispatcher already takes
rows with `FOR UPDATE SKIP LOCKED`, so what this lock buys is that a burst of
alerts collapses into a single waiting job instead of one job per alert — and
that the per-minute sweep and an insert-time defer never fight over the same
rows."""


async def enqueue_dispatch(session: AsyncSession) -> None:
    """Defer one dispatch job inside whatever transaction the caller has open.

    Runs inside a savepoint (`begin_nested`) so the duplicate `queueing_lock` a
    second write raises as an `IntegrityError` is caught and logged without
    aborting the caller's transaction — the alert, its other writes and the
    rows already queued are unaffected, exactly as in the weather, irrigation
    and alerts fan-outs.

    `args` carries the `timestamp` the task's own signature requires, for the
    same reason the `/dev/jobs` routes pass `timestamp=0` to `defer_async`: the
    periodic form gets it from procrastinate, and a job deferred with empty args
    dies in the worker with `TypeError: dispatch_outbox() missing 1 required
    positional argument`, which silently kills D7's "at insert" half while the
    minute sweep still makes the delivery look fine. Found in the T11 seminar
    demo on a live stack: every cron job `succeeded` and every deferred job
    `failed`.
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
                    "task_name": DISPATCH_TASK_NAME,
                    "priority": 0,
                    "lock": _DISPATCH_LOCK,
                    "queueing_lock": _DISPATCH_LOCK,
                    "args": json.dumps({"timestamp": 0}),
                },
            )
    except IntegrityError:
        logger.info("notifications: dispatch job already queued")


# `queue` has to be repeated on `periodic`: it configures the job from scratch
# (procrastinate's `configure_task` reads the options it is handed, never the
# task's own default), and a job left in the default queue would sit where this
# worker does not listen.
@app.periodic(cron="* * * * *", queue=QUEUE_NAME)
@app.task(
    name=DISPATCH_TASK_NAME, queue=QUEUE_NAME, retry=RetryStrategy(max_attempts=2, linear_wait=30)
)
async def dispatch_outbox(timestamp: int) -> None:
    """Drain the outbox (docs/06 §4).

    Runs both at the end of the transaction that wrote the rows (D7) and every
    minute (the sweep). Two passes at once are safe and expected: the claim
    skips the rows the other holds, so a row is not sent twice by two live
    workers. Delivery is still AT LEAST ONCE (D30): a worker that dies between
    the provider accepting a push and `sent` being committed leaves the row
    `pending` and the next sweep sends it again, which the payload's `tag` and
    the push service's `Topic` make a replacement rather than a second copy.

    The cron is read in the worker's own local time (croniter on a naive local
    clock), so the `worker` service runs in `America/Bogota` (infra/compose.yaml),
    the zone docs/10 §3 fixes every job hour to.
    """
    async with async_session_factory() as session, async_session_factory() as push_session:
        # Two sessions, not one: the Web Push sender commits when it deletes a
        # subscription the push service reported as gone, and a commit on the
        # dispatcher's own session would end the transaction holding the claim's
        # `FOR UPDATE SKIP LOCKED` locks, letting a second worker pick up the rest
        # of this batch and send it too.
        report = await dispatch_due_notifications(
            outbox=SqlAlchemyOutboxRepository(session),
            senders=build_senders(SqlAlchemyPushSubscriptionRepository(push_session)),
            # The process-wide registry, not a fresh one: "5 fallos seguidos"
            # (docs/06 §4) has to count across sweeps, or every minute would
            # restart the count and no circuit would ever open (D35).
            circuits=provider_circuits(),
            now=datetime.now(UTC),
        )
        # Each outcome commits on its own, so this only matters when every row
        # of the run was passed over (another worker held them all): their
        # claim locks are only released by a commit.
        await session.commit()
    logger.info(
        "notifications: claimed %d, sent %d, retried %d, failed %d, held %d, passed over %d",
        report.claimed,
        report.sent,
        report.retried,
        report.failed,
        report.deferred,
        report.skipped,
    )
