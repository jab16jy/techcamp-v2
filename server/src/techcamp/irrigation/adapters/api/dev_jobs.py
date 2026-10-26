"""Seminar-only manual trigger for the irrigation jobs (docs/04-api.md:181,
ADR-0021). Registered only when the profile is seminar.

Queues the daily fan-out rather than doing the calculation inline: the balance
and recommendation belong to the worker process, and a seminar trigger wants
the same code path the 04:30 cron uses.
"""

from __future__ import annotations

import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from techcamp.irrigation.adapters.jobs import (
    QUEUE_NAME,
    balance_day_has_observed_weather,
    run_daily_plots,
)
from techcamp.shared.dates import local_today
from techcamp.shared.db import async_session_factory
from techcamp.shared.errors import ProblemError
from techcamp.shared.jobs import app as jobs_app

router = APIRouter(prefix="/dev/jobs", tags=["dev-jobs"])

_FUTURE_DAY_DETAIL = "Only today or past days can be computed: the balance day (D-1) must be over."

_NO_WEATHER_DETAIL = (
    "the balance day {balance_day} has no observed weather, so the balance would store "
    "nothing; run POST /dev/jobs/weather:run for that day first"
)


class IrrigationJobsRunRequest(BaseModel):
    """`{ day? }`: the day to compute recommendations for, defaulting to local today
    (docs/04-api.md:181). A `date` field, so a malformed value is FastAPI's 422."""

    day: datetime.date | None = None


class IrrigationJobsRunResponse(BaseModel):
    """The queued fan-out job, and what it will compute.

    `status` is "skipped" when the balance day has no observed weather: the
    balance stores no row for a day without it, so the trigger would queue a
    job that writes nothing and a presenter would read the empty result as a
    failure of the product. The weather route answers the same way for the day
    it cannot consolidate (#214).
    """

    job_id: int | None
    status: Literal["queued", "skipped"]
    detail: str | None = None


@router.post("/irrigation:run", response_model=IrrigationJobsRunResponse)
async def run_irrigation_jobs(payload: IrrigationJobsRunRequest) -> IrrigationJobsRunResponse:
    day = payload.day or local_today()
    if day > local_today():
        raise ProblemError(status=422, title="Unprocessable day", detail=_FUTURE_DAY_DETAIL)

    balance_day = day - datetime.timedelta(days=1)
    async with async_session_factory() as session:
        has_weather = await balance_day_has_observed_weather(session, day=day)
    if not has_weather:
        return IrrigationJobsRunResponse(
            job_id=None,
            status="skipped",
            detail=_NO_WEATHER_DETAIL.format(balance_day=balance_day.isoformat()),
        )

    async with jobs_app.open_async():
        job_id = await run_daily_plots.configure(queue=QUEUE_NAME).defer_async(
            timestamp=0, day=day.isoformat()
        )
    return IrrigationJobsRunResponse(job_id=job_id, status="queued")
