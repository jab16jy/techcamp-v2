"""Tests for the monthly metrics job (E11 T6, docs/10-dag.md:178-180, docs/04:261).

ADR-0012, ADR-0021. Real Postgres, real queues:
- The run is a day-one 02:00 periodic in the `metrics` queue (D-T0.7).
- It fans a summary job out per **closed** cycle and then an index job out per plot
  for the month before `day`, in that order.
- Both halves deduplicate on a second run of the same month, and a new month is
  never swallowed by the previous month's queueing lock.
- Each fan-out job stores its own row, and one item failing leaves the month
  standing for the others.

Every assertion is scoped to the test's own ids: the fan-out reads the whole
table set, so another test's plots and cycles are in it too (the same
isolation lesson as `tests/irrigation/test_jobs.py`).
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

import pytest
from home.conftest import add_plot, make_env
from metrics.conftest import add_cycle
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.domain.errors import PlotNotFoundError
from techcamp.metrics.adapters.cycle_summary_repository import SqlAlchemyCycleSummaryRepository
from techcamp.metrics.adapters.jobs import (
    COMPUTE_PLOT_MONTH_TASK_NAME,
    QUEUE_NAME,
    RUN_MONTHLY_METRICS_TASK_NAME,
    SUMMARIZE_CLOSED_CYCLE_TASK_NAME,
    _defer_item,
    compute_plot_month_index,
    previous_month,
    run_monthly_metrics,
    summarize_closed_cycle,
)
from techcamp.metrics.adapters.monthly_repository import SqlAlchemyMonthlyMetricRepository
from techcamp.shared.ids import uuid7
from techcamp.shared.jobs import app as jobs_app

pytestmark = pytest.mark.anyio

SEPTEMBER = date(2026, 9, 1)
"""The month the index jobs of a run on 2026-10-01 are asked for (D-T0.7)."""


@pytest.fixture(autouse=True)
async def _clear_jobs(db_session: AsyncSession):
    """The queue is shared state across tests; leave it empty behind each one."""
    yield
    await db_session.execute(text("DELETE FROM procrastinate_jobs"))
    await db_session.commit()


async def _jobs(db_session: AsyncSession, *, task_name: str) -> list[dict[str, object]]:
    result = await db_session.execute(
        text(
            "SELECT id, queue_name, task_name, lock, queueing_lock, args, status "
            "FROM procrastinate_jobs WHERE task_name = :t ORDER BY id"
        ),
        {"t": task_name},
    )
    return [dict(row) for row in result.mappings().all()]


def _mine(jobs: list[dict[str, object]], *, key: str, values: set[UUID]) -> list[dict[str, object]]:
    return [job for job in jobs if UUID(str(job["args"][key])) in values]


async def test_the_monthly_run_is_a_day_one_two_am_periodic_in_the_metrics_queue() -> None:
    """docs/10-dag.md:178 `subgraph mensual [Mensual: día 1, 02:00]`: the run happens
    on day one at 02:00, in the worker's local time (America/Bogota).
    """
    periodic = jobs_app.periodic_registry.periodic_tasks
    entry = periodic[(RUN_MONTHLY_METRICS_TASK_NAME, "")]
    assert entry.cron == "0 2 1 * *"
    assert entry.configure_kwargs["queue"] == QUEUE_NAME


async def test_the_indexed_month_is_the_one_before_the_run_day() -> None:
    """D-T0.7: the month indexed is the one **before** `day`'s, and it is its first
    day (docs/03:438 `month` es el primer día del mes).
    """
    assert previous_month(date(2026, 10, 1)) == SEPTEMBER
    assert previous_month(date(2026, 10, 31)) == SEPTEMBER
    assert previous_month(date(2026, 1, 15)) == date(2025, 12, 1)


async def test_the_monthly_run_summarizes_closed_cycles_then_indexes_the_previous_month(
    db_session: AsyncSession,
) -> None:
    """docs/04:261: one job that summarizes the finished cycles and then computes the
    index of the month before `day`; docs/10-dag.md:179 keeps the two in that order.
    """
    env = await make_env(db_session)
    second_plot = await add_plot(db_session, org_id=env.org_id, farm_id=env.farm_id, name="Lote 2")
    harvested = await add_cycle(
        db_session, env, sown_on=date(2026, 4, 1), status="harvested", expected_harvest_on=None
    )
    lost = await add_cycle(
        db_session, env, sown_on=date(2026, 5, 1), status="lost", expected_harvest_on=None
    )

    await run_monthly_metrics(timestamp=0, day="2026-10-01")

    cycle_jobs = _mine(
        await _jobs(db_session, task_name=SUMMARIZE_CLOSED_CYCLE_TASK_NAME),
        key="crop_cycle_id",
        values={harvested, lost},
    )
    assert {UUID(str(job["args"]["crop_cycle_id"])) for job in cycle_jobs} == {harvested, lost}
    for job in cycle_jobs:
        assert job["queue_name"] == QUEUE_NAME
        assert job["status"] == "todo"
        assert job["args"]["org_id"] == str(env.org_id)
        assert job["lock"] == f"metrics:cycle:{job['args']['crop_cycle_id']}"
        assert job["queueing_lock"] == job["lock"]

    plot_jobs = _mine(
        await _jobs(db_session, task_name=COMPUTE_PLOT_MONTH_TASK_NAME),
        key="plot_id",
        values={env.plot_id, second_plot},
    )
    assert len(plot_jobs) == 2
    for job in plot_jobs:
        assert job["queue_name"] == QUEUE_NAME
        assert job["status"] == "todo"
        assert job["args"]["org_id"] == str(env.org_id)
        assert job["args"]["month"] == SEPTEMBER.isoformat()
        assert job["lock"] == f"metrics:plot:{job['args']['plot_id']}"
        assert job["queueing_lock"] == f"metrics:plot:{job['args']['plot_id']}:{SEPTEMBER}"

    # The cycle half is queued first, so the summaries exist before the index jobs
    # read the same logbook (docs/10-dag.md:179 `o --> p`).
    assert max(int(job["id"]) for job in cycle_jobs) < min(int(job["id"]) for job in plot_jobs)


async def test_an_active_cycle_is_not_summarized_by_the_monthly_run(
    db_session: AsyncSession,
) -> None:
    """docs/03:443 and D-T0.8: `crop_cycle_summary` holds `harvested` or `lost` cycles
    only; an active cycle's impact is computed when it is read, never stored.
    """
    env = await make_env(db_session)
    active = await add_cycle(db_session, env, sown_on=date(2026, 8, 1), status="active")

    await run_monthly_metrics(timestamp=0, day="2026-10-01")

    jobs = _mine(
        await _jobs(db_session, task_name=SUMMARIZE_CLOSED_CYCLE_TASK_NAME),
        key="crop_cycle_id",
        values={active},
    )
    assert jobs == []


async def test_a_second_run_of_the_same_month_does_not_duplicate_its_jobs(
    db_session: AsyncSession,
) -> None:
    """docs/03:442: the write is an upsert so running twice lands the same figures, and
    the queueing lock means the second run does not even queue it twice.
    """
    env = await make_env(db_session)
    cycle_id = await add_cycle(
        db_session, env, sown_on=date(2026, 4, 1), status="harvested", expected_harvest_on=None
    )

    await run_monthly_metrics(timestamp=0, day="2026-10-01")
    await run_monthly_metrics(timestamp=0, day="2026-10-01")

    cycle_jobs = _mine(
        await _jobs(db_session, task_name=SUMMARIZE_CLOSED_CYCLE_TASK_NAME),
        key="crop_cycle_id",
        values={cycle_id},
    )
    plot_jobs = _mine(
        await _jobs(db_session, task_name=COMPUTE_PLOT_MONTH_TASK_NAME),
        key="plot_id",
        values={env.plot_id},
    )
    assert len(cycle_jobs) == 1
    assert len(plot_jobs) == 1

    # A different month is a different queueing lock: October indexes August.
    await run_monthly_metrics(timestamp=0, day="2026-11-01")
    august = _mine(
        await _jobs(db_session, task_name=COMPUTE_PLOT_MONTH_TASK_NAME),
        key="plot_id",
        values={env.plot_id},
    )
    assert {str(job["args"]["month"]) for job in august} == {
        SEPTEMBER.isoformat(),
        date(2026, 10, 1).isoformat(),
    }


async def test_the_index_job_stores_its_plot_month(db_session: AsyncSession) -> None:
    """The per-plot job runs `compute_plot_month` over the real adapters and stores the
    row. A plot with no evidence at all still stores one: `record_keeping` is calendar
    math and never null, so it scores `0` ("a silent month is evidence of not
    recording", docs/11-metricas.md §2) while the three evidence-backed components stay
    null (D-T0.3), and the index over the one non-null component is `0`.
    """
    env = await make_env(db_session)

    await compute_plot_month_index(
        org_id=str(env.org_id), plot_id=str(env.plot_id), month=SEPTEMBER.isoformat()
    )

    stored = await SqlAlchemyMonthlyMetricRepository(db_session).get(
        env.org_id, env.plot_id, month=SEPTEMBER
    )
    assert stored is not None
    assert stored.month == SEPTEMBER
    assert stored.components.monitoring is None
    assert stored.components.decision is None
    assert stored.components.risk_management is None
    assert stored.components.record_keeping == 0
    assert stored.digital_adoption_index == 0


async def test_the_summary_job_stores_its_closed_cycle(db_session: AsyncSession) -> None:
    """The per-cycle job runs `summarize_cycle`, which persists a `harvested` cycle
    (docs/03:439) and leaves every metric null when the cycle has no evidence of it.
    """
    env = await make_env(db_session)
    cycle_id = await add_cycle(
        db_session, env, sown_on=date(2026, 4, 1), status="harvested", expected_harvest_on=None
    )

    await summarize_closed_cycle(org_id=str(env.org_id), crop_cycle_id=str(cycle_id))

    stored = await SqlAlchemyCycleSummaryRepository(db_session).get_for_org(cycle_id, env.org_id)
    assert stored is not None
    assert stored.plot_id == env.plot_id
    assert stored.yield_kg_ha is None
    assert stored.relative_yield is None


async def test_an_item_that_fails_does_not_take_the_month_with_it(
    db_session: AsyncSession,
) -> None:
    """Per-item containment: a plot that cannot be indexed is procrastinate's problem
    alone (it retries on its own), and every other plot of the month still stores its row.
    """
    env = await make_env(db_session)
    healthy = await add_plot(db_session, org_id=env.org_id, farm_id=env.farm_id, name="Lote 2")

    with pytest.raises(PlotNotFoundError):
        await compute_plot_month_index(
            org_id=str(env.org_id), plot_id=str(uuid7()), month=SEPTEMBER.isoformat()
        )

    await compute_plot_month_index(
        org_id=str(env.org_id), plot_id=str(healthy), month=SEPTEMBER.isoformat()
    )
    stored = await SqlAlchemyMonthlyMetricRepository(db_session).get(
        env.org_id, healthy, month=SEPTEMBER
    )
    assert stored is not None


async def test_a_duplicate_queueing_lock_is_logged_and_the_sweep_goes_on(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """The savepoint around the defer is what makes a duplicate `queueing_lock` cost the
    duplicate and nothing else: the sweep's own transaction stays usable and the next item
    still lands (E11 lessons, `irrigation/adapters/jobs.py::_defer_plot_job`).
    """
    args = {"org_id": str(uuid7()), "plot_id": str(uuid7()), "month": SEPTEMBER.isoformat()}
    lock = "metrics:plot:test"
    for _ in range(2):
        await _defer_item(
            db_session,
            task_name=COMPUTE_PLOT_MONTH_TASK_NAME,
            lock=lock,
            queueing_lock=lock,
            args=args,
        )
    await db_session.commit()

    jobs = await _jobs(db_session, task_name=COMPUTE_PLOT_MONTH_TASK_NAME)
    assert len(jobs) == 1
    assert f"metrics: job for {lock} already queued" in caplog.text
