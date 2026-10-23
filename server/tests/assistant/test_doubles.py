"""What the doubles themselves promise, so the later tests can rely on them (E12 T1).

Every port is external I/O and every double is a test double at one of them
(AGENTS.md: "a port exists only with two real implementations, or when external I/O
needs a test double"). These tests pin four things the rest of the epic depends on
and cannot prove on its own:

- each double satisfies the protocol it stands in for, so T2/T3's real adapter has
  the same shape to satisfy;
- retrieval honours `top_k` and the similarity floor, so a 0.74 fragment is
  excluded and a fifth qualifying fragment ends the list (ADR-0008:12);
- the stores are scoped by `user_id`, so another user's conversation and messages
  are simply **absent** — the 404 contract (D-T12.5, docs/04-api.md §Convenciones
  HTTP), which a repository test with a real database re-proves in T2;
- the *signatures* keep the isolation the docs decide: the corpus port takes no
  organization, and every conversation and message method takes its `user_id`. A
  port that grew an `org_id` would pass every other test here.
"""

from __future__ import annotations

import inspect
import math
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from assistant.doubles import (
    FakeConversationRepository,
    FakeEmbedder,
    FakeKnowledgeRepository,
    FakeLLMClient,
    FakeMessageRepository,
    FakeUsageQuota,
    fragment_vector_at,
)
from techcamp.assistant.application.ports import (
    EMBEDDING_DIMENSION,
    ConversationRepository,
    Embedder,
    KnowledgeRepository,
    LLMClient,
    MessageRepository,
    RetrievedFragment,
    StreamToken,
    TokenUsage,
    UsageQuotaPort,
)
from techcamp.assistant.domain.errors import ConversationNotFoundError
from techcamp.assistant.domain.limits import RETRIEVAL_TOP_K, SIMILARITY_FLOOR
from techcamp.assistant.domain.models import (
    ChatMessage,
    Conversation,
    Message,
    MessageEvidence,
    MessageRole,
)

pytestmark = pytest.mark.anyio

_AT = datetime(2026, 10, 6, 14, 30, tzinfo=UTC)
_USER_ID = UUID("11111111-1111-1111-1111-111111111111")
_OTHER_USER_ID = UUID("22222222-2222-2222-2222-222222222222")
_PLOT_ID = UUID("33333333-3333-3333-3333-333333333333")
_DOCUMENT_ID = UUID("44444444-4444-4444-4444-444444444444")


def test_every_double_satisfies_the_port_it_stands_in_for() -> None:
    conversations = FakeConversationRepository()
    messages = FakeMessageRepository(conversations=conversations)
    assert isinstance(FakeLLMClient(), LLMClient)
    assert isinstance(FakeEmbedder(), Embedder)
    assert isinstance(FakeKnowledgeRepository(), KnowledgeRepository)
    assert isinstance(conversations, ConversationRepository)
    assert isinstance(messages, MessageRepository)
    assert isinstance(FakeUsageQuota(messages=messages), UsageQuotaPort)


async def test_the_llm_double_records_the_messages_it_received() -> None:
    client = FakeLLMClient(text="Riegue el martes.")
    messages = [ChatMessage(role=MessageRole.USER, content="¿Por qué no regué ayer?")]

    async for _token in client.stream(messages):
        pass

    assert client.messages == [messages]
    assert client.call_count == 1


async def test_one_request_is_one_call_and_the_tokens_arrive_in_order() -> None:
    """ADR-0007:26 — the flow is deterministic: one call per request, no tool loop
    and no framework. The double makes that countable, so a flow that called twice
    would show it here."""
    client = FakeLLMClient(text="Riegue el martes.")

    tokens = [token async for token in client.stream([])]

    assert [token.text for token in tokens] == ["Riegue el martes."]
    assert client.call_count == 1


async def test_the_last_token_carries_the_usage_and_the_model_that_answered() -> None:
    """The `usage` of the provider's last frame is where `message.cost_cop` comes
    from (ADR-0026), so it travels with the stream and not out of band."""
    client = FakeLLMClient(text="Listo.", prompt_tokens=420, completion_tokens=12)

    tokens = [token async for token in client.stream([])]

    assert tokens[-1].usage == TokenUsage(prompt_tokens=420, completion_tokens=12)
    assert tokens[-1].model == "deepseek-chat"


async def test_the_embedder_returns_one_384_dimension_vector_per_text() -> None:
    # ADR-0026 pins `intfloat/multilingual-e5-small`: 384 dimensions, and T2
    # writes `vector(384)`.
    embedder = FakeEmbedder()

    vectors = await embedder.embed(["riego", "riesgo"])

    assert [len(vector) for vector in vectors] == [384, 384]
    assert EMBEDDING_DIMENSION == 384


async def test_the_embedding_of_a_text_is_deterministic_and_not_shared() -> None:
    embedder = FakeEmbedder()

    (first,) = await embedder.embed(["riego el martes"])
    (again,) = await embedder.embed(["riego el martes"])
    (other,) = await embedder.embed(["riesgo de inundación"])

    assert first == again
    assert first != other


async def test_a_known_text_embeds_to_a_unit_vector() -> None:
    """The knowledge double compares by cosine, which is what pgvector's HNSW
    `vector_cosine_ops` does (docs/03-modelo-datos.md §Índices principales)."""

    (vector,) = await FakeEmbedder().embed(["riego"])

    assert math.isclose(math.sqrt(sum(value * value for value in vector)), 1.0, abs_tol=1e-9)


async def test_a_wrong_dimension_is_refused_by_the_embedder() -> None:
    """The dimension is a property of the pinned model, not a preference of the
    caller: 384 floats or an error, so a bad vector never reaches `kb_chunk`."""

    with pytest.raises(ValueError):
        await FakeEmbedder(dimension=8).embed(["riego"])


async def test_retrieval_excludes_a_fragment_below_the_similarity_floor() -> None:
    # ADR-0008:12 — similitud ≥ 0,75.
    knowledge = FakeKnowledgeRepository()
    knowledge.add_fragment(
        document_id=_DOCUMENT_ID, content="No alcanza", embedding=fragment_vector_at(0.74)
    )

    found = await knowledge.search_by_embedding([1.0, 0.0], top_k=5, similarity_floor=0.75)

    assert found == []


async def test_retrieval_includes_a_fragment_exactly_on_the_floor() -> None:
    knowledge = FakeKnowledgeRepository()
    knowledge.add_fragment(
        document_id=_DOCUMENT_ID, content="Alcanza justo", embedding=fragment_vector_at(0.75)
    )

    found = await knowledge.search_by_embedding([1.0, 0.0], top_k=5, similarity_floor=0.75)

    assert [fragment.content for fragment in found] == ["Alcanza justo"]


async def test_retrieval_returns_at_most_the_asked_fragments_closest_first() -> None:
    knowledge = FakeKnowledgeRepository()
    for similarity in (0.76, 0.95, 0.80, 0.99, 0.88, 0.77):
        knowledge.add_fragment(
            document_id=_DOCUMENT_ID,
            content=f"fragmento {similarity}",
            embedding=fragment_vector_at(similarity),
        )

    found = await knowledge.search_by_embedding([1.0, 0.0], top_k=5, similarity_floor=0.75)

    assert len(found) == 5
    assert [round(fragment.similarity, 2) for fragment in found] == [0.99, 0.95, 0.88, 0.80, 0.77]


async def test_retrieval_records_the_question_it_was_asked() -> None:
    knowledge = FakeKnowledgeRepository()

    await knowledge.search_by_embedding([1.0, 0.0], top_k=3, similarity_floor=0.75)

    assert knowledge.calls == [([1.0, 0.0], 3, 0.75)]


async def test_a_question_and_a_stored_fragment_of_the_same_text_match() -> None:
    """The two doubles compose the way the real ones do: the embedder's vector is
    what retrieval compares."""
    embedder = FakeEmbedder()
    knowledge = FakeKnowledgeRepository()
    (vector,) = await embedder.embed(["¿por qué no regué ayer?"])
    knowledge.add_fragment(
        document_id=_DOCUMENT_ID, content="Riego aplazado por déficit de humedad", embedding=vector
    )
    (other,) = await embedder.embed(["¿cuándo germinó el ají?"])

    same = await knowledge.search_by_embedding(vector, top_k=5, similarity_floor=0.75)
    unrelated = await knowledge.search_by_embedding(other, top_k=5, similarity_floor=0.75)

    assert [fragment.content for fragment in same] == ["Riego aplazado por déficit de humedad"]
    assert unrelated == []


async def test_a_fragment_with_no_direction_never_reaches_the_floor() -> None:
    """A zero vector has no direction: it scores `0.0` instead of dividing by
    zero, so a broken embedding can neither crash retrieval nor pass the floor."""
    knowledge = FakeKnowledgeRepository()
    knowledge.add_fragment(document_id=_DOCUMENT_ID, content="sin dirección", embedding=[0.0, 0.0])

    assert await knowledge.search_by_embedding([1.0, 0.0], top_k=5, similarity_floor=0.75) == []
    assert [
        fragment.content
        for fragment in await knowledge.search_by_embedding(
            [1.0, 0.0], top_k=5, similarity_floor=0.0
        )
    ] == ["sin dirección"]


async def test_a_document_is_found_by_id_and_an_unknown_one_is_absent() -> None:
    knowledge = FakeKnowledgeRepository()
    knowledge.add_document(
        document_id=_DOCUMENT_ID,
        title="Boletín Agroclimático Nacional, octubre 2026",
        kind="agroclimatic_bulletin",
        source_url="https://example.org/ban-oct-2026",
    )

    document = await knowledge.get_document(_DOCUMENT_ID)

    assert document is not None
    assert document.title == "Boletín Agroclimático Nacional, octubre 2026"
    assert await knowledge.get_document(uuid4()) is None


async def test_a_conversation_is_only_visible_to_its_owner() -> None:
    conversations = FakeConversationRepository()
    conversation = await conversations.create(user_id=_USER_ID, plot_id=_PLOT_ID)

    assert (
        await conversations.get_for_user(user_id=_USER_ID, conversation_id=conversation.id)
        == conversation
    )
    assert (
        await conversations.get_for_user(user_id=_OTHER_USER_ID, conversation_id=conversation.id)
        is None
    )


async def test_a_conversation_of_another_user_is_absent_from_the_list() -> None:
    conversations = FakeConversationRepository()
    mine = await conversations.create(user_id=_USER_ID, plot_id=_PLOT_ID)
    theirs = await conversations.create(user_id=_OTHER_USER_ID, plot_id=_PLOT_ID)

    listed = await conversations.list_for_user(user_id=_USER_ID)

    assert [conversation.id for conversation in listed] == [mine.id]
    assert theirs.id not in {conversation.id for conversation in listed}


def _question(conversation_id: UUID, *, at: datetime) -> Message:
    return Message(
        id=uuid4(),
        conversation_id=conversation_id,
        role=MessageRole.USER,
        content="¿Por qué no regué ayer?",
        created_at=at,
    )


async def _store() -> tuple[FakeConversationRepository, FakeMessageRepository, Conversation]:
    conversations = FakeConversationRepository()
    messages = FakeMessageRepository(conversations=conversations)
    conversation = await conversations.create(user_id=_USER_ID, plot_id=_PLOT_ID)
    return conversations, messages, conversation


async def test_a_message_is_stored_in_the_conversation_that_asked_for_it() -> None:
    _conversations, messages, conversation = await _store()

    stored = await messages.add(
        user_id=_USER_ID,
        conversation_id=conversation.id,
        message=_question(conversation.id, at=_AT),
    )
    listed = await messages.list_for_user(user_id=_USER_ID, conversation_id=conversation.id)

    assert stored.role is MessageRole.USER
    assert [message.content for message in listed] == ["¿Por qué no regué ayer?"]


async def test_another_user_cannot_read_or_write_a_transcript() -> None:
    """docs/04-api.md §Convenciones HTTP: another user's conversation answers
    `404`, never `403` and never an empty `200`. Here that is absence."""
    conversations, messages, conversation = await _store()
    await messages.add(
        user_id=_USER_ID,
        conversation_id=conversation.id,
        message=_question(conversation.id, at=_AT),
    )

    assert (
        await messages.list_for_user(user_id=_OTHER_USER_ID, conversation_id=conversation.id) == []
    )
    with pytest.raises(ConversationNotFoundError):
        await messages.add(
            user_id=_OTHER_USER_ID,
            conversation_id=conversation.id,
            message=_question(conversation.id, at=_AT),
        )


async def test_feedback_is_stored_only_for_a_visible_message() -> None:
    _conversations, messages, conversation = await _store()
    answer = await messages.add(
        user_id=_USER_ID,
        conversation_id=conversation.id,
        message=Message(
            id=uuid4(),
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content="Riego aplazado por déficit de humedad.",
            created_at=_AT,
            evidence=MessageEvidence.from_call(
                model="deepseek-chat",
                prompt_tokens=420,
                completion_tokens=12,
                cost_cop=Decimal("1.5"),
            ),
        ),
    )

    assert await messages.set_helpful(user_id=_USER_ID, message_id=answer.id, helpful=True) is True
    assert (
        await messages.set_helpful(user_id=_OTHER_USER_ID, message_id=answer.id, helpful=False)
        is False
    )


async def test_the_quota_counts_questions_of_the_local_day_and_nothing_else() -> None:
    conversations = FakeConversationRepository()
    messages = FakeMessageRepository(conversations=conversations)
    quota = FakeUsageQuota(messages=messages)
    mine = await conversations.create(user_id=_USER_ID, plot_id=_PLOT_ID)
    theirs = await conversations.create(user_id=_OTHER_USER_ID, plot_id=_PLOT_ID)
    # 2026-10-07T02:00Z is still 2026-10-06 in Bogota; 05:00Z is 00:00 there.
    await messages.add(
        user_id=_USER_ID,
        conversation_id=mine.id,
        message=_question(mine.id, at=datetime(2026, 10, 7, 2, 0, tzinfo=UTC)),
    )
    await messages.add(
        user_id=_USER_ID,
        conversation_id=mine.id,
        message=_question(mine.id, at=datetime(2026, 10, 6, 20, 0, tzinfo=UTC)),
    )
    await messages.add(
        user_id=_USER_ID,
        conversation_id=mine.id,
        message=_question(mine.id, at=datetime(2026, 10, 7, 5, 0, tzinfo=UTC)),
    )
    await messages.add(
        user_id=_OTHER_USER_ID,
        conversation_id=theirs.id,
        message=_question(theirs.id, at=datetime(2026, 10, 6, 20, 0, tzinfo=UTC)),
    )

    assert await quota.questions_asked_on(user_id=_USER_ID, day=date(2026, 10, 6)) == 2
    assert await quota.questions_asked_on(user_id=_USER_ID, day=date(2026, 10, 7)) == 1


async def test_the_month_spend_sums_the_costs_and_ignores_the_missing_ones() -> None:
    """D-T12.1: the no-LLM answer stores `cost_cop IS NULL` and the sum ignores it,
    exactly as SQL `SUM` does. A month of another year is not this month's spend,
    and a real zero is a zero that counts."""
    conversations = FakeConversationRepository()
    messages = FakeMessageRepository(conversations=conversations)
    quota = FakeUsageQuota(messages=messages)
    conversation = await conversations.create(user_id=_USER_ID, plot_id=_PLOT_ID)
    for day, evidence in (
        (
            date(2026, 10, 2),
            MessageEvidence.from_call(
                model="deepseek-chat", prompt_tokens=1, completion_tokens=1, cost_cop=Decimal("3.5")
            ),
        ),
        (date(2026, 10, 3), MessageEvidence.no_llm()),
        (
            date(2026, 10, 4),
            MessageEvidence.from_call(
                model="deepseek-chat", prompt_tokens=1, completion_tokens=1, cost_cop=Decimal(0)
            ),
        ),
        (
            date(2026, 9, 30),
            MessageEvidence.from_call(
                model="deepseek-chat", prompt_tokens=1, completion_tokens=1, cost_cop=Decimal("99")
            ),
        ),
    ):
        await messages.add(
            user_id=_USER_ID,
            conversation_id=conversation.id,
            message=Message(
                id=uuid4(),
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                content="respuesta",
                created_at=datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC),
                evidence=evidence,
            ),
        )

    spend = await quota.month_spend_cop(user_id=_USER_ID, month=date(2026, 10, 1))

    # September's billed answer is not October's spend, and the two missing costs
    # are skipped rather than added as `0.00`.
    assert spend == Decimal("3.5")


async def test_a_month_with_nothing_recorded_spends_nothing() -> None:
    """No evidence of spending is evidence of no spending, and not "unknown": the
    cap comparison needs a number."""
    messages = FakeMessageRepository(conversations=FakeConversationRepository())

    assert await FakeUsageQuota(messages=messages).month_spend_cop(
        user_id=_USER_ID, month=date(2026, 10, 1)
    ) == Decimal(0)


def test_a_retrieved_fragment_carries_its_document_content_and_similarity() -> None:
    fragment = RetrievedFragment(
        id=uuid4(), document_id=_DOCUMENT_ID, content="texto", similarity=0.91
    )

    assert fragment.similarity == 0.91


def test_a_stream_token_without_usage_is_a_plain_piece_of_text() -> None:
    assert StreamToken(text="Riego ").usage is None


def test_the_floor_and_top_k_are_constants_of_the_module() -> None:
    """The double is told both, so a test that passed a literal as its own
    argument would prove nothing about the query T2 will write."""
    assert (RETRIEVAL_TOP_K, SIMILARITY_FLOOR) == (5, 0.75)


def test_the_knowledge_repository_filters_by_no_organization() -> None:
    """D-T12.5: `kb_document` and `kb_chunk` carry no `org_id` because the corpus
    is shared institutional reference. A port that grew an organization filter
    would make every later adapter carry a fiction."""
    for method in (KnowledgeRepository.search_by_embedding, KnowledgeRepository.get_document):
        assert "org_id" not in inspect.signature(method).parameters


def test_every_conversation_and_message_method_is_scoped_by_its_user() -> None:
    """docs/09 §Seguridad with D-T12.5: `conversation` and `message` filter by the
    owning user, so every method of both stores has to take it."""
    scoped = (
        ConversationRepository.create,
        ConversationRepository.get_for_user,
        ConversationRepository.list_for_user,
        MessageRepository.add,
        MessageRepository.list_for_user,
        MessageRepository.set_helpful,
    )
    for method in scoped:
        assert "user_id" in inspect.signature(method).parameters, method.__qualname__


def test_the_quota_port_is_scoped_by_its_user_too() -> None:
    """The daily limit is per user and the budget is the organization's own: a
    quota read without a user would hand one producer another's count."""
    for method in (
        UsageQuotaPort.questions_asked_on,
        UsageQuotaPort.month_spend_cop,
        UsageQuotaPort.admit_question,
    ):
        assert "user_id" in inspect.signature(method).parameters, method.__qualname__
