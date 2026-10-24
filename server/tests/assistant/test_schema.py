"""Schema of the assistant tables (docs/03-modelo-datos.md §`kb_document`,
`kb_chunk`, `conversation` y `message`: el asistente (E12); ADR-0026; D-T12.1,
D-T12.2, D-T12.5).

Every test runs against real Postgres: `conftest._migrated_schema` migrates to
`head` once per session and downgrades to `base` at teardown, so a broken
upgrade or downgrade fails the whole suite, not just a dedicated test.

What these tests protect:

* the corpus is **full-precision** `vector(384)` with a **cosine HNSW** index
  (ADR-0026; D-T12.2) — half precision or another width would silently change
  what `intfloat/multilingual-e5-small` produces;
* the documented vocabularies (`kind`, `enso_state`) and D-T12.1's `helpful`
  CHECK, which are the constraints docs/03 states as *decisions*, not defensive
  extras;
* **missing evidence is `null`, never `0`**: an answer without an LLM call is
  writable with `model IS NULL` and `cost_cop IS NULL` (D-T12.1), which is what
  distinguishes "the LLM was never called" from "it was called and cost
  nothing";
* the ingestion idempotency key `(source_url, sha256)` (docs/03 §`kb_document`,
  `kb_chunk`, `conversation` y `message`, row `Unicidad de la ingesta`), which
  T3's ingestion write port is defined against;
* `conversation.plot_id` is **nullable on purpose**: docs/04-api.md:227 writes
  `POST /assistant/messages { conversation_id?, plot_id?, content }`, so the
  conversation a first message creates may be about no plot. docs/03:363-367
  annotates no nullability at all, and its silence is not a `NOT NULL`
  assertion — the same diagram writes `department "null = nacional"`
  (docs/03:351) where a null is meant. Do not "fix" this column back to `NOT
  NULL`; `user_id` is the one that stays `NOT NULL` (D-T12.1/D-T12.5);
* the **one head** of the migration graph: a second revision fails the `justfile`
  gate, and E10 T10 had to rechain for exactly that.

`kb_document` and `kb_chunk` carry no `org_id` (D-T12.5) and are therefore **not**
in `conftest`'s TRUNCATE list, so `_wiped_around_each_test` deletes every row
these tests write instead of leaking them into another test.
"""

from __future__ import annotations

import datetime
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.assistant.adapters.orm import (
    ConversationRow,
    KbChunkRow,
    KbDocumentRow,
    MessageRow,
)
from techcamp.assistant.application.ports import EMBEDDING_DIMENSION
from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import AppUserRow, OrganizationRow
from techcamp.shared.db import Base
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

SERVER_DIR = Path(__file__).resolve().parent.parent.parent
_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)
_ASSISTANT_TABLES = ("conversation", "kb_chunk", "kb_document", "message")
_WIPE_ORDER = ("message", "conversation", "kb_chunk", "kb_document")
"""`_wiped_around_each_test` deletes children before parents, or the transcript's
own foreign key refuses the delete."""


def _vector_literal(dimension: int) -> str:
    """The `vector` literal as Postgres parses it from text.

    A raw `text()` bind parameter has no SQLAlchemy type attached, so a Python
    list reaches asyncpg unencoded (`TypeError: expected str, got list`); the
    literal is text and Postgres casts it to the column's type.
    """
    return "[" + ",".join(["0.05"] * dimension) + "]"


_EMBEDDING = _vector_literal(EMBEDDING_DIMENSION)


@pytest.fixture(autouse=True)
async def _wiped_around_each_test(db_session: AsyncSession) -> Any:
    """These tables carry no `org_id` and `kb_document`/`kb_chunk` are not in
    `conftest`'s TRUNCATE list, so every row written here is deleted explicitly
    (D-T12.5; docs/03 §`kb_document`, `kb_chunk`, `conversation` y `message`, row
    `org_id`)."""
    await _wipe(db_session)
    yield
    await _wipe(db_session)


async def _wipe(db_session: AsyncSession) -> None:
    await db_session.rollback()
    for table in _WIPE_ORDER:
        await db_session.execute(text(f"DELETE FROM {table}"))
    await db_session.commit()


# -- helpers ------------------------------------------------------------------


async def _document(
    db_session: AsyncSession,
    *,
    title: str = "Guía de riego por goteo",
    source_url: str = "https://example.org/guia-goteo.pdf",
    sha256: str = "a" * 64,
    kind: str = "guide",
    published_on: datetime.date = datetime.date(2026, 9, 1),
    department: str | None = "Córdoba",
    enso_state: str | None = None,
) -> uuid.UUID:
    document_id = uuid7()
    await db_session.execute(
        text(
            "INSERT INTO kb_document (id, title, source_url, license, kind, sha256, "
            " published_on, department, enso_state) "
            "VALUES (:id, :title, :source_url, 'CC BY 4.0', :kind, :sha256, :published_on, "
            " :department, :enso_state)"
        ),
        {
            "id": document_id,
            "title": title,
            "source_url": source_url,
            "kind": kind,
            "sha256": sha256,
            "published_on": published_on,
            "department": department,
            "enso_state": enso_state,
        },
    )
    await db_session.commit()
    return document_id


async def _chunk(db_session: AsyncSession, document_id: uuid.UUID | None) -> uuid.UUID:
    chunk_id = uuid7()
    await db_session.execute(
        text(
            "INSERT INTO kb_chunk (id, document_id, content, embedding) "
            "VALUES (:id, :document_id, :content, :embedding)"
        ),
        {
            "id": chunk_id,
            "document_id": document_id,
            "content": "Riega por goteo dos veces por semana en época seca.",
            "embedding": _EMBEDDING,
        },
    )
    await db_session.commit()
    return chunk_id


async def _user(db_session: AsyncSession, phone: str = "+573001234567") -> uuid.UUID:
    """A real `app_user` row, the one thing every conversation must name
    (D-T12.1/D-T12.5: the daily limit reaches the user through
    `conversation.user_id`)."""
    user_id = uuid7()
    db_session.add(AppUserRow(id=user_id, phone=phone))
    await db_session.commit()
    return user_id


async def _conversation(db_session: AsyncSession) -> uuid.UUID:
    """A conversation of a real user about a real plot (D-T12.5)."""
    org_id = uuid7()
    db_session.add(OrganizationRow(id=org_id, name="Finca", kind="individual"))
    await db_session.commit()
    farm_id = uuid7()
    db_session.add(
        FarmRow(id=farm_id, org_id=org_id, name="Finca", municipality_code="47001", location=_POINT)
    )
    await db_session.commit()
    plot_id = uuid7()
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=_BOUNDARY,
            irrigation_system="none",
        )
    )
    user_id = await _user(db_session)
    conversation_id = uuid7()
    await db_session.execute(
        text("INSERT INTO conversation (id, user_id, plot_id) VALUES (:id, :user_id, :plot_id)"),
        {"id": conversation_id, "user_id": user_id, "plot_id": plot_id},
    )
    await db_session.commit()
    return conversation_id


async def _indexdef(db_session: AsyncSession, index_name: str) -> str:
    result = await db_session.execute(
        text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"), {"name": index_name}
    )
    indexdef = result.scalar_one_or_none()
    assert indexdef is not None, f"index {index_name} does not exist"
    return indexdef


def _indexed_columns(indexdef: str) -> list[str]:
    match = re.search(r"USING \w+ \(([^)]*)\)", indexdef)
    assert match is not None, f"no column list in {indexdef}"
    return match.group(1).split(", ")


# -- the corpus ----------------------------------------------------------------


@pytest.mark.parametrize("table", _ASSISTANT_TABLES)
async def test_the_assistant_table_exists(db_session: AsyncSession, table: str) -> None:
    result = await db_session.execute(
        text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = :table"
        ),
        {"table": table},
    )
    assert result.scalar_one_or_none() == table


async def test_the_vector_extension_is_installed(db_session: AsyncSession) -> None:
    """ADR-0003's precedent: the migration installs `vector` itself because
    CI's Postgres service has no compose init script to install it."""
    result = await db_session.execute(
        text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
    )
    assert result.scalar_one_or_none() == "vector"


async def test_kb_chunk_embedding_is_a_full_precision_384_dimension_vector(
    db_session: AsyncSession,
) -> None:
    """`vector(384)`, not `halfvec(384)`: D-T12.2 rejects half precision for a
    ~50 MB corpus, and 384 is `intfloat/multilingual-e5-small`'s own width
    (ADR-0026). `format_type` renders the type and its modifier, so a
    `halfvec`, a different dimension or no modifier at all would not read as
    `vector(384)`."""
    result = await db_session.execute(
        text(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = 'kb_chunk'::regclass AND attname = 'embedding' "
            "AND NOT attisdropped"
        )
    )
    assert result.scalar_one() == "vector(384)"


async def test_kb_chunk_searches_by_cosine_hnsw(db_session: AsyncSession) -> None:
    """`HNSW(embedding vector_cosine_ops)` is the semantic search of the corpus
    (docs/03 §Índices principales; ADR-0008:12)."""
    indexdef = await _indexdef(db_session, "ix_kb_chunk_embedding_cosine")
    assert "USING hnsw" in indexdef
    assert "vector_cosine_ops" in indexdef


async def test_a_repeated_ingestion_of_the_same_document_is_refused(
    db_session: AsyncSession,
) -> None:
    """`kb_document` is unique by `(source_url, sha256)` (docs/03 §`kb_document`,
    `kb_chunk`, `conversation` y `message`, row `Unicidad de la ingesta`):
    ingestion is a repeatable job (ADR-0012), so re-loading a document must not
    duplicate it nor leave its chunks orphaned."""
    await _document(db_session)

    with pytest.raises(IntegrityError):
        await _document(db_session, title="El mismo archivo con otro título")

    await db_session.rollback()


async def test_the_same_content_under_another_url_is_another_document(
    db_session: AsyncSession,
) -> None:
    """The key is the pair: the same content republished at another URL is
    another document, and only the exact pair is refused."""
    await _document(db_session)

    await _document(db_session, source_url="https://example.org/mirror/guia-goteo.pdf")


async def test_kb_document_rejects_an_unknown_kind(db_session: AsyncSession) -> None:
    """`kind` ∈ {guide, agroclimatic_bulletin} (docs/03:352): anything else is
    refused by the database, not only by the ingestion job."""
    with pytest.raises(IntegrityError):
        await _document(db_session, kind="manual")

    await db_session.rollback()


async def test_kb_document_rejects_an_unknown_enso_state(db_session: AsyncSession) -> None:
    """`enso_state` ∈ {neutral, el_nino, la_nina} (docs/03:354), and it is a
    property of the bulletin only — which is why the column is nullable and the
    vocabulary is a CHECK rather than an enum over two of three values."""
    with pytest.raises(IntegrityError):
        await _document(db_session, kind="agroclimatic_bulletin", enso_state="nino_neutro")

    await db_session.rollback()


async def test_a_national_document_has_no_department(db_session: AsyncSession) -> None:
    """`department` `null = nacional` (docs/03:353): the seasonal-climate fact
    reads the department's bulletin and falls back to the national one
    (docs/06 §9), so null is a value, not missing evidence."""
    document_id = await _document(
        db_session,
        title="Boletín Agroclimático Nacional",
        source_url="https://example.org/ban-nacional.pdf",
        kind="agroclimatic_bulletin",
        department=None,
        enso_state="la_nina",
    )

    result = await db_session.execute(
        text("SELECT department, enso_state FROM kb_document WHERE id = :id"), {"id": document_id}
    )
    department, enso_state = result.one()
    assert department is None
    assert enso_state == "la_nina"


async def test_a_chunk_requires_its_document(db_session: AsyncSession) -> None:
    """A fragment of no document can never be cited — a citation carries the
    source and its licence (ADR-0008:32) — so `document_id` is `NOT NULL`."""
    with pytest.raises(IntegrityError):
        await _chunk(db_session, None)

    await db_session.rollback()


async def test_a_stored_embedding_keeps_its_384_dimensions(db_session: AsyncSession) -> None:
    """The column's width is the model's width (ADR-0026), so a vector of
    another length is refused by the database rather than padded or truncated."""
    document_id = await _document(db_session)

    with pytest.raises(DBAPIError) as exc_info:
        await db_session.execute(
            text(
                "INSERT INTO kb_chunk (id, document_id, content, embedding) "
                "VALUES (:id, :document_id, 'texto', :embedding)"
            ),
            {"id": uuid7(), "document_id": document_id, "embedding": _vector_literal(128)},
        )
        await db_session.commit()

    assert "expected 384 dimensions, not 128" in str(exc_info.value.orig)
    await db_session.rollback()


# -- the transcript ------------------------------------------------------------


async def test_a_conversation_about_no_plot_is_writable(db_session: AsyncSession) -> None:
    """`plot_id` is **nullable on purpose**: `POST /assistant/messages
    { conversation_id?, plot_id?, content }` (docs/04-api.md:227) makes the
    first message's plot optional, so the conversation it creates is about no
    plot — which is exactly the `plot_id: UUID | None` of T1's frozen
    `ConversationRepository.create`
    (`assistant/application/ports.py:209`)."""
    user_id = await _user(db_session)
    conversation_id = uuid7()
    await db_session.execute(
        text("INSERT INTO conversation (id, user_id, plot_id) VALUES (:id, :user_id, NULL)"),
        {"id": conversation_id, "user_id": user_id},
    )
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT plot_id FROM conversation WHERE id = :id"), {"id": conversation_id}
    )
    assert result.scalar_one() is None


async def test_a_conversation_requires_its_user(db_session: AsyncSession) -> None:
    """`user_id` stays `NOT NULL`: every conversation belongs to a user, and
    the daily limit reaches the user through it (D-T12.1/D-T12.5), so a
    conversation with no owner would sit outside every isolation and outside the
    limit. Making `plot_id` nullable must not weaken this."""
    with pytest.raises(IntegrityError) as exc_info:
        await db_session.execute(
            text("INSERT INTO conversation (id, user_id, plot_id) VALUES (:id, NULL, NULL)"),
            {"id": uuid7()},
        )
        await db_session.commit()

    # The refusal is the one on `user_id`, not the one on `plot_id` the same
    # INSERT would have raised while `plot_id` was wrongly `NOT NULL`.
    assert 'null value in column "user_id"' in str(exc_info.value.orig)
    await db_session.rollback()


async def test_a_message_requires_its_created_at(db_session: AsyncSession) -> None:
    """D-T12.1: the daily limit counts `created_at` in America/Bogota
    (docs/04-api.md:22) and the monthly budget sums `cost_cop` over it
    (docs/11:132), so a message without a timestamp belongs to neither query."""
    conversation_id = await _conversation(db_session)

    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO message (id, conversation_id, role, content, created_at) "
                "VALUES (:id, :conversation_id, 'user', '¿cuándo riego?', NULL)"
            ),
            {"id": uuid7(), "conversation_id": conversation_id},
        )
        await db_session.commit()

    await db_session.rollback()


async def test_a_message_stores_its_created_at_when_the_caller_omits_it(
    db_session: AsyncSession,
) -> None:
    """`created_at` has a server default, so no writer has to pass the clock and
    no message is left unaccounted for."""
    conversation_id = await _conversation(db_session)
    message_id = uuid7()
    await db_session.execute(
        text(
            "INSERT INTO message (id, conversation_id, role, content) "
            "VALUES (:id, :conversation_id, 'user', '¿cuándo riego?')"
        ),
        {"id": message_id, "conversation_id": conversation_id},
    )
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT created_at FROM message WHERE id = :id"), {"id": message_id}
    )
    assert result.scalar_one() is not None


async def test_message_cost_cop_is_numeric_12_6(db_session: AsyncSession) -> None:
    """`numeric(12,6)`: COP up to 999 999.999999 keeps six decimals, so the
    `SUM(cost_cop)` of the month against the cap is exact (D-T12.1;
    docs/11:132)."""
    result = await db_session.execute(
        text(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = 'message'::regclass AND attname = 'cost_cop' "
            "AND NOT attisdropped"
        )
    )
    assert result.scalar_one() == "numeric(12,6)"


async def test_an_answer_without_an_llm_call_is_writable_with_null_evidence(
    db_session: AsyncSession,
) -> None:
    """D-T12.1 and docs/03 §Ausencia de evidencia: the degraded answer writes
    `model IS NULL`, tokens `null` and `cost_cop IS NULL`. **Missing evidence is
    never `0`**, and `model IS NULL` is what distinguishes "the LLM was never
    called" from "it was called and cost nothing" — SQL `SUM` ignores nulls,
    which is exactly the budget's behaviour."""
    conversation_id = await _conversation(db_session)
    message_id = uuid7()
    await db_session.execute(
        text(
            "INSERT INTO message (id, conversation_id, role, content, model, "
            " input_tokens, output_tokens, cost_cop, helpful) "
            "VALUES (:id, :conversation_id, 'assistant', 'No hay boletín del departamento.', "
            " NULL, NULL, NULL, NULL, NULL)"
        ),
        {"id": message_id, "conversation_id": conversation_id},
    )
    await db_session.commit()

    result = await db_session.execute(
        text(
            "SELECT model, input_tokens, output_tokens, cost_cop, helpful FROM message "
            "WHERE id = :id"
        ),
        {"id": message_id},
    )
    assert result.one() == (None, None, None, None, None)


async def test_a_user_question_carries_no_cost(db_session: AsyncSession) -> None:
    """The cost belongs to the answer: the question is what the daily limit
    counts and it was never a provider call (ADR-0007:14 — one call per
    request)."""
    conversation_id = await _conversation(db_session)
    await db_session.execute(
        text(
            "INSERT INTO message (id, conversation_id, role, content, cost_cop, model) "
            "VALUES (:id, :conversation_id, 'user', '¿cuándo riego?', NULL, NULL)"
        ),
        {"id": uuid7(), "conversation_id": conversation_id},
    )
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT cost_cop, model FROM message WHERE conversation_id = :conversation_id"),
        {"conversation_id": conversation_id},
    )
    assert result.one() == (None, None)


async def test_the_helpful_check_refuses_a_marked_user_message(
    db_session: AsyncSession,
) -> None:
    """`CHECK (helpful IS NULL OR role = 'assistant')` (D-T12.1): only an
    assistant answer can be marked helpful, so the feedback endpoint cannot
    record a rating of the user's own question."""
    conversation_id = await _conversation(db_session)

    with pytest.raises(IntegrityError) as exc_info:
        await db_session.execute(
            text(
                "INSERT INTO message (id, conversation_id, role, content, helpful) "
                "VALUES (:id, :conversation_id, 'user', '¿cuándo riego?', true)"
            ),
            {"id": uuid7(), "conversation_id": conversation_id},
        )
        await db_session.commit()

    assert "ck_message_helpful_only_assistant" in str(exc_info.value.orig)
    await db_session.rollback()


async def test_the_helpful_check_accepts_a_marked_assistant_answer(
    db_session: AsyncSession,
) -> None:
    """The positive side of the same CHECK: the happy path of the feedback
    endpoint writes `helpful` on an assistant answer."""
    conversation_id = await _conversation(db_session)
    message_id = uuid7()
    await db_session.execute(
        text(
            "INSERT INTO message (id, conversation_id, role, content, model, helpful) "
            "VALUES (:id, :conversation_id, 'assistant', 'Riega el lunes.', "
            "'deepseek-chat', true)"
        ),
        {"id": message_id, "conversation_id": conversation_id},
    )
    await db_session.commit()

    result = await db_session.execute(
        text("SELECT helpful FROM message WHERE id = :id"), {"id": message_id}
    )
    assert result.scalar_one() is True


@pytest.mark.parametrize("role", ["user", "assistant"])
async def test_the_helpful_check_admits_null_for_either_role(
    db_session: AsyncSession, role: str
) -> None:
    """`helpful IS NULL` is the ordinary state of every message until the user
    rates it, so the CHECK has to admit a null for both roles."""
    conversation_id = await _conversation(db_session)

    await db_session.execute(
        text(
            "INSERT INTO message (id, conversation_id, role, content, helpful) "
            "VALUES (:id, :conversation_id, :role, 'texto', NULL)"
        ),
        {"id": uuid7(), "conversation_id": conversation_id, "role": role},
    )
    await db_session.commit()


async def test_the_daily_count_index_is_conversation_id_then_created_at(
    db_session: AsyncSession,
) -> None:
    """`(conversation_id, created_at)` for the day's count of a user
    (docs/03 §Índices principales): the column order *is* the index, so a
    reversed definition would not answer the query it exists for."""
    indexdef = await _indexdef(db_session, "ix_message_conversation_created_at")
    assert _indexed_columns(indexdef) == ["conversation_id", "created_at"]


async def test_the_monthly_spend_index_is_created_at(db_session: AsyncSession) -> None:
    """`(created_at)` for `SUM(cost_cop)` of the month against the cap
    (docs/03 §Índices principales; docs/11:132)."""
    indexdef = await _indexdef(db_session, "ix_message_created_at")
    assert _indexed_columns(indexdef) == ["created_at"]


# -- the mapping and the migration do not drift --------------------------------


async def test_the_four_row_classes_map_the_four_tables() -> None:
    """One class per documented table, so a rename in one place is a failure
    here and not a repository that queries a table that no longer exists."""
    assert {
        row.__tablename__ for row in (KbDocumentRow, KbChunkRow, ConversationRow, MessageRow)
    } == (set(_ASSISTANT_TABLES))


_DOCUMENTED_CONSTRAINTS = {
    "kb_document": {"ck_kb_document_enso_state", "ck_kb_document_kind", "uq_kb_document_sha256"},
    "message": {"ck_message_helpful_only_assistant"},
}
"""The names the migration gives its CHECKs and its unique constraint (the
repository's `ck_<table>_<column>` / `uq_<table>_<columns>` convention)."""


async def test_the_orm_declares_the_documented_constraint_names() -> None:
    """The mapping carries the same names as the migration: a constraint whose
    name exists in only one of the two is drift, and the name is what an error
    message and a test quote."""
    declared = {
        table: {
            constraint.name
            for constraint in Base.metadata.tables[table].constraints
            if constraint.name is not None
        }
        for table in _DOCUMENTED_CONSTRAINTS
    }
    assert declared == _DOCUMENTED_CONSTRAINTS


async def test_the_migration_created_the_same_constraint_names_as_the_orm(
    db_session: AsyncSession,
) -> None:
    """The other half of the same guarantee, read from the database catalog."""
    for table, expected in _DOCUMENTED_CONSTRAINTS.items():
        result = await db_session.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = to_regclass(:table) AND contype IN ('c', 'u')"
            ),
            {"table": table},
        )
        assert set(result.scalars().all()) == expected


async def test_the_orm_and_the_migration_agree_on_every_assistant_index(
    db_session: AsyncSession,
) -> None:
    """Same guarantee for the indexes: the HNSW one on `kb_chunk` and the two
    supporting indexes on `message` (D-T12.1) exist on both sides, so neither
    can quietly lose or rename one."""
    orm_indexes = {
        table: {index.name for index in Base.metadata.tables[table].indexes}
        for table in ("kb_chunk", "message")
    }
    assert orm_indexes == {
        "kb_chunk": {"ix_kb_chunk_embedding_cosine"},
        "message": {"ix_message_conversation_created_at", "ix_message_created_at"},
    }

    result = await db_session.execute(
        text(
            "SELECT tablename, indexname FROM pg_indexes "
            "WHERE schemaname = 'public' AND tablename IN ('kb_chunk', 'message') "
            "AND indexname NOT LIKE '%\\_pkey'"
        )
    )
    migrated: dict[str, set[str]] = {table: set() for table in ("kb_chunk", "message")}
    for tablename, indexname in result.all():
        migrated[tablename].add(indexname)
    assert migrated == orm_indexes


async def test_the_conversation_plot_is_nullable_and_the_user_is_not() -> None:
    """The mapping's half of the same nullability contract the two behavioural
    tests above pin in the database: `plot_id` is `UUID | None` because
    docs/04-api.md:227 makes the first message's plot optional, and `user_id` is
    `NOT NULL` because every conversation belongs to a user (D-T12.1/D-T12.5).
    Without this, an ORM insert of a plot-less conversation would still be
    refused while the raw-SQL tests stayed green."""
    columns = ConversationRow.__table__.c
    assert columns.plot_id.nullable is True
    assert columns.user_id.nullable is False


@pytest.mark.parametrize("table", ("kb_document", "kb_chunk", "conversation"))
async def test_the_table_carries_no_org_id(table: str) -> None:
    """D-T12.5 and docs/03 §`kb_document`, `kb_chunk`, `conversation` y
    `message`: the corpus is shared institutional reference and a conversation
    belongs to a user. Isolation happens where the data leaves the server, in
    the cited source, and in `resolve_plot_access` for the plot — never in the
    table."""
    assert [c.name for c in Base.metadata.tables[table].columns if c.name == "org_id"] == []


async def test_the_mapping_owns_the_embedding_width_from_the_port() -> None:
    """`Vector(EMBEDDING_DIMENSION)`: the width has one owner
    (`assistant.application.ports.EMBEDDING_DIMENSION`, ADR-0026), so the
    mapping cannot disagree with the migration — whose literal `vector(384)` the
    previous test reads out of the catalog — about the width."""
    assert KbChunkRow.__table__.c.embedding.type.dim == EMBEDDING_DIMENSION == 384


async def test_the_migration_graph_has_exactly_one_head() -> None:
    """`justfile:140-147` fails CI on two heads, and E10 T10 had to rechain for
    exactly that: this task writes one revision on top of `b6e1c4a7f2d9`."""
    config = Config(str(SERVER_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(SERVER_DIR / "migrations"))

    assert len(ScriptDirectory.from_config(config).get_heads()) == 1
