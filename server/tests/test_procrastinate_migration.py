"""The procrastinate schema migration applies procrastinate's own SQL
statement by statement (`migrations/versions/231a40930eb5_add_procrastinate_schema.py`).

#39: `_split_sql_statements` tracked only bare `$$` spans, and nothing tested it,
so a procrastinate release whose schema used a tagged dollar quote or a `;`
inside a literal or a comment would break the upgrade at apply time — a
migration that only fails once a dependency is bumped is a latent outage.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from procrastinate.schema import SchemaManager
from sqlalchemy import text

from techcamp.shared.db import engine

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "versions"
    / "231a40930eb5_add_procrastinate_schema.py"
)


def _migration() -> ModuleType:
    """The migration module, loaded by path: `migrations/` is not a package
    (Alembic loads these files itself, never as an import)."""
    spec = importlib.util.spec_from_file_location("procrastinate_schema_migration", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _split(sql: str) -> list[str]:
    return _migration()._split_sql_statements(sql)


def test_a_tagged_dollar_quote_is_not_split() -> None:
    """`$body$ ... $body$` is one literal, exactly like `$$ ... $$`, but a
    splitter that only toggles on the bare tag reads its closing tag as the
    opening one and splits every `;` inside the function body."""
    body = "CREATE FUNCTION f() RETURNS void AS $body$ BEGIN; END $body$ LANGUAGE plpgsql"
    assert _split(f"{body};") == [body]


def test_a_semicolon_inside_a_literal_is_not_a_separator() -> None:
    assert _split("SELECT 'a;b';") == ["SELECT 'a;b'"]
    # `''` escapes a quote inside the literal, so the literal does not end there.
    assert _split("SELECT 'it''s; fine';") == ["SELECT 'it''s; fine'"]


def test_a_semicolon_inside_a_line_comment_is_not_a_separator() -> None:
    assert _split("-- a; b\nSELECT 1;") == ["-- a; b\nSELECT 1"]


def test_the_real_schema_splits_losslessly() -> None:
    """The installed version's schema, the exact input the migration applies.
    Rejoining the statements has to reproduce it: a splitter that dropped or
    duplicated a fragment would still hand Alembic a plausible-looking list of
    statements."""
    schema = SchemaManager.get_schema()
    statements = _split(schema)

    assert statements
    rejoined = "; ".join(statements) + ";"
    assert " ".join(rejoined.split()) == " ".join(schema.split())


@pytest.mark.anyio
async def test_every_statement_of_the_real_schema_applies_on_its_own() -> None:
    """What the migration actually does, against a real Postgres: asyncpg
    prepares each `op.execute()` as a single statement and refuses a string
    holding more than one command, so every statement the splitter returns has
    to stand alone. Applied inside a rolled-back transaction in a throwaway
    schema, so it neither collides with the real procrastinate tables nor
    leaves anything behind.

    It also asserts the two names `enqueue_recalibration` hard-codes into its
    `SELECT procrastinate_defer_jobs_v1(...)` call still exist, so a version
    bump that renamed them fails here instead of at enqueue time.
    """
    statements = _split(SchemaManager.get_schema())

    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            await conn.execute(text("CREATE SCHEMA procrastinate_split_probe"))
            await conn.execute(text("SET LOCAL search_path = procrastinate_split_probe"))
            for statement in statements:
                await conn.execute(text(statement))

            tables = (
                (
                    await conn.execute(
                        text(
                            "SELECT tablename FROM pg_tables "
                            "WHERE schemaname = 'procrastinate_split_probe' ORDER BY tablename"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert tables == [
                "procrastinate_events",
                "procrastinate_jobs",
                "procrastinate_periodic_defers",
                "procrastinate_workers",
            ]

            deferred_function = await conn.execute(
                text(
                    "SELECT count(*) FROM pg_proc p "
                    "JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'procrastinate_split_probe' "
                    "AND p.proname = 'procrastinate_defer_jobs_v1'"
                )
            )
            assert deferred_function.scalar_one() == 1

            deferred_type = await conn.execute(
                text(
                    "SELECT count(*) FROM pg_type t "
                    "JOIN pg_namespace n ON n.oid = t.typnamespace "
                    "WHERE n.nspname = 'procrastinate_split_probe' "
                    "AND t.typname = 'procrastinate_job_to_defer_v1'"
                )
            )
            assert deferred_type.scalar_one() == 1
        finally:
            # Explicit rollback: `conn.begin()` commits on a clean exit, and the
            # probe must not outlive the test (a leftover schema would fail the
            # next run's CREATE).
            await transaction.rollback()


def test_the_splitter_ignores_whitespace_only_fragments() -> None:
    assert _split("  \n ; \n SELECT 1 ;\n") == ["SELECT 1"]


def test_a_statement_without_a_trailing_semicolon_is_kept() -> None:
    assert _split("SELECT 1;\nSELECT 2") == ["SELECT 1", "SELECT 2"]
