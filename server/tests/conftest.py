"""Shared test fixtures. Behavior tests run against real Postgres (no SQLite double)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.seed import seed_factory_rules
from techcamp.shared.db import async_session_factory, engine

SERVER_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session", autouse=True)
def _migrated_schema() -> None:
    """Apply the real Alembic migration once per test session; drop it after."""
    config = Config(str(SERVER_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(SERVER_DIR / "migrations"))
    command.upgrade(config, "head")
    _set_our_background_jobs(scheduled=False)
    yield
    # No re-arming before the downgrade (#89): `alter_job(scheduled => true)` keeps
    # the old `next_start`, so every policy that fell due during the session would
    # fire the moment the launcher saw it, racing the `DROP INDEX` and
    # `DROP MATERIALIZED VIEW` below. The downgrade drops the jobs with their
    # hypertables, so there is nothing to restore.
    command.downgrade(config, "base")


def _set_our_background_jobs(*, scheduled: bool) -> None:
    """Unschedule TimescaleDB's compression and continuous-aggregate
    policies for the whole test session.

    Only the session start calls this: a `scheduled=True` before the downgrade
    would re-arm the jobs while it runs (#89).

    They share a lock domain with the rows a test writes and with the
    `CALL refresh_continuous_aggregate(..., NULL, NULL)` a test runs by hand:
    when a policy fires mid-test, whichever side loses the race fails with
    `55P03 lock_not_available` — the nondeterminism behind #63, which
    `timescaledb_information.job_errors` recorded for `reading_hourly`. Tests
    drive the aggregates themselves, so no policy may run underneath them.

    Only jobs with a hypertable in our schema: TimescaleDB's internal jobs
    (telemetry reporting, job history retention) own none, do not touch the
    hypertables, and are left running.
    """
    # The asyncpg dialect has no sync DBAPI, so `alter_job` runs through the
    # async engine's own greenlet bridge: this fixture must stay sync because
    # Alembic drives the migration from sync code.
    asyncio.run(_alter_our_jobs(scheduled=scheduled))


async def _alter_our_jobs(*, scheduled: bool) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "SELECT alter_job(job_id, scheduled => :scheduled) "
                "FROM timescaledb_information.jobs "
                "WHERE hypertable_schema = 'public'"
            ),
            {"scheduled": scheduled},
        )


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
    async with engine.begin() as conn:
        # `weather_cell`/`weather_daily` are in the truncate because they carry
        # no `org_id`: the `organization ... CASCADE` alone never reaches them,
        # and E5 writes a cell every time a plot is created, so they would
        # otherwise leak from one test into the next. `procrastinate_jobs` for
        # the same reason: creating a plot on a cold cell defers a forecast
        # fetch (farms `get_or_create_cell`), and a test that counts deferred
        # jobs would otherwise see the previous test's.
        await conn.execute(
            text(
                "TRUNCATE membership, app_user, organization, weather_daily, weather_cell, "
                "irrigation_recommendation, water_balance_daily, procrastinate_jobs "
                "RESTART IDENTITY CASCADE"
            )
        )
        # TRUNCATE organization CASCADE wipes `alert_rule` entirely because of
        # `alert_rule.org_id FK organization`. Re-seed the factory rules so
        # subsequent tests find them intact.
        await seed_factory_rules(conn)
