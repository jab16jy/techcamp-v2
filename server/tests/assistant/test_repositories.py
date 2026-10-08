"""The assistant's SQL repositories (E12 T5; ADR-0008; docs/03-modelo-datos.md §
`kb_document`, `kb_chunk`, `conversation` y `message`: el asistente (E12);
D-T12.1, D-T12.5; docs/09-cuellos-de-botella.md §Seguridad).

Three adapters, all of them reads and writes of this module's own tables:

* **`SqlAlchemyKnowledgeRepository`** — the vector search ADR-0008:12 asks for
  (cosine over `vector(384)`, top 5, floor 0.75), the document lookup a citation
  is built from, and the latest agroclimatic bulletin with its national fallback.
  **No `org_id` filter anywhere**, because the corpus is shared institutional
  reference (D-T12.5) — the same row for every organization, exactly like
  `municipality` / `model_version` / `risk_prediction`.
* **`SqlAlchemyConversationRepository`** and **`SqlAlchemyMessageRepository`** —
  the transcript. Every method takes the `user_id`, because `conversation`
  carries no `org_id` (D-T12.5) and isolation is the owning user plus
  `resolve_plot_access`. **Another user's row is absent, not refused**: the
  endpoint maps absence to `404`, never to `403` and never to an empty `200`
  (docs/04-api.md §Convenciones HTTP).

What these tests protect, each a way a repository that "works" still leaks:

* **Another user's conversation and message are invisible** — `get_for_user`,
  `list_for_user` and `set_helpful` filter by `user_id` (docs/09 §Seguridad).
* **`set_helpful` answers `False` for a message of another user**, which is what
  `POST /assistant/messages/{id}:feedback` turns into `404`.
* **D-T12.1's evidence round-trips**: `model`, the two token counts and
  `cost_cop` are stored and read back, and the degraded answer stores them
  **null** — `model IS NULL` is what distinguishes "the LLM was never called"
  from "it was called and cost nothing".
* **The floor is applied in SQL** as well as in the use case
  (`application.retrieval` re-filters): a repository that ignored it would hand
  a 0.74 fragment to the use case, and the double in that use case's tests is
  what makes the re-filter observable.
* **The bulletin falls back to the national one** when the department has none
  (docs/06 §9: "o del nacional si no hay uno regional"), and picks the latest by
  `published_on`.
* **`created_at` is written by the database**, not by the caller: the daily limit
  counts by it (docs/06 §9) and the monthly budget sums over it
  (docs/11-metricas.md:132), so a writer that passed its own clock would make
  both readings a function of the writer.

`kb_document`, `kb_chunk`, `conversation` and `message` carry no `org_id` and
hang off `app_user`/`plot`, so `conftest`'s `TRUNCATE organization ... CASCADE`
reaches `conversation` but **not** `kb_document`/`kb_chunk`: the fixture below
wipes the corpus tables around every test in this module.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant.db_fixtures import seed_domain
from techcamp.assistant.adapters.orm import (
    ConversationRow,
    KbChunkRow,
    KbDocumentRow,
    MessageRow,
)
from techcamp.assistant.adapters.repositories import (
    SqlAlchemyConversationRepository,
    SqlAlchemyKnowledgeRepository,
    SqlAlchemyMessageRepository,
)
from techcamp.assistant.application.ports import EMBEDDING_DIMENSION, KnowledgeDocument
from techcamp.assistant.domain.models import (
    Conversation,
    Message,
    MessageEvidence,
    MessageRole,
)
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

OWNER: UUID
OTHER: UUID
PLOT_ID: UUID
DOCUMENT_ID = uuid7()

_AT = datetime(2026, 10, 7, 14, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
async def _domain(db_session: AsyncSession) -> AsyncIterator[None]:
    """Real `app_user` / `plot` rows behind the ids below: `conversation.user_id`
    and `conversation.plot_id` are foreign keys, so a transcript test that faked
    them would be asserting against a schema this repository never runs on."""
    seeded = await seed_domain(db_session)
    global OWNER, OTHER, PLOT_ID
    OWNER, OTHER, PLOT_ID = seeded.user_id, seeded.outsider_id, seeded.plot_id
    yield


@pytest.fixture(autouse=True)
async def _wipe_corpus(db_session: AsyncSession) -> AsyncIterator[None]:
    """The corpus tables are shared reference with no `org_id`, so nothing in
    `conftest`'s TRUNCATE list reaches them (same reason as
    `test_kb_ingest_job`)."""
    yield
    await db_session.execute(delete(KbChunkRow))
    await db_session.execute(delete(KbDocumentRow))
    await db_session.commit()


def _unit_vector(index: int) -> list[float]:
    """A 384-float vector whose direction is `index` on the first axis, so the
    cosine between two of them is exactly 1.0 or 0.0 — a similarity a test can
    assert on without floating-point slack."""
    vector = [0.0] * EMBEDDING_DIMENSION
    vector[index] = 1.0
    return vector


async def _seed_document(
    session: AsyncSession,
    *,
    document_id: UUID,
    kind: str = "guide",
    title: str = "Guía de riego por goteo",
    department: str | None = None,
    published_on: date = date(2026, 9, 30),
    enso_state: str | None = None,
) -> UUID:
    session.add(
        KbDocumentRow(
            id=document_id,
            title=title,
            source_url="https://example.example/guia",
            license="CC-BY-4.0",
            kind=kind,
            sha256=f"sha-{document_id}",
            published_on=published_on,
            department=department,
            enso_state=enso_state,
        )
    )
    await session.commit()
    return document_id


def _leaning_vector() -> list[float]:
    """A 384-float vector 0.9 along the first axis and 0.1 along the second: its
    cosine against `_unit_vector(0)` is ~0.994, comfortably above the floor and
    below 1.0, so "closest first" is observable without floating-point slack."""
    vector = [0.0] * EMBEDDING_DIMENSION
    vector[0] = 0.9
    vector[1] = 0.1
    return vector


async def _seed_chunk(
    session: AsyncSession,
    *,
    document_id: UUID,
    direction: int | None = None,
    content: str = "Un fragmento.",
    vector: list[float] | None = None,
) -> UUID:
    chunk_id = uuid4()
    session.add(
        KbChunkRow(
            id=chunk_id,
            document_id=document_id,
            content=content,
            embedding=vector if vector is not None else _unit_vector(direction or 0),
        )
    )
    await session.commit()
    return chunk_id


async def _seed_conversation(session: AsyncSession, *, user_id: UUID) -> Conversation:
    repository = SqlAlchemyConversationRepository(session)
    conversation = await repository.create(user_id=user_id, plot_id=PLOT_ID)
    return conversation


async def _seed_message(
    session: AsyncSession,
    *,
    user_id: UUID,
    conversation_id: UUID,
    role: MessageRole = MessageRole.ASSISTANT,
    content: str = "La parcela necesita 12,5 mm de riego.",
    evidence: MessageEvidence | None = None,
) -> Message:
    repository = SqlAlchemyMessageRepository(session)
    return await repository.add(
        user_id=user_id,
        conversation_id=conversation_id,
        message=Message(
            id=uuid4(),
            conversation_id=conversation_id,
            role=role,
            content=content,
            created_at=_AT,
            evidence=evidence if evidence is not None else MessageEvidence.no_llm(),
        ),
    )


# --- the corpus ------------------------------------------------------------


async def test_search_returns_the_closest_chunks_and_drops_what_is_below_the_floor(
    db_session: AsyncSession,
) -> None:
    """ADR-0008:12: five fragments at similarity ≥ 0.75, and a 0.74 fragment is
    not evidence. The floor is applied here as well as in
    `application.retrieval`, so a repository cannot widen it."""
    document_id = await _seed_document(db_session, document_id=DOCUMENT_ID)
    await _seed_chunk(db_session, document_id=document_id, direction=0, content="Coincide.")
    await _seed_chunk(db_session, document_id=document_id, direction=3, content="Perpendicular.")

    repository = SqlAlchemyKnowledgeRepository(db_session)
    found = await repository.search_by_embedding(_unit_vector(0), top_k=5, similarity_floor=0.75)

    assert [fragment.content for fragment in found] == ["Coincide."]
    assert found[0].similarity == pytest.approx(1.0)


async def test_search_returns_at_most_top_k_in_closest_first_order(
    db_session: AsyncSession,
) -> None:
    """`top_k` is a cap the caller states, not a promise the repository ignores:
    three matching chunks with `top_k=2` answer with two, best first."""
    document_id = await _seed_document(db_session, document_id=DOCUMENT_ID)
    # Two fragments close to the query's direction and one far from it, so the
    # order and the cap are both observable without floating-point slack: the
    # query is the first axis and the second fragment leans on it.
    await _seed_chunk(db_session, document_id=document_id, direction=0, content="El más cercano")
    await _seed_chunk(
        db_session, document_id=document_id, content="Casi tan cercano", vector=_leaning_vector()
    )
    await _seed_chunk(db_session, document_id=document_id, direction=3, content="Perpendicular")

    repository = SqlAlchemyKnowledgeRepository(db_session)
    found = await repository.search_by_embedding(_unit_vector(0), top_k=2, similarity_floor=0.75)

    assert len(found) == 2
    similarities = [fragment.similarity for fragment in found]
    assert similarities == sorted(similarities, reverse=True)
    assert all(similarity >= 0.75 for similarity in similarities)


async def test_search_answers_with_nothing_when_the_corpus_holds_no_close_fragment(
    db_session: AsyncSession,
) -> None:
    """Fewer than `top_k` — including none — is a legitimate answer (the port's
    own contract): a corpus that holds nothing close is evidence the answer has
    to report as missing."""
    document_id = await _seed_document(db_session, document_id=DOCUMENT_ID)
    await _seed_chunk(db_session, document_id=document_id, direction=7)

    repository = SqlAlchemyKnowledgeRepository(db_session)
    found = await repository.search_by_embedding(_unit_vector(0), top_k=5, similarity_floor=0.75)

    assert found == []


async def test_a_document_is_read_with_the_fields_a_citation_is_built_from(
    db_session: AsyncSession,
) -> None:
    """ADR-0008:12 records source **and** licence per document, and
    `latest_climate_bulletin` reads `department` and `enso_state` — so the
    projection has to carry all of them."""
    await _seed_document(
        db_session,
        document_id=DOCUMENT_ID,
        kind="agroclimatic_bulletin",
        title="Boletín Agroclimático Nacional",
        published_on=date(2026, 9, 30),
        enso_state="neutral",
    )

    repository = SqlAlchemyKnowledgeRepository(db_session)
    document = await repository.get_document(DOCUMENT_ID)

    assert document == KnowledgeDocument(
        id=DOCUMENT_ID,
        title="Boletín Agroclimático Nacional",
        kind="agroclimatic_bulletin",
        source_url="https://example.example/guia",
        license="CC-BY-4.0",
        published_on=date(2026, 9, 30),
        department=None,
        enso_state="neutral",
    )


async def test_a_missing_document_is_absent_rather_than_empty(
    db_session: AsyncSession,
) -> None:
    """A fragment whose document is gone cannot be cited, so the lookup answers
    `None` and the use case drops it (`application.retrieval`)."""
    repository = SqlAlchemyKnowledgeRepository(db_session)

    assert await repository.get_document(uuid4()) is None


async def test_the_latest_bulletin_prefers_the_departments_and_falls_back_to_the_national_one(
    db_session: AsyncSession,
) -> None:
    """docs/06 §9: the latest bulletin **of the farm's department, or the national
    one if there is no regional one**, by publication date."""
    national_id = await _seed_document(
        db_session,
        document_id=DOCUMENT_ID,
        kind="agroclimatic_bulletin",
        title="Boletín Agroclimático Nacional",
        published_on=date(2026, 9, 30),
        enso_state="neutral",
    )
    await _seed_document(
        db_session,
        document_id=uuid4(),
        kind="agroclimatic_bulletin",
        title="Boletín de Córdoba",
        department="Córdoba",
        published_on=date(2026, 8, 31),
        enso_state="la_nina",
    )
    newest_cordoba_id = await _seed_document(
        db_session,
        document_id=uuid4(),
        kind="agroclimatic_bulletin",
        title="Boletín de Córdoba más reciente",
        department="Córdoba",
        published_on=date(2026, 9, 25),
        enso_state="el_nino",
    )

    repository = SqlAlchemyKnowledgeRepository(db_session)
    regional = await repository.latest_climate_bulletin(department="Córdoba")
    fallback = await repository.latest_climate_bulletin(department="Bolívar")

    assert regional is not None and regional.id == newest_cordoba_id
    assert fallback is not None and fallback.id == national_id


async def test_a_corpus_without_bulletins_answers_with_nothing(
    db_session: AsyncSession,
) -> None:
    """No bulletin is a missing seasonal fact, which the answer names (docs/06 §9)
    — never an invented one."""
    await _seed_document(db_session, document_id=DOCUMENT_ID, kind="guide")

    repository = SqlAlchemyKnowledgeRepository(db_session)

    assert await repository.latest_climate_bulletin(department=None) is None


# --- the transcript --------------------------------------------------------


async def test_a_conversation_is_created_with_its_owner_and_its_plot(
    db_session: AsyncSession,
) -> None:
    """docs/03:363-366: `conversation` is `id`, `user_id` and a nullable
    `plot_id` — no `org_id` (D-T12.5)."""
    repository = SqlAlchemyConversationRepository(db_session)

    conversation = await repository.create(user_id=OWNER, plot_id=PLOT_ID)

    assert conversation.user_id == OWNER
    assert conversation.plot_id == PLOT_ID
    stored = await db_session.execute(select(ConversationRow.user_id))
    assert stored.scalar_one() == OWNER


async def test_a_question_about_no_plot_still_has_a_conversation(
    db_session: AsyncSession,
) -> None:
    """docs/04-api.md:227: `POST /assistant/messages { conversation_id?, plot_id?,
    content }` — the first message may name no plot."""
    repository = SqlAlchemyConversationRepository(db_session)

    conversation = await repository.create(user_id=OWNER, plot_id=None)

    assert conversation.plot_id is None


async def test_another_users_conversation_is_absent_and_not_refused(
    db_session: AsyncSession,
) -> None:
    """docs/09 §Seguridad: a resource of another organization answers `404`, never
    `403`, so the repository's answer is absence."""
    conversation = await _seed_conversation(db_session, user_id=OWNER)

    repository = SqlAlchemyConversationRepository(db_session)

    assert await repository.get_for_user(user_id=OWNER, conversation_id=conversation.id)
    assert await repository.get_for_user(user_id=OTHER, conversation_id=conversation.id) is None


async def test_the_conversations_of_a_user_come_back_oldest_first_and_only_his(
    db_session: AsyncSession,
) -> None:
    repository = SqlAlchemyConversationRepository(db_session)
    first = await repository.create(user_id=OWNER, plot_id=None)
    second = await repository.create(user_id=OWNER, plot_id=PLOT_ID)
    await repository.create(user_id=OTHER, plot_id=None)

    listed = await repository.list_for_user(user_id=OWNER)

    assert [conversation.id for conversation in listed] == [first.id, second.id]


async def test_a_turn_is_stored_with_its_role_and_the_database_own_the_clock(
    db_session: AsyncSession,
) -> None:
    """`created_at` has a server default (docs/03:368-376): the daily limit counts
    by it and the monthly budget sums over it, so a writer that passed its own
    clock would make both readings a function of the writer."""
    conversation = await _seed_conversation(db_session, user_id=OWNER)

    stored = await _seed_message(
        db_session, user_id=OWNER, conversation_id=conversation.id, role=MessageRole.USER
    )

    row = await db_session.execute(
        select(MessageRow.role, MessageRow.created_at).where(MessageRow.id == stored.id)
    )
    role, created_at = row.one()
    assert role == "user"
    assert created_at is not None


async def test_the_evidence_of_a_call_round_trips_and_a_degraded_answer_stores_nulls(
    db_session: AsyncSession,
) -> None:
    """D-T12.1: the four evidence columns travel together, and the answer without
    an LLM call stores them **null** — `model IS NULL` is what distinguishes "no
    se llamó al LLM" from "se llamó y no costó nada"."""
    conversation = await _seed_conversation(db_session, user_id=OWNER)
    repository = SqlAlchemyMessageRepository(db_session)

    called = await _seed_message(
        db_session,
        user_id=OWNER,
        conversation_id=conversation.id,
        evidence=MessageEvidence.from_call(
            model="deepseek-chat",
            prompt_tokens=3500,
            completion_tokens=400,
            cost_cop=Decimal("184.500000"),
        ),
    )
    degraded = await _seed_message(
        db_session,
        user_id=OWNER,
        conversation_id=conversation.id,
        evidence=MessageEvidence.no_llm(),
    )

    turns = {
        message.id: message
        for message in await repository.list_for_user(
            user_id=OWNER, conversation_id=conversation.id
        )
    }

    assert turns[called.id].evidence == MessageEvidence.from_call(
        model="deepseek-chat",
        prompt_tokens=3500,
        completion_tokens=400,
        cost_cop=Decimal("184.500000"),
    )
    assert turns[degraded.id].evidence == MessageEvidence.no_llm()
    assert turns[degraded.id].evidence.called is False


async def test_the_turns_of_a_conversation_come_back_oldest_first(
    db_session: AsyncSession,
) -> None:
    conversation = await _seed_conversation(db_session, user_id=OWNER)
    repository = SqlAlchemyMessageRepository(db_session)
    question = await _seed_message(
        db_session,
        user_id=OWNER,
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content="¿Por qué no regué ayer?",
    )
    answer = await _seed_message(db_session, user_id=OWNER, conversation_id=conversation.id)

    listed = await repository.list_for_user(user_id=OWNER, conversation_id=conversation.id)

    assert [message.id for message in listed] == [question.id, answer.id]


async def test_another_users_turns_are_absent(db_session: AsyncSession) -> None:
    conversation = await _seed_conversation(db_session, user_id=OWNER)
    await _seed_message(db_session, user_id=OWNER, conversation_id=conversation.id)

    repository = SqlAlchemyMessageRepository(db_session)

    assert await repository.list_for_user(user_id=OTHER, conversation_id=conversation.id) == []


async def test_feedback_is_stored_and_reads_back(db_session: AsyncSession) -> None:
    """docs/04-api.md:228: `POST /assistant/messages/{id}:feedback { helpful }`."""
    conversation = await _seed_conversation(db_session, user_id=OWNER)
    answer = await _seed_message(db_session, user_id=OWNER, conversation_id=conversation.id)
    repository = SqlAlchemyMessageRepository(db_session)

    assert await repository.set_helpful(user_id=OWNER, message_id=answer.id, helpful=True) is True

    turns = await repository.list_for_user(user_id=OWNER, conversation_id=conversation.id)
    assert turns[0].helpful is True


async def test_feedback_can_be_withdrawn(db_session: AsyncSession) -> None:
    """A rating is a rating of one answer, and a producer who changes his mind
    must be able to say so: the column is written, not only ever set."""
    conversation = await _seed_conversation(db_session, user_id=OWNER)
    answer = await _seed_message(db_session, user_id=OWNER, conversation_id=conversation.id)
    repository = SqlAlchemyMessageRepository(db_session)
    await repository.set_helpful(user_id=OWNER, message_id=answer.id, helpful=True)

    assert await repository.set_helpful(user_id=OWNER, message_id=answer.id, helpful=False) is True

    turns = await repository.list_for_user(user_id=OWNER, conversation_id=conversation.id)
    assert turns[0].helpful is False


async def test_a_turn_cannot_be_written_into_another_users_conversation(
    db_session: AsyncSession,
) -> None:
    """The foreign key would happily accept it: `message.conversation_id` points
    at `conversation.id` and says nothing about who owns it. Without this check a
    turn could be written into someone else's transcript — the one leak the
    schema cannot prevent on its own (D-T12.5)."""
    from techcamp.assistant.domain.errors import ConversationNotFoundError

    conversation = await _seed_conversation(db_session, user_id=OWNER)
    repository = SqlAlchemyMessageRepository(db_session)

    with pytest.raises(ConversationNotFoundError):
        await repository.add(
            user_id=OTHER,
            conversation_id=conversation.id,
            message=Message(
                id=uuid4(),
                conversation_id=conversation.id,
                role=MessageRole.USER,
                content="¿Cuál es su teléfono?",
                created_at=_AT,
            ),
        )


async def test_feedback_on_a_message_of_another_user_answers_false(
    db_session: AsyncSession,
) -> None:
    """The feedback endpoint's `404`: absence, never `403`
    (docs/04-api.md §Convenciones HTTP)."""
    conversation = await _seed_conversation(db_session, user_id=OWNER)
    answer = await _seed_message(db_session, user_id=OWNER, conversation_id=conversation.id)

    repository = SqlAlchemyMessageRepository(db_session)

    assert await repository.set_helpful(user_id=OTHER, message_id=answer.id, helpful=True) is False


async def test_feedback_on_a_users_own_question_is_refused_by_the_database(
    db_session: AsyncSession,
) -> None:
    """D-T12.1's CHECK: only an assistant answer can be rated. The domain refuses
    it first (`HelpfulFlagOnUserMessageError`), and the database refuses it too —
    this is the constraint doing its job when the domain is bypassed."""
    from sqlalchemy.exc import IntegrityError

    conversation = await _seed_conversation(db_session, user_id=OWNER)
    question = await _seed_message(
        db_session,
        user_id=OWNER,
        conversation_id=conversation.id,
        role=MessageRole.USER,
    )
    db_session.add(
        MessageRow(
            id=question.id,
            conversation_id=question.conversation_id,
            role="user",
            content=question.content,
            created_at=_AT,
            helpful=True,
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()
