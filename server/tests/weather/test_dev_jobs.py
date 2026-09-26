"""Seminar-only manual trigger for the weather jobs (T5b, docs/04-api.md:177,
ADR-0021): `POST /api/v1/dev/jobs/weather:run { day? }`, which queues the 3 h
forecast refresh and the daily consolidation right now so a seminar does not
wait for the cron.

The route is registered only in the seminar profile, so these tests also pin its
absence in production — the decision `main.py` makes once, at import time.
"""

from __future__ import annotations

import datetime
import importlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.main import app
from techcamp.weather.adapters.jobs import (
    CONSOLIDATE_ACTIVE_CELLS_TASK_NAME,
    QUEUE_NAME,
    REFRESH_ACTIVE_CELLS_TASK_NAME,
    local_today,
)

pytestmark = pytest.mark.anyio

# Client-relative: the TestClient's base_url already ends in `/api/v1` and this
# httpx version appends a leading-slash path to it rather than replacing it, so
# the registered path has to be asserted separately (see below).
_ROUTE = "/dev/jobs/weather:run"
_REGISTERED_PATH = "/api/v1/dev/jobs/weather:run"


@pytest.fixture(autouse=True)
async def _clear_jobs(db_session: AsyncSession):
    """The route defers through procrastinate's own psycopg connector, on a
    connection this session cannot clean up through, so the jobs it leaves would
    outlive the test."""
    yield
    await db_session.execute(text("DELETE FROM procrastinate_jobs"))
    await db_session.commit()


def _client() -> TestClient:
    return TestClient(app, base_url="http://testserver/api/v1")


async def _queued(db_session: AsyncSession) -> dict[int, dict[str, object]]:
    """The queued *fan-out* jobs, by id.

    Scoped to the two task names because the route queues whole runs, not
    per-cell jobs: other suites leave `weather.refresh_cell` jobs behind (every
    plot created on a cold cell defers one, T5a), and those are not what this
    route queues.
    """
    return {
        job.id: {
            "task_name": job.task_name,
            "queue_name": job.queue_name,
            "status": job.status,
            "args": job.args,
        }
        for job in (
            await db_session.execute(
                text(
                    "SELECT id, task_name, queue_name, status, args FROM procrastinate_jobs "
                    "WHERE task_name = ANY(ARRAY[:a, :b]) ORDER BY id"
                ),
                {
                    "a": REFRESH_ACTIVE_CELLS_TASK_NAME,
                    "b": CONSOLIDATE_ACTIVE_CELLS_TASK_NAME,
                },
            )
        ).all()
    }


def _only(queued: dict[int, dict[str, object]], task_name: str) -> dict[str, object]:
    return next(job for job in queued.values() if job["task_name"] == task_name)


def _registered_paths(application: object) -> set[str]:
    """Every path the app serves, from the OpenAPI schema rather than
    `app.routes`: this FastAPI version keeps an included router as one entry
    instead of flattening its routes, so `app.routes` does not name them."""
    return set(application.openapi()["paths"])


async def test_the_route_queues_the_refresh_and_the_consolidation(
    db_session: AsyncSession,
) -> None:
    """docs/04-api.md:177: the seminar runs both halves of the weather schedule on
    demand. Both jobs must land in the `weather` queue, the only one the worker
    listens to — procrastinate's `defer` builds a job from the options it is
    handed and never from the task's own default, so a job deferred without
    saying so would sit in the default queue and never run. That is asserted
    here rather than assumed."""
    response = _client().post(_ROUTE, json={})

    # docs/04-api.md:177 documents the path without the version prefix, like
    # every other route there; the versioned one is what is actually served.
    assert response.request.url.path == _REGISTERED_PATH
    assert response.status_code == 200
    body = response.json()
    queued = await _queued(db_session)
    assert set(queued) == {body["job_id"], body["consolidate_job_id"]}
    assert {job["queue_name"] for job in queued.values()} == {QUEUE_NAME}
    assert {job["status"] for job in queued.values()} == {"todo"}
    assert {job["task_name"] for job in queued.values()} == {
        REFRESH_ACTIVE_CELLS_TASK_NAME,
        CONSOLIDATE_ACTIVE_CELLS_TASK_NAME,
    }


async def test_the_route_consolidates_the_chosen_day(db_session: AsyncSession) -> None:
    """`day` names the day to consolidate, so a seminar can consolidate a day the
    worker was down for instead of only yesterday."""
    day = local_today() - datetime.timedelta(days=2)

    response = _client().post(_ROUTE, json={"day": day.isoformat()})

    assert response.status_code == 200
    queued = await _queued(db_session)
    # procrastinate adds its own `timestamp` to every job's args, so the day is
    # read out of them rather than the whole dict compared.
    assert _only(queued, CONSOLIDATE_ACTIVE_CELLS_TASK_NAME)["args"]["day"] == day.isoformat()
    # The refresh takes no day: its window is the provider's 16 forecast days.
    assert "day" not in _only(queued, REFRESH_ACTIVE_CELLS_TASK_NAME)["args"]


async def test_the_route_defaults_to_consolidating_yesterday(db_session: AsyncSession) -> None:
    """docs/10-dag.md §3: the daily job consolidates the previous day, so a run
    with no `day` does exactly what the 03:00 cron would."""
    response = _client().post(_ROUTE, json={})

    assert response.status_code == 200
    queued = await _queued(db_session)
    expected = (local_today() - datetime.timedelta(days=1)).isoformat()
    assert _only(queued, CONSOLIDATE_ACTIVE_CELLS_TASK_NAME)["args"]["day"] == expected


async def test_the_route_rejects_a_day_that_has_not_happened(db_session: AsyncSession) -> None:
    """A day in the future has no observed weather to consolidate, and asking the
    provider for it would be a negative `past_days` — a 500 for a typo in a
    seminar-only tool, instead of the 422 this repo answers a bad value with."""
    tomorrow = local_today() + datetime.timedelta(days=1)

    response = _client().post(_ROUTE, json={"day": tomorrow.isoformat()})

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert await _queued(db_session) == {}


async def test_the_route_rejects_a_day_that_is_not_a_day(db_session: AsyncSession) -> None:
    """A value that is not a date is FastAPI's own validation failure, which this
    repo answers as a plain JSON 422 (`shared/errors.py`'s validation handler),
    not as a `ProblemError` — the response shape is asserted so the difference
    stays a decision rather than a drift."""
    response = _client().post(_ROUTE, json={"day": "ayer"})

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/json"
    assert await _queued(db_session) == {}


async def test_the_route_is_absent_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-0021 and docs/04-api.md: the `/dev` endpoints "no se registran en el
    perfil `production`". `main.py` decides that at import time, so the
    production app has to be built to be inspected: the module is reloaded under
    the production profile and restored afterwards, which is also what keeps the
    rest of the suite on the seminar app."""
    import techcamp.main as main

    assert _REGISTERED_PATH in _registered_paths(app), "seminar profile keeps the route"
    monkeypatch.setenv("TECHCAMP_PROFILE", "production")
    try:
        production_app = importlib.reload(main).app
        assert _REGISTERED_PATH not in _registered_paths(production_app)
    finally:
        monkeypatch.undo()
        importlib.reload(main)
    assert _REGISTERED_PATH in _registered_paths(main.app)
