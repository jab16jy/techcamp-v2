"""Seminar-only manual trigger for the monthly metrics run (docs/04-api.md:261,
ADR-0021). Registered only when the profile is seminar.

It *queues* the run rather than computing inline, exactly like the weather and
irrigation triggers: a seminar that presses the button wants the same code path
the day-one 02:00 cron uses (docs/10-dag.md:178).

No day is refused here, unlike `weather:run` and `irrigation:run`: those two
measure a day that has to be over, while this one indexes the month *before*
`day` and summarizes cycles that already closed, so a day of the future is a
month that exists but holds no evidence yet — a row of nulls, which is a stored
figure the docs ask for (docs/11-metricas.md §2, D-T0.3).
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from techcamp.metrics.adapters.jobs import QUEUE_NAME, run_monthly_metrics
from techcamp.shared.dates import local_today
from techcamp.shared.jobs import app as jobs_app

router = APIRouter(prefix="/dev/jobs", tags=["dev-jobs"])


class MetricsJobsRunRequest(BaseModel):
    """`{ day? }`: the day the run resolves its month from, defaulting to local today
    (docs/04-api.md:261). A `date` field, so a malformed value is FastAPI's 422 rather
    than a `fromisoformat` failure inside the worker."""

    day: datetime.date | None = None


class MetricsJobsRunResponse(BaseModel):
    """The queued run. One id because this route queues a single job
    (docs/04-api.md:261: `metrics` is not `weather`)."""

    job_id: int


@router.post("/metrics:run", response_model=MetricsJobsRunResponse)
async def run_metrics_jobs(payload: MetricsJobsRunRequest) -> MetricsJobsRunResponse:
    # The day is always sent, default resolved here rather than in the job, so the
    # queued args say which month this run was asked for.
    day = payload.day or local_today()
    # `queue` on the defer, not just on the task: procrastinate builds the job from
    # the options it is handed and never from the task's own default, and without
    # it the job would sit in the default queue this worker does not listen to.
    async with jobs_app.open_async():
        job_id = await run_monthly_metrics.configure(queue=QUEUE_NAME).defer_async(
            timestamp=0, day=day.isoformat()
        )
    return MetricsJobsRunResponse(job_id=job_id)
