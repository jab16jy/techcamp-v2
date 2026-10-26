"""The rule that says when a test session may drop the schema it ran on (#341).

The session fixture of `tests/conftest.py` migrates the database to head when
the session starts and drops it when the session ends. That drop takes the
schema of everything else that shares the database with it, and this worktree's
seminar stack runs on the same Postgres (ADR-0021): the worker dies on a
missing table and the next scenario needs a manual `alembic upgrade head`.

The rule is a marker, not a comparison: a session drops the schema only when
the recipe that started it says the database is its own (`TECHCAMP_TEST_DB`).
The gate recipes export it and point the tests at a database of their own; a
session run against the seminar database (a direct `uv run pytest`) leaves the
schema where it is.
"""

from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config

SERVER_DIR = Path(__file__).resolve().parent.parent
"""The server project: `alembic.ini` and `migrations/` live there."""

TEST_DATABASE_ENV = "TECHCAMP_TEST_DB"
"""The marker that says "this session owns the database it runs against"."""


def owns_the_database() -> bool:
    """Whether this session may drop the schema it runs against."""
    return os.environ.get(TEST_DATABASE_ENV, "").strip() == "1"


def schema_config() -> Config:
    """The Alembic configuration of the server project's migrations."""
    config = Config(str(SERVER_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(SERVER_DIR / "migrations"))
    return config


def drop_the_schema(config: Config) -> None:
    """Drop the schema, but only if this session owns the database."""
    if not owns_the_database():
        return
    # No re-arming before the downgrade (#89): `alter_job(scheduled => true)` keeps
    # the old `next_start`, so every policy that fell due during the session would
    # fire the moment the launcher saw it, racing the `DROP INDEX` and
    # `DROP MATERIALIZED VIEW` below. The downgrade drops the jobs with their
    # hypertables, so there is nothing to restore.
    command.downgrade(config, "base")
