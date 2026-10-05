"""Tests for the seminar-only `/dev/jobs/metrics:run` trigger (E11 T6, docs/04:261).

ADR-0021: the route exists only in the seminar profile. Like the weather and
irrigation triggers it *queues* the run instead of computing inline, so a seminar
that presses the button exercises the same code path the day-one cron uses.
"""

from __future__ import annotations

import datetime
import importlib
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.main import app
from techcamp.metrics.adapters.jobs import QUEUE_NAME, RUN_MONTHLY_METRICS_TASK_NAME
from techcamp.shared.dates import local_today

pytestmark = pytest.mark.anyio

_ROUTE = "/dev/jobs/metrics:run"
_REGISTERED_PATH = "/api/v1/dev/jobs/metrics:run"


@pytest.fixture(autouse=True)
async def _clear_jobs(db_session: AsyncSession):
    yield
    await db_session.execute(text("DELETE FROM procrastinate_jobs"))
    await db_session.commit()


def _client() -> TestClient:
    return TestClient(app, base_url="http://testserver/api/v1")


def _registered_paths(application: Any) -> set[str]:
    return set(application.openapi()["paths"])


async def _queued_runs(db_session: AsyncSession) -> dict[int, dict[str, Any]]:
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
                    "WHERE task_name = :t ORDER BY id"
                ),
                {"t": RUN_MONTHLY_METRICS_TASK_NAME},
            )
        ).all()
    }


async def test_the_dev_route_queues_the_monthly_run_on_the_metrics_queue(
    db_session: AsyncSession,
) -> None:
    """docs/04:261: `day` defaults to today in America/Bogota, so the queued run indexes
    the month before today, and the job sits on the queue the worker listens to.
    """
    response = _client().post(_ROUTE, json={})
    assert response.request.url.path == _REGISTERED_PATH
    assert response.status_code == 200
    body = response.json()
    assert "job_id" in body

    queued = await _queued_runs(db_session)
    assert body["job_id"] in queued
    assert queued[body["job_id"]]["task_name"] == RUN_MONTHLY_METRICS_TASK_NAME
    assert queued[body["job_id"]]["queue_name"] == QUEUE_NAME
    assert queued[body["job_id"]]["status"] == "todo"
    assert queued[body["job_id"]]["args"]["day"] == local_today().isoformat()


async def test_the_route_carries_the_chosen_day_to_the_job(db_session: AsyncSession) -> None:
    """A seminar run for an older month enqueues that day, and the job resolves the
    month before it."""
    day = local_today() - datetime.timedelta(days=40)
    response = _client().post(_ROUTE, json={"day": day.isoformat()})
    assert response.status_code == 200
    queued = await _queued_runs(db_session)
    assert queued[response.json()["job_id"]]["args"]["day"] == day.isoformat()


async def test_the_dev_route_rejects_an_invalid_date(db_session: AsyncSession) -> None:
    """An unparseable string is FastAPI validation, not a `fromisoformat` failure inside
    the worker (docs/04:261)."""
    response = _client().post(_ROUTE, json={"day": "manana"})
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/json"


async def test_the_dev_route_is_absent_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-0021: `/dev` routes are registered only when the profile is seminar."""
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
