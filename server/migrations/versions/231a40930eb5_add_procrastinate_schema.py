"""add procrastinate schema

Revision ID: 231a40930eb5
Revises: 8c3983dc2dfd
Create Date: 2026-09-25 00:00:00.000000

"""
from typing import Sequence, Union

import re

from alembic import op
from procrastinate.schema import SchemaManager

# revision identifiers, used by Alembic.
revision: str = '231a40930eb5'
down_revision: Union[str, Sequence[str], None] = '8c3983dc2dfd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DOLLAR_QUOTE = re.compile(r'\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$')
"""The opening tag of a dollar-quoted body: `$$` or `$tag$`. Postgres forbids a
`$` inside the tag, which is what keeps the closing tag unambiguous."""


def _split_sql_statements(sql: str) -> list[str]:
    """`SchemaManager.get_schema()` returns one script of `;`-separated
    statements, several of them `$$`-quoted plpgsql function bodies. asyncpg
    (this project's driver) prepares each `op.execute()` call as a single
    statement and refuses a string containing more than one command
    (`cannot insert multiple commands into a prepared statement`, confirmed
    against a real run), so each top-level statement is executed on its own.

    A `;` only ends a statement outside a literal, so this tracks the three
    places one can hide: single-quoted strings (with their `''` escape),
    dollar-quoted bodies (bare `$$` or tagged `$tag$ ... $tag$`), and `--`
    line comments. The pinned procrastinate version's own schema happens to
    use only bare `$$`, but a later version is free to `RAISE` a message
    containing a `;` or to tag its bodies, and a mis-split script fails the
    upgrade at apply time — `tests/test_procrastinate_migration.py` pins all
    three cases against the real schema."""
    statements = []
    current: list[str] = []
    # None outside a dollar-quoted body, else the tag that opened it.
    dollar_tag: str | None = None
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if dollar_tag is not None:
            if sql.startswith(dollar_tag, i):
                current.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
                continue
        elif sql.startswith("--", i):
            end = sql.find("\n", i)
            end = n if end == -1 else end
            current.append(sql[i:end])
            i = end
            continue
        elif ch == "'":
            # `''` inside the string is an escaped quote, not its end.
            end = i + 1
            while end < n:
                if sql[end] != "'":
                    end += 1
                elif sql.startswith("''", end):
                    end += 2
                else:
                    end += 1
                    break
            current.append(sql[i:end])
            i = end
            continue
        else:
            # An unquoted dollar sign opens a body: `$$` or `$tag$`, where the
            # tag is an identifier and may not contain another `$`.
            match = _DOLLAR_QUOTE.match(sql, i)
            if match is not None:
                dollar_tag = match.group(0)
                current.append(dollar_tag)
                i += len(dollar_tag)
                continue
        if ch == ";" and dollar_tag is None:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def upgrade() -> None:
    """Upgrade schema."""
    # ADR-0012: procrastinate owns its own tables/types/functions/triggers.
    # `SchemaManager.get_schema()` (verified via ctx7 against procrastinate's
    # own `schema.py`) returns the exact SQL its `procrastinate apply-schema`
    # CLI command would run, so the schema is applied as part of this
    # migration instead of a separate manual step.
    #
    # The applied SQL therefore depends on the *installed* procrastinate, so
    # `pyproject.toml` pins it exactly (`==3.10.0`) and `uv.lock` locks it: a
    # floating range would silently apply a different schema to a database
    # whose Alembic version is already recorded. Upgrading procrastinate needs
    # a new migration of its own, written against the new version's schema and
    # its own upstream migrations (procrastinate ships
    # `procrastinate_migrations`, which this migration does not track) — this
    # one only ever creates a fresh install's schema.
    for statement in _split_sql_statements(SchemaManager.get_schema()):
        op.execute(statement)


def downgrade() -> None:
    """Downgrade schema."""
    # No public "drop schema" helper ships in procrastinate itself; every
    # object it creates is named with a `procrastinate_` prefix, so this
    # drops by that prefix rather than hand-listing table/type/function names
    # that would drift against the library's own schema over time.
    op.execute(
        """
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN SELECT tablename FROM pg_tables
                WHERE schemaname = current_schema() AND tablename LIKE 'procrastinate_%'
            LOOP
                EXECUTE 'DROP TABLE IF EXISTS ' || quote_ident(r.tablename) || ' CASCADE';
            END LOOP;
            FOR r IN SELECT proname FROM pg_proc
                JOIN pg_namespace ON pg_namespace.oid = pg_proc.pronamespace
                WHERE pg_namespace.nspname = current_schema() AND proname LIKE 'procrastinate_%'
            LOOP
                EXECUTE 'DROP FUNCTION IF EXISTS ' || quote_ident(r.proname) || ' CASCADE';
            END LOOP;
            FOR r IN SELECT typname FROM pg_type
                WHERE typname LIKE 'procrastinate_%' AND typtype IN ('e', 'c')
            LOOP
                EXECUTE 'DROP TYPE IF EXISTS ' || quote_ident(r.typname) || ' CASCADE';
            END LOOP;
        END $$;
        """
    )
