"""Shared test fixtures. Behavior tests run against real Postgres (no SQLite double)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

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
    yield
    command.downgrade(config, "base")


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE membership, app_user, organization RESTART IDENTITY CASCADE")
        )
