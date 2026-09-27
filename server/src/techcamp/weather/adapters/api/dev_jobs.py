"""Seminar-only manual trigger for the weather jobs (docs/04-api.md:177,
ADR-0021). Registered only when the profile is seminar.

It *queues* the two scheduled runs rather than doing their work inline: the
provider calls belong to the worker, which is the process that owns the Open-Meteo
adapter and its circuit breaker, and a seminar that triggers a run wants the same
code path the cron uses.
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from techcamp.shared.dates import local_today
from techcamp.shared.errors import ProblemError
from techcamp.shared.jobs import app as jobs_app
from techcamp.weather.adapters.jobs import (
    QUEUE_NAME,
    consolidate_active_cells,
    previous_day,
    refresh_active_cells,
)

router = APIRouter(prefix="/dev/jobs", tags=["dev-jobs"])

_UNCONSOLIDATED_DAY_DETAIL = (
    "Only a day that is already over can be consolidated: today is still a forecast, "
    "and Open-Meteo answers a request for past days only with the days it has passed."
)


class WeatherJobsRunRequest(BaseModel):
    """`{ day? }`: the day to consolidate, defaulting to the previous one
    (docs/04-api.md:177). A `date` field, so a malformed value is FastAPI's 422
    rather than a `fromisoformat` failure inside the job."""

    day: datetime.date | None = None


class WeatherJobsRunResponse(BaseModel):
    """The queued jobs. Two, because the route runs both halves of the weather
    schedule: the 3 h forecast refresh and the daily consolidation."""

    job_id: int
    consolidate_job_id: int


@router.post("/weather:run", response_model=WeatherJobsRunResponse)
async def run_weather_jobs(payload: WeatherJobsRunRequest) -> WeatherJobsRunResponse:
    day = payload.day or previous_day()
    # `>=` today, not `>`: today's weather is still a forecast, and Open-Meteo
    # answers `past_days=0, forecast_days=0` (which is what today would ask for)
    # with zero days, so accepting it would queue a consolidation that provably
    # stores nothing while the route reports success.
    if day >= local_today():
        raise ProblemError(status=422, title="Unprocessable day", detail=_UNCONSOLIDATED_DAY_DETAIL)
    # `queue` on every defer, not just on the task: procrastinate's `defer` builds
    # a job from the options it is handed and never from the task's own default
    # (`configure_task`), so without it both jobs would sit in the default queue
    # that this project's worker does not listen to.
    #
    # The procrastinate `App` is opened per call because nothing else opens it in
    # the `api` process (the worker opens its own when it runs), and a dev-only
    # trigger paying one connection per request is the cheap way to be correct —
    # a lifespan-managed queue client would hold a connection in production for a
    # route that does not exist there.
    async with jobs_app.open_async():
        refresh_id = await refresh_active_cells.configure(queue=QUEUE_NAME).defer_async(timestamp=0)
        consolidate_id = await consolidate_active_cells.configure(queue=QUEUE_NAME).defer_async(
            timestamp=0, day=day.isoformat()
        )
    return WeatherJobsRunResponse(job_id=refresh_id, consolidate_job_id=consolidate_id)
