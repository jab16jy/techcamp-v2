"""add procrastinate schema

Revision ID: 231a40930eb5
Revises: 8c3983dc2dfd
Create Date: 2026-09-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
from procrastinate.schema import SchemaManager

# revision identifiers, used by Alembic.
revision: str = '231a40930eb5'
down_revision: Union[str, Sequence[str], None] = '8c3983dc2dfd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _split_sql_statements(sql: str) -> list[str]:
    """`SchemaManager.get_schema()` returns one script of `;`-separated
    statements, several of them `$$`-quoted plpgsql function bodies. asyncpg
    (this project's driver) prepares each `op.execute()` call as a single
    statement and refuses a string containing more than one command
    (`cannot insert multiple commands into a prepared statement`, confirmed
    against a real run), so each top-level statement is executed on its own,
    tracking `$$...$$` spans so a semicolon inside a function body doesn't
    split it."""
    statements = []
    current: list[str] = []
    in_dollar = False
    i = 0
    n = len(sql)
    while i < n:
        if sql[i : i + 2] == "$$":
            in_dollar = not in_dollar
            current.append("$$")
            i += 2
            continue
        ch = sql[i]
        if ch == ";" and not in_dollar:
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
