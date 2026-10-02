"""The daily risk job and its seminar-only trigger (E10 T6b, docs/06-diseno-detallado.md
§8, docs/04-api.md §Solo perfil seminario (`/dev`), ADR-0012, ADR-0021; D-T6b.2).

Against real Postgres, because the rows are written by the real repository: a
rerun's idempotence is `uq_risk_prediction_cell_event_month_version` (docs/03
§Unicidad de la predicción) and not a double's memory. The archive is T6a's own
seminar adapter replaying its recorded responses (ADR-0021), and the predictor is
the double T9 replaces with the promoted model's.
"""

from __future__ import annotations

import datetime
import importlib
from collections.abc import Mapping
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import techcamp.risk.adapters.jobs as jobs_module
from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import OrganizationRow
from techcamp.main import app
from techcamp.risk.adapters.jobs import (
    QUEUE_NAME,
    RUN_ACTIVE_CELLS_TASK_NAME,
    predict_active_cells,
)
from techcamp.risk.adapters.open_meteo_archive import seminar_archive_adapter
from techcamp.risk.adapters.orm import ModelVersionRow
from techcamp.risk.application.ports import PredictionOutcome, PredictorRegistry
from techcamp.risk.domain.models import ModelVersion
from techcamp.shared.dates import local_today
from techcamp.shared.ids import uuid7
from techcamp.shared.jobs import app as jobs_app
from techcamp.weather.adapters.repositories import SqlAlchemyWeatherRepository

pytestmark = pytest.mark.anyio

_ROUTE = "/dev/jobs/risk:run"
_REGISTERED_PATH = "/api/v1/dev/jobs/risk:run"
_DAY = datetime.date(2026, 10, 2)
_VERSION = "2026-10-02"
_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)


@pytest.fixture(autouse=True)
async def _clear_jobs(db_session: AsyncSession):
    """The route defers through procrastinate's own psycopg connector, on a
    connection this session cannot clean up through (same as the weather and
    irrigation route tests)."""
    yield
    await db_session.execute(text("DELETE FROM procrastinate_jobs"))
    await db_session.commit()


class _FixedPredictor:
    """The predictor double: one probability for every version and cell."""

    def __init__(self, probability: float) -> None:
        self._probability = probability

    def predict(
        self, version: ModelVersion, features: Mapping[str, float | None]
    ) -> PredictionOutcome:
        return PredictionOutcome(
            probability=self._probability,
            top_factors=[{"feature": "precip_sum_1m", "value": 900.0, "contribution": 0.4}],
        )


def _use_doubles(
    monkeypatch: pytest.MonkeyPatch, *, with_predictors: bool = True, with_cells: bool = True
) -> None:
    if with_cells:
        # The composition root does this in `techcamp/worker.py`
        # (docs/05 §Solo la fachada pública); tests wire it the same way.
        jobs_module.configure_weather_cells(SqlAlchemyWeatherRepository)
    monkeypatch.setattr(jobs_module, "_archive", lambda: seminar_archive_adapter())
    monkeypatch.setattr(
        jobs_module,
        "_predictors",
        lambda: PredictorRegistry(
            {
                ("risk_flood", _VERSION): _FixedPredictor(0.82),
                ("risk_drought", _VERSION): _FixedPredictor(0.2),
            }
            if with_predictors
            else {}
        ),
    )


async def _cell_with_plot(db_session: AsyncSession, *, lat: str, lon: str) -> int:
    """A cell and a plot on it, the arrangement that makes a cell active
    (docs/06-diseno-detallado.md §6)."""
    cell_id = await SqlAlchemyWeatherRepository(db_session).get_or_create_cell(
        Decimal(lat), Decimal(lon)
    )
    org_id = uuid7()
    db_session.add(OrganizationRow(id=org_id, name="Finca", kind="individual"))
    await db_session.commit()
    farm_id = uuid7()
    db_session.add(
        FarmRow(id=farm_id, org_id=org_id, name="Finca", municipality_code="47001", location=_POINT)
    )
    await db_session.commit()
    plot_id: UUID = uuid7()
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=_BOUNDARY,
            irrigation_system="none",
            weather_cell_id=cell_id,
        )
    )
    await db_session.commit()
    return cell_id


async def _register_versions(db_session: AsyncSession, *names: str) -> None:
    for name in names:
        db_session.add(
            ModelVersionRow(
                id=uuid7(),
                name=name,
                version=_VERSION,
                artifact_uri="s3://models/risk.ubj",
                is_baseline=False,
                thresholds={"high": 0.7, "critical": 0.85},
                promoted=True,
                created_at=datetime.datetime(2026, 10, 2, tzinfo=datetime.UTC),
            )
        )
    await db_session.commit()


async def _stored(db_session: AsyncSession) -> list[tuple[int, str, datetime.date, str]]:
    return [
        (row.cell_id, row.event_type, row.horizon_start, row.severity)
        for row in (
            await db_session.execute(
                text(
                    "SELECT cell_id, event_type, horizon_start, severity FROM risk_prediction "
                    "ORDER BY cell_id, event_type"
                )
            )
        ).all()
    ]


async def test_the_daily_run_predicts_every_cell_a_plot_points_at(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """docs/06-diseno-detallado.md §8: features per active cell, one prediction per
    event. A cell no plot falls into is not active and gets no provider call."""
    _use_doubles(monkeypatch)
    first = await _cell_with_plot(db_session, lat="10.9", lon="-74.1")
    second = await _cell_with_plot(db_session, lat="11.9", lon="-74.2")
    await SqlAlchemyWeatherRepository(db_session).get_or_create_cell(
        Decimal("9.9"), Decimal("-75.1")
    )
    await _register_versions(db_session, "risk_flood", "risk_drought")

    await predict_active_cells(timestamp=0, day=_DAY.isoformat())

    # `0.82` clears `{high: 0.7}` but not `{critical: 0.85}` (docs/08-ml.md §M2
    # "Severidad"), and the read is ordered by event, so `drought` comes first.
    assert await _stored(db_session) == [
        (first, "drought", datetime.date(2026, 10, 1), "low"),
        (first, "flood", datetime.date(2026, 10, 1), "high"),
        (second, "drought", datetime.date(2026, 10, 1), "low"),
        (second, "flood", datetime.date(2026, 10, 1), "high"),
    ]


async def test_the_run_predicts_the_month_of_the_day_it_is_given(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The month is the day's, and `horizon_days` its length (docs/08-ml.md §M2
    "Horizonte"; D-T0.3): a run named for another day of M writes M."""
    _use_doubles(monkeypatch)
    cell_id = await _cell_with_plot(db_session, lat="10.9", lon="-74.1")
    await _register_versions(db_session, "risk_flood")

    await predict_active_cells(timestamp=0, day=datetime.date(2026, 11, 20).isoformat())

    assert await _stored(db_session) == [(cell_id, "flood", datetime.date(2026, 11, 1), "high")]


async def test_a_second_run_of_the_same_month_writes_no_second_prediction(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La predicción de una celda, evento y mes se escribe una vez; las corridas
    siguientes del mismo mes no la repiten (docs/06-diseno-detallado.md §8)."""
    _use_doubles(monkeypatch)
    await _cell_with_plot(db_session, lat="10.9", lon="-74.1")
    await _register_versions(db_session, "risk_flood")

    await predict_active_cells(timestamp=0, day=_DAY.isoformat())
    await predict_active_cells(timestamp=0, day=_DAY.isoformat())

    assert len(await _stored(db_session)) == 1


async def test_a_run_with_no_registered_predictor_stores_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a predictor there is no probability to store, and the job finishes
    normally instead of failing (docs/06-diseno-detallado.md §8 "Sin modelo
    promovido")."""
    _use_doubles(monkeypatch, with_predictors=False)
    await _cell_with_plot(db_session, lat="10.9", lon="-74.1")
    await _register_versions(db_session, "risk_flood")

    await predict_active_cells(timestamp=0, day=_DAY.isoformat())

    assert await _stored(db_session) == []


async def test_a_run_without_a_configured_cell_reader_fails_loudly(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The composition seam is not optional: a worker that never called
    `configure_weather_cells` must raise instead of running a job that predicts
    nothing and looks like a month without risk (docs/05-arquitectura.md §Solo la
    fachada pública)."""
    monkeypatch.setattr(jobs_module, "_weather_cells", None)
    await _cell_with_plot(db_session, lat="10.9", lon="-74.1")
    await _register_versions(db_session, "risk_flood")

    with pytest.raises(RuntimeError, match="configure_weather_cells"):
        await predict_active_cells(timestamp=0, day=_DAY.isoformat())

    assert await _stored(db_session) == []


async def test_the_worker_runs_the_risk_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """A task on a queue this worker does not listen to never runs, and every test
    that calls the coroutine directly would stay green (docs/10-dag.md §3, ADR-0012)."""
    import techcamp.worker as worker

    listened: list[str] = []
    monkeypatch.setattr(
        worker.app, "run_worker", lambda **kwargs: listened.extend(kwargs["queues"])
    )

    worker.main()

    assert QUEUE_NAME in listened


async def test_the_daily_run_is_scheduled_at_six_in_the_morning(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """docs/06-diseno-detallado.md §8 and docs/10-dag.md §3: the risk inference runs
    every day at 06:00, on the queue this worker listens to. The cron is read in
    the worker's own local time, which is America/Bogota (docs/10-dag.md §3)."""
    import techcamp.worker  # noqa: F401  registers the periodic schedules

    scheduled = {
        task_name: entry
        for (task_name, _periodic_id), entry in jobs_app.periodic_registry.periodic_tasks.items()
    }

    assert scheduled[RUN_ACTIVE_CELLS_TASK_NAME].cron == "0 6 * * *"
    assert scheduled[RUN_ACTIVE_CELLS_TASK_NAME].configure_kwargs.get("queue") == QUEUE_NAME


async def test_the_run_defaults_to_the_bogota_day(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`day` is optional in the job too: the 06:00 cron hands over only
    `timestamp`, so the run has to resolve the local day itself."""
    _use_doubles(monkeypatch)
    cell_id = await _cell_with_plot(db_session, lat="10.9", lon="-74.1")
    await _register_versions(db_session, "risk_flood")

    await predict_active_cells(timestamp=0)

    assert await _stored(db_session) == [(cell_id, "flood", local_today().replace(day=1), "high")]


def _client() -> TestClient:
    return TestClient(app, base_url="http://testserver/api/v1")


def _registered_paths(application: object) -> set[str]:
    return set(application.openapi()["paths"])  # type: ignore[union-attr]


async def _queued_jobs(db_session: AsyncSession) -> dict[int, dict[str, object]]:
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
                {"t": RUN_ACTIVE_CELLS_TASK_NAME},
            )
        ).all()
    }


async def test_the_dev_route_returns_a_job_id_and_defaults_the_day_to_today(
    db_session: AsyncSession,
) -> None:
    """`POST /dev/jobs/risk:run { day? }` → `{ job_id }` (docs/04-api.md §Solo perfil
    seminario), with the day defaulting to today."""
    response = _client().post(_ROUTE, json={})

    assert response.request.url.path == _REGISTERED_PATH
    assert response.status_code == 200
    queued = await _queued_jobs(db_session)
    assert response.json()["job_id"] in queued
    assert queued[response.json()["job_id"]] == {
        "task_name": RUN_ACTIVE_CELLS_TASK_NAME,
        "queue_name": QUEUE_NAME,
        "status": "todo",
        "args": {"day": local_today().isoformat(), "timestamp": 0},
    }


async def test_the_dev_route_accepts_the_day_it_is_given(db_session: AsyncSession) -> None:
    day = _DAY
    response = _client().post(_ROUTE, json={"day": day.isoformat()})

    assert response.status_code == 200
    queued = await _queued_jobs(db_session)
    assert queued[response.json()["job_id"]]["args"]["day"] == day.isoformat()


async def test_the_dev_route_rejects_a_future_day_with_422(db_session: AsyncSession) -> None:
    """The month of a day that has not happened is not a month to predict: the
    horizon reads data through the last day of M-1 (docs/08-ml.md §M2 "Horizonte"),
    and a month whose window does not exist cannot have a prediction."""
    tomorrow = local_today() + datetime.timedelta(days=1)
    response = _client().post(_ROUTE, json={"day": tomorrow.isoformat()})

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert await _queued_jobs(db_session) == {}


async def test_the_dev_route_rejects_an_unparseable_day_with_422(
    db_session: AsyncSession,
) -> None:
    """A `date` field, so a malformed value is FastAPI's own 422 and not a
    `fromisoformat` failure inside the job."""
    response = _client().post(_ROUTE, json={"day": "manana"})

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/json"
    assert await _queued_jobs(db_session) == {}


async def test_the_dev_route_is_absent_in_the_production_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0021: `/dev` routes exist only in the seminar profile."""
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
