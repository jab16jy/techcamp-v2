"""Seminar-only manual trigger for the daily risk job (docs/04-api.md §Solo perfil
seminario (`/dev`), docs/06-diseno-detallado.md §8; ADR-0021). Registered only when
the profile is seminar.

It *queues* the scheduled run rather than doing its work inline: the archive calls
belong to the worker, which owns the Open-Meteo adapter and its circuit breaker,
and a seminar that triggers a run wants the same code path the 06:00 cron uses.
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from techcamp.risk.adapters.jobs import QUEUE_NAME, predict_active_cells
from techcamp.shared.dates import local_today
from techcamp.shared.errors import ProblemError
from techcamp.shared.jobs import app as jobs_app

router = APIRouter(prefix="/dev/jobs", tags=["dev-jobs"])

_FUTURE_DAY_DETAIL = (
    "Only today or past days can be run: the month a day belongs to is predicted with data "
    "through the last day of the previous month, so a day that has not happened yet names a "
    "month whose window does not exist."
)


class RiskJobsRunRequest(BaseModel):
    """`{ day? }`: the day whose month is predicted, defaulting to local today
    (docs/04-api.md §Solo perfil seminario). A `date` field, so a malformed value
    is FastAPI's 422 rather than a `fromisoformat` failure inside the job."""

    day: datetime.date | None = None


class RiskJobsRunResponse(BaseModel):
    """The queued run: one job, because the run itself walks the cells
    (D-T6b.2)."""

    job_id: int


@router.post("/risk:run", response_model=RiskJobsRunResponse)
async def run_risk_jobs(payload: RiskJobsRunRequest) -> RiskJobsRunResponse:
    day = payload.day or local_today()
    if day > local_today():
        raise ProblemError(status=422, title="Unprocessable day", detail=_FUTURE_DAY_DETAIL)
    # `queue` on the defer, not only on the task: procrastinate's `defer_async`
    # builds the job from the options it is handed and never from the task's own
    # default (`configure_task`), so without it the job would sit in the default
    # queue this project's worker does not listen to.
    #
    # The procrastinate `App` is opened per call because nothing else opens it in
    # the `api` process, for the reason `weather`'s route spells out.
    async with jobs_app.open_async():
        job_id = await predict_active_cells.configure(queue=QUEUE_NAME).defer_async(
            timestamp=0, day=day.isoformat()
        )
    return RiskJobsRunResponse(job_id=job_id)
