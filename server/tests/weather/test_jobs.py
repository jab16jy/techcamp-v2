"""Weather forecast refresh jobs (T5a, docs/06-diseno-detallado.md §6,
docs/10-dag.md §3, docs/09-cuellos-de-botella.md:29, ADR-0012).

The 3 h run fans one job out per active cell, each job refreshes that cell's
16-day forecast, and a provider outage leaves the stored rows exactly as they
were, served as `stale` with their own `fetched_at`. Open-Meteo is the only
test double in the chain: an injected `httpx.MockTransport` (ADR-0021).
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
import pytest
from procrastinate.jobs import Job
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import FarmRow
from techcamp.farms.adapters.repositories import SqlAlchemyPlotRepository
from techcamp.farms.domain.models import IrrigationSystem
from techcamp.identity.adapters.orm import OrganizationRow
from techcamp.shared.ids import uuid7
from techcamp.shared.jobs import app
from techcamp.weather.adapters import jobs as jobs_module
from techcamp.weather.adapters.jobs import (
    FORECAST_DAYS,
    QUEUE_NAME,
    REFRESH_TASK_NAME,
    refresh_active_cells,
    refresh_cell,
)
from techcamp.weather.adapters.open_meteo import OpenMeteoAdapter, OpenMeteoUnavailableError
from techcamp.weather.adapters.repositories import SqlAlchemyWeatherRepository
from techcamp.weather.domain.models import WeatherDay

pytestmark = pytest.mark.anyio

_FARM_POINT = "SRID=4326;POINT(-74.1 10.9)"
# Centroid (-74.09, 10.91): the 0.1° cell (-74.1, 10.9).
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.92, -74.08 10.92, -74.08 10.90, -74.10 10.90))"
)
# Centroid (-74.19, 10.91): the cell (-74.2, 10.9).
_OTHER_CELL_BOUNDARY = (
    "SRID=4326;POLYGON((-74.20 10.90, -74.20 10.92, -74.18 10.92, -74.18 10.90, -74.20 10.90))"
)
_FIRST_DAY = datetime.date(2026, 9, 26)
# An arbitrary instant in the past, so "the refresh moved `fetched_at` forward"
# is decided by this run's clock rather than by the day the suite happens to run.
_STORED_AT = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)


@pytest.fixture(autouse=True)
async def _clear_jobs(db_session: AsyncSession):
    """`procrastinate_jobs` is keyed by cell id and `db_session`'s own cleanup
    truncates only the organization tables, so a `todo` job left by one test
    would make the next test's per-cell defer a no-op (the `queueing_lock`
    refuses a duplicate) and hide a broken fan-out."""
    yield
    await db_session.execute(text("DELETE FROM procrastinate_jobs"))
    await db_session.commit()


def _forecast_body(days: int) -> dict[str, Any]:
    """`days` daily rows, as Open-Meteo answers a daily-variables query."""
    return {
        "latitude": 10.9,
        "longitude": -74.1,
        "utc_offset_seconds": -18000,
        "timezone": "America/Bogota",
        "daily": {
            "time": [(_FIRST_DAY + datetime.timedelta(days=i)).isoformat() for i in range(days)],
            "et0_fao_evapotranspiration": [4.0 + i for i in range(days)],
            "precipitation_sum": [0.0] * days,
            "temperature_2m_min": [24.0] * days,
            "temperature_2m_max": [33.0] * days,
            "relative_humidity_2m_mean": [78.0] * days,
        },
        "daily_units": {},
    }


def _adapter_responding(handler: Any) -> OpenMeteoAdapter:
    """A real adapter whose only test double is the transport: `max_retries=0`
    so a failing response is the failure under test, not three backoffs."""
    return OpenMeteoAdapter(
        transport=httpx.MockTransport(handler),
        base_url="https://api.open-meteo.com/v1",
        max_retries=0,
    )


def _use_adapter(monkeypatch: pytest.MonkeyPatch, adapter: OpenMeteoAdapter) -> None:
    """Put `adapter` in the place the worker resolves it from, so the task body
    under test is the one that runs in production."""
    monkeypatch.setattr(jobs_module, "_adapter", lambda: adapter)


async def _make_org_and_farm(db_session: AsyncSession) -> tuple[UUID, UUID]:
    org_id = uuid7()
    db_session.add(OrganizationRow(id=org_id, name="Finca", kind="individual"))
    await db_session.commit()
    farm_id = uuid7()
    db_session.add(
        FarmRow(
            id=farm_id,
            org_id=org_id,
            name="Finca A",
            municipality_code="47001",
            location=_FARM_POINT,
        )
    )
    await db_session.commit()
    return org_id, farm_id


async def _create_plot(
    db_session: AsyncSession, boundary: str = _BOUNDARY, *, name: str = "Lote 1"
) -> int:
    org_id, farm_id = await _make_org_and_farm(db_session)
    plot = await SqlAlchemyPlotRepository(db_session).create(
        plot_id=uuid7(),
        org_id=org_id,
        farm_id=farm_id,
        name=name,
        boundary_wkt=boundary,
        irrigation_system=IrrigationSystem.NONE,
        irrigation_efficiency=None,
        system_flow_lph=None,
    )
    assert plot.weather_cell_id is not None
    return plot.weather_cell_id


async def _make_cell_with_data(db_session: AsyncSession) -> int:
    """A cell whose forecast has already been stored once, so it is not cold.

    Built without going through the plot write path: that path defers a refresh
    for a cold cell, which is the behavior under test elsewhere in this file.
    """
    repository = SqlAlchemyWeatherRepository(db_session)
    cell_id = await repository.get_or_create_cell(Decimal("10.9"), Decimal("-74.1"))
    await repository.upsert_daily(
        cell_id,
        [
            WeatherDay(
                day=_FIRST_DAY,
                is_forecast=True,
                et0_mm=4.0,
                rain_mm=0.0,
                tmin_c=24.0,
                tmax_c=33.0,
                rh_mean_pct=78.0,
                fetched_at=_STORED_AT,
            )
        ],
    )
    return cell_id


async def _stored_days(
    db_session: AsyncSession, cell_id: int
) -> list[tuple[datetime.date, bool, float | None, datetime.datetime]]:
    rows = (
        await db_session.execute(
            text(
                "SELECT day, is_forecast, et0_mm, fetched_at FROM weather_daily "
                "WHERE cell_id = :c ORDER BY day, is_forecast"
            ),
            {"c": cell_id},
        )
    ).all()
    return [
        (r.day, r.is_forecast, float(r.et0_mm) if r.et0_mm is not None else None, r.fetched_at)
        for r in rows
    ]


async def _refresh_jobs(db_session: AsyncSession) -> list[Any]:
    return (
        await db_session.execute(
            text(
                "SELECT args, lock, queueing_lock, status, queue_name, task_name "
                "FROM procrastinate_jobs WHERE task_name = :t ORDER BY id"
            ),
            {"t": REFRESH_TASK_NAME},
        )
    ).all()


async def test_the_three_hourly_run_defers_one_job_per_active_cell(
    db_session: AsyncSession,
) -> None:
    """docs/06-diseno-detallado.md §6: the 3 h run refreshes the forecast of the
    *active* cells, and docs/09-cuellos-de-botella.md:29 has each cell's job
    separate so the worker runs them in parallel. A cell no plot falls into is
    not active and must not cost a provider call."""
    first = await _create_plot(db_session)
    second = await _create_plot(db_session, _OTHER_CELL_BOUNDARY, name="Lote 2")
    cells = SqlAlchemyWeatherRepository(db_session)
    await cells.get_or_create_cell(Decimal("11.0"), Decimal("-75.0"))  # no plot points here

    await refresh_active_cells(timestamp=0)

    jobs = await _refresh_jobs(db_session)
    assert {job.args["cell_id"] for job in jobs} == {first, second}
    assert all(job.queue_name == QUEUE_NAME for job in jobs)
    assert all(job.status == "todo" for job in jobs)
    # One cell never refreshes twice at once: the per-cell lock serializes its
    # jobs and the queueing lock refuses a duplicate while one is still `todo`.
    assert {job.lock for job in jobs} == {f"refresh:cell:{first}", f"refresh:cell:{second}"}
    assert {job.queueing_lock for job in jobs} == {job.lock for job in jobs}


async def test_a_cell_that_already_has_a_job_waiting_is_skipped_not_fatal(
    db_session: AsyncSession,
) -> None:
    """The 3 h run and a plot's cold start can land on the same cell, and the
    fan-out's own retry re-reads every cell. procrastinate's SQL defer function
    lets the `queueing_lock` unique violation out instead of swallowing it the
    way `Task.defer` does, which inside the caller's transaction would take the
    whole run down — every other cell of the run would lose its job — so the
    deferral has to roll back to a savepoint and move on."""
    cell_id = await _create_plot(db_session)  # its cold start queued one already
    other = await _create_plot(db_session, _OTHER_CELL_BOUNDARY, name="Lote 2")

    await refresh_active_cells(timestamp=0)

    jobs = await _refresh_jobs(db_session)
    # The cold start's job survives, and the second cell is still queued.
    assert {(job.args["cell_id"], job.status) for job in jobs} == {
        (cell_id, "todo"),
        (other, "todo"),
    }


async def test_a_cell_refresh_stores_16_forecast_days(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """docs/06-diseno-detallado.md §6 and the feature doc: the refresh writes the
    whole forecast window Open-Meteo offers (16 days, none of them past), each
    row flagged `is_forecast` and stamped with the time of this fetch — the
    clock the `stale` rule reads."""
    cell_id = await _create_plot(db_session)
    requests: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_forecast_body(FORECAST_DAYS))

    _use_adapter(monkeypatch, _adapter_responding(_handler))
    before_this_run = datetime.datetime.now(datetime.UTC)

    await refresh_cell(cell_id=cell_id)

    assert len(requests) == 1
    assert requests[0].url.params["past_days"] == "0"
    assert requests[0].url.params["forecast_days"] == str(FORECAST_DAYS)

    stored = await _stored_days(db_session, cell_id)
    assert [day for day, _, _, _ in stored] == [
        _FIRST_DAY + datetime.timedelta(days=i) for i in range(FORECAST_DAYS)
    ]
    assert all(is_forecast for _, is_forecast, _, _ in stored)
    assert [et0 for _, _, et0, _ in stored] == [4.0 + i for i in range(FORECAST_DAYS)]
    fetched_at = {fetched_at for _, _, _, fetched_at in stored}
    assert len(fetched_at) == 1
    assert min(fetched_at) >= before_this_run


async def test_a_second_refresh_overwrites_the_forecast_window(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 3 h run rewrites the same `(cell_id, day, is_forecast)` keys over and
    over, so a re-run must move the stored values and their `fetched_at` forward
    instead of piling up a second row per day."""
    cell_id = await _make_cell_with_data(db_session)
    moved = [_FIRST_DAY + datetime.timedelta(days=i) for i in range(FORECAST_DAYS)]
    body = _forecast_body(FORECAST_DAYS)
    body["daily"]["time"] = [day.isoformat() for day in moved]
    body["daily"]["et0_fao_evapotranspiration"] = [9.0] * FORECAST_DAYS
    _use_adapter(monkeypatch, _adapter_responding(lambda _request: httpx.Response(200, json=body)))
    before_this_run = datetime.datetime.now(datetime.UTC)

    await refresh_cell(cell_id=cell_id)

    stored = await _stored_days(db_session, cell_id)
    assert len(stored) == FORECAST_DAYS
    assert all(et0 == 9.0 for _, _, et0, _ in stored)
    assert all(fetched_at >= before_this_run for _, _, _, fetched_at in stored)


async def test_an_open_meteo_outage_keeps_the_previous_rows_and_their_fetched_at(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """docs/06-diseno-detallado.md §6 degradation: when the provider is down the
    cell keeps the last stored data, served later as `stale` with the hour it
    was actually fetched. So a failed refresh writes nothing at all, and the
    task records no retry: the adapter already retried inside (T3b) and the next
    3 h run is the real backstop, so a retry here would only add load to an
    outage."""
    cell_id = await _make_cell_with_data(db_session)
    before = await _stored_days(db_session, cell_id)

    _use_adapter(
        monkeypatch,
        _adapter_responding(lambda _request: httpx.Response(503, json={"reason": "down"})),
    )

    await refresh_cell(cell_id=cell_id)

    assert await _stored_days(db_session, cell_id) == before

    job = Job(
        id=1,
        status="todo",
        queue=QUEUE_NAME,
        priority=0,
        lock=None,
        queueing_lock=None,
        task_name=REFRESH_TASK_NAME,
        task_kwargs={},
        scheduled_at=None,
        attempts=0,
        abort_requested=False,
        worker_id=None,
    )
    task = app.tasks[REFRESH_TASK_NAME]
    assert (
        task.get_retry_exception(
            exception=OpenMeteoUnavailableError("Open-Meteo is down", upstream_status=503),
            job=job,
        )
        is None
    )


async def test_creating_a_plot_defers_a_refresh_for_a_cell_that_has_no_data(
    db_session: AsyncSession,
) -> None:
    """A plot whose cell has never been fetched would serve an empty forecast
    until the next 3 h run, so creating (or moving onto) a cell defers a one-off
    refresh for it. It rides the plot's own transaction, ADR-0012's "los jobs se
    encolan en la misma transacción que los datos que los originan"."""
    cell_id = await _create_plot(db_session)

    jobs = await _refresh_jobs(db_session)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.args == {"cell_id": cell_id}
    assert job.queueing_lock == f"refresh:cell:{cell_id}"
    assert job.status == "todo"
    # The cell row is committed before the plot, so the worker can already
    # resolve the coordinates the deferred job needs.
    coordinates = (
        await db_session.execute(
            text("SELECT lat, lon FROM weather_cell WHERE id = :c"), {"c": cell_id}
        )
    ).one()
    assert coordinates == (Decimal("10.9"), Decimal("-74.1"))


async def test_a_plot_on_a_cell_that_already_has_data_defers_nothing(
    db_session: AsyncSession,
) -> None:
    """Only a cold cell needs the one-off fetch: a second plot on a cell that was
    already refreshed must not spend another provider call, or every rename of
    every plot would."""
    await _make_cell_with_data(db_session)

    org_id, farm_id = await _make_org_and_farm(db_session)
    await SqlAlchemyPlotRepository(db_session).create(
        plot_id=uuid7(),
        org_id=org_id,
        farm_id=farm_id,
        name="Lote 2",
        boundary_wkt=_BOUNDARY,
        irrigation_system=IrrigationSystem.NONE,
        irrigation_efficiency=None,
        system_flow_lph=None,
    )

    assert await _refresh_jobs(db_session) == []


async def test_a_refresh_of_a_deleted_cell_does_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cell can go away between the fan-out and the worker picking the job
    up; there is nothing left to refresh, and the job must not ask the provider
    about coordinates it never had."""
    calls: list[tuple[float, float]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        calls.append((float(params["latitude"]), float(params["longitude"])))
        return httpx.Response(200, json=_forecast_body(FORECAST_DAYS))

    _use_adapter(monkeypatch, _adapter_responding(_handler))

    cells = SqlAlchemyWeatherRepository(db_session)
    await cells.get_or_create_cell(Decimal("10.9"), Decimal("-74.1"))
    await refresh_cell(cell_id=999_999)

    assert calls == []
