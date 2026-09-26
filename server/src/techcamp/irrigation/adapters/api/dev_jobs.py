"""Seminar-only manual trigger for the irrigation jobs (docs/04-api.md:181,
ADR-0021). Registered only when the profile is seminar.

Queues the daily fan-out rather than doing the calculation inline: the balance
and recommendation belong to the worker process, and a seminar trigger wants
the same code path the 04:30 cron uses.
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from techcamp.irrigation.adapters.jobs import QUEUE_NAME, local_today, run_daily_plots
from techcamp.shared.errors import ProblemError
from techcamp.shared.jobs import app as jobs_app

router = APIRouter(prefix="/dev/jobs", tags=["dev-jobs"])

_FUTURE_DAY_DETAIL = "Only today or past days can be computed: the balance day (D-1) must be over."


class IrrigationJobsRunRequest(BaseModel):
    """`{ day? }`: the day to compute recommendations for, defaulting to local today
    (docs/04-api.md:181). A `date` field, so a malformed value is FastAPI's 422."""

    day: datetime.date | None = None


class IrrigationJobsRunResponse(BaseModel):
    """The queued fan-out job."""

    job_id: int


@router.post("/irrigation:run", response_model=IrrigationJobsRunResponse)
async def run_irrigation_jobs(payload: IrrigationJobsRunRequest) -> IrrigationJobsRunResponse:
    day = payload.day or local_today()
    if day > local_today():
        raise ProblemError(status=422, title="Unprocessable day", detail=_FUTURE_DAY_DETAIL)

    async with jobs_app.open_async():
        job_id = await run_daily_plots.configure(queue=QUEUE_NAME).defer_async(
            timestamp=0, day=day.isoformat()
        )
    return IrrigationJobsRunResponse(job_id=job_id)
