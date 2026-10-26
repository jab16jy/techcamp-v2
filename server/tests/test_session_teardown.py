"""The test session must not drop a schema the session does not own (#341).

The session fixture of `tests/conftest.py` drops the schema it migrated when
the session ends. This worktree's seminar stack runs on the same Postgres
(ADR-0021), so an unmarked session that drops the schema takes the live demo
down with it: the worker dies on a missing table and the next scenario needs
a manual `alembic upgrade head`.
"""

from __future__ import annotations

import asyncio

from session_teardown import (
    TEST_DATABASE_ENV,
    drop_the_schema,
    owns_the_database,
    schema_config,
)
from sqlalchemy import text

from techcamp.shared.db import engine


def _the_schema_is_still_there(table: str) -> bool:
    async def _query() -> bool:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT to_regclass(:name) IS NOT NULL"),
                {"name": f"public.{table}"},
            )
            return bool(result.scalar())

    return asyncio.run(_query())


def test_the_marker_says_whether_the_session_owns_the_database(monkeypatch) -> None:
    monkeypatch.delenv(TEST_DATABASE_ENV, raising=False)
    assert owns_the_database() is False

    monkeypatch.setenv(TEST_DATABASE_ENV, "1")
    assert owns_the_database() is True

    # Anything else is not the marker's yes.
    monkeypatch.setenv(TEST_DATABASE_ENV, "0")
    assert owns_the_database() is False


def test_the_teardown_drops_the_schema_only_when_the_session_owns_it(monkeypatch) -> None:
    dropped: list[str] = []
    monkeypatch.setattr(
        "alembic.command.downgrade", lambda config, revision: dropped.append(revision)
    )

    monkeypatch.delenv(TEST_DATABASE_ENV, raising=False)
    drop_the_schema(schema_config())
    assert dropped == []

    monkeypatch.setenv(TEST_DATABASE_ENV, "1")
    drop_the_schema(schema_config())
    assert dropped == ["base"]


def test_a_schema_the_session_does_not_own_survives_the_teardown(monkeypatch) -> None:
    """The real database, the real teardown: the schema stays where it is."""
    monkeypatch.delenv(TEST_DATABASE_ENV, raising=False)

    drop_the_schema(schema_config())

    assert _the_schema_is_still_there("organization") is True
