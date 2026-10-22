"""The limit, the budget and the degraded branch on the endpoint (E12 T6;
docs/04-api.md §Límites de uso and :22; docs/06-diseno-detallado.md §9;
D-T12.1, D-T12.3, D-T12.5; docs/09-cuellos-de-botella.md §Seguridad).

`test_usage.py` decides the quota and `test_usage_quota.py` decides it in
Postgres; what this module proves is the contract a producer meets: the
**eleventh** question of his day is `429` with a `Retry-After` he can act on, an
exhausted budget answers the degraded no-LLM response **without calling the
provider at all**, and neither lets him read a plot he may not see.

The last one is why the access gates sit in front of the quota gate: the degraded
answer carries a plot's facts, so a caller over their limit must not be able to
use it as a way around `404` (D-T12.5).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant.db_fixtures import SeededDomain, seed_domain
from assistant.doubles import FakeEmbedder, FakeLLMClient
from techcamp.assistant.adapters.api.deps import (
    get_budget_cap,
    get_embedder,
    get_llm_client,
)
from techcamp.assistant.adapters.api.stream import ANSWER_EVENT, DONE_EVENT, SOURCES_EVENT
from techcamp.assistant.adapters.orm import KbChunkRow, KbDocumentRow, MessageRow
from techcamp.assistant.adapters.repositories import SqlAlchemyConversationRepository
from techcamp.assistant.application.ports import EMBEDDING_DIMENSION
from techcamp.assistant.domain.limits import (
    DAILY_QUESTION_LIMIT,
    seconds_until_local_midnight,
)
from techcamp.assistant.domain.models import MessageRole
from techcamp.main import app
from techcamp.shared.dates import BOGOTA_TZ, local_today
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

MESSAGES_URL = "/api/v1/assistant/messages"
QUESTION = "¿Por qué no regué ayer?"
ANSWER_TOKENS = ["La parcela ", "necesita 12,5 mm ", "de riego hoy."]


@pytest.fixture(autouse=True)
async def _wipe_corpus(db_session: AsyncSession) -> Iterator[None]:
    yield
    await db_session.execute(delete(KbChunkRow))
    await db_session.execute(delete(KbDocumentRow))
    await db_session.commit()


@pytest.fixture
async def domain(db_session: AsyncSession) -> SeededDomain:
    """The seeded rows, and the two non-database ports doubled.

    The budget cap is not overridden here: unset means the deployment has not set
    a COP amount yet, so the daily limit is the only gate these tests meet unless
    one overrides `get_budget_cap` itself.
    """
    seeded = await seed_domain(db_session)
    _override(get_llm_client, _fake_llm())
    _override(get_embedder, lambda: FakeEmbedder())
    return seeded


@pytest.fixture(autouse=True)
def _clear_dependency_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _override(dependency: Any, value: Any) -> None:
    app.dependency_overrides[dependency] = value if callable(value) else (lambda: value)


def _fake_llm() -> FakeLLMClient:
    return FakeLLMClient(
        tokens=ANSWER_TOKENS, model="seminar-local", prompt_tokens=3500, completion_tokens=42
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _client() -> TestClient:
    return TestClient(app)


def _blocks(body: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for raw in body.split("\n\n"):
        if not raw.strip():
            continue
        event, data = "", ""
        for line in raw.split("\n"):
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")
        blocks.append({"event": event, "data": json.loads(data)})
    return blocks


def _block(body: str, event: str) -> dict[str, Any] | None:
    for block in _blocks(body):
        if block["event"] == event:
            return block["data"]
    return None


def _ask(
    domain: SeededDomain,
    *,
    conversation_id: UUID | None = None,
    plot_id: UUID | None = None,
    token: str | None = None,
) -> Any:
    payload: dict[str, Any] = {"content": QUESTION}
    if conversation_id is not None:
        payload["conversation_id"] = str(conversation_id)
    if plot_id is not None:
        payload["plot_id"] = str(plot_id)
    return _client().post(
        MESSAGES_URL,
        json=payload,
        headers=_auth(token if token is not None else domain.token),
    )


async def _conversation_of(
    db_session: AsyncSession, domain: SeededDomain, *, plot_id: UUID | None = None
) -> UUID:
    conversation = await SqlAlchemyConversationRepository(db_session).create(
        user_id=domain.user_id, plot_id=plot_id
    )
    return conversation.id


async def _questions(
    db_session: AsyncSession, *, conversation_id: UUID, count: int, at: datetime
) -> None:
    for index in range(count):
        db_session.add(
            MessageRow(
                id=uuid7(),
                conversation_id=conversation_id,
                role=MessageRole.USER.value,
                content=f"pregunta anterior {index}",
                created_at=at,
            )
        )
    await db_session.commit()


async def _billed_answers(
    db_session: AsyncSession, *, conversation_id: UUID, costs: list[Decimal | None]
) -> None:
    for cost in costs:
        db_session.add(
            MessageRow(
                id=uuid7(),
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT.value,
                content="respuesta anterior",
                created_at=datetime.now(UTC),
                model="seminar-local" if cost is not None else None,
                cost_cop=cost,
            )
        )
    await db_session.commit()


async def _seed_corpus_fragment(db_session: AsyncSession, *, question: str) -> UUID:
    """One document and one fragment embedded exactly as the embedder embeds
    `question`, so the cosine is 1.0 and the retrieval returns it."""
    document_id = UUID("99999999-9999-9999-9999-999999999999")
    db_session.add(
        KbDocumentRow(
            id=document_id,
            title="Guía de riego por goteo",
            source_url="https://example.example/guia",
            license="CC-BY-4.0",
            kind="guide",
            sha256="sha-assistant-limits",
            published_on=date(2026, 6, 1),
            department=None,
            enso_state=None,
        )
    )
    await db_session.commit()
    vector = (await FakeEmbedder().embed([question]))[0]
    assert len(vector) == EMBEDDING_DIMENSION
    db_session.add(
        KbChunkRow(
            id=UUID("aaaaaaa1-0000-0000-0000-000000000000"),
            document_id=document_id,
            content="El estrés hídrico en floración reduce el cuaje.",
            embedding=vector,
        )
    )
    await db_session.commit()
    return document_id


# --- the daily limit ------------------------------------------------------


async def test_the_eleventh_question_of_the_day_answers_429_with_retry_after(
    domain: SeededDomain, db_session: AsyncSession
) -> None:
    """docs/04-api.md:22 — the eleventh question of the day answers `429` with
    `Retry-After`, and the tenth succeeds."""
    llm = _fake_llm()
    _override(get_llm_client, lambda: llm)
    conversation_id = await _conversation_of(db_session, domain, plot_id=domain.plot_id)
    await _questions(
        db_session,
        conversation_id=conversation_id,
        count=DAILY_QUESTION_LIMIT,
        at=datetime.now(UTC),
    )

    response = _ask(domain, conversation_id=conversation_id)

    assert response.status_code == 429
    retry_after = response.headers.get("retry-after")
    assert retry_after is not None
    # Seconds, not an HTTP-date: a value the client can act on without trusting
    # the server's clock (T6's acceptance criterion).
    assert retry_after.isdigit()
    assert 1 <= int(retry_after) <= 24 * 3600
    assert abs(int(retry_after) - seconds_until_local_midnight()) <= 1
    assert llm.call_count == 0


async def test_the_tenth_question_of_the_day_is_answered(
    domain: SeededDomain, db_session: AsyncSession
) -> None:
    llm = _fake_llm()
    _override(get_llm_client, lambda: llm)
    conversation_id = await _conversation_of(db_session, domain, plot_id=domain.plot_id)
    await _questions(
        db_session,
        conversation_id=conversation_id,
        count=DAILY_QUESTION_LIMIT - 1,
        at=datetime.now(UTC),
    )

    response = _ask(domain, conversation_id=conversation_id)

    assert response.status_code == 200
    assert llm.call_count == 1
    assert _block(response.text, DONE_EVENT) is not None


async def test_yesterdays_questions_do_not_count_against_today(
    domain: SeededDomain, db_session: AsyncSession
) -> None:
    """The count is the user's local day in America/Bogota (docs/06 §9): ten
    questions from yesterday leave today's ten slots free."""
    llm = _fake_llm()
    _override(get_llm_client, lambda: llm)
    conversation_id = await _conversation_of(db_session, domain, plot_id=domain.plot_id)
    yesterday = datetime.combine(local_today() - timedelta(days=1), time(12, 0), tzinfo=BOGOTA_TZ)
    await _questions(
        db_session,
        conversation_id=conversation_id,
        count=DAILY_QUESTION_LIMIT,
        at=yesterday,
    )

    response = _ask(domain, conversation_id=conversation_id)

    assert response.status_code == 200
    assert llm.call_count == 1


# --- the budget and the degraded answer -----------------------------------


async def test_an_exhausted_budget_answers_the_degraded_answer_without_a_provider_call(
    domain: SeededDomain, db_session: AsyncSession
) -> None:
    """docs/06 §9: limit or budget exhausted → the answer without the model, and
    zero provider calls. The cap is an argument, never a literal in the domain."""
    llm = _fake_llm()
    _override(get_llm_client, lambda: llm)
    _override(get_budget_cap, Decimal("20"))
    conversation_id = await _conversation_of(db_session, domain, plot_id=domain.plot_id)
    await _billed_answers(db_session, conversation_id=conversation_id, costs=[Decimal("20")])

    response = _ask(domain, conversation_id=conversation_id)

    assert response.status_code == 200
    answer = _block(response.text, ANSWER_EVENT)
    assert answer is not None
    assert answer["degraded"] is True
    assert answer["reason"] == "budget_exhausted"
    assert "Datos de la parcela" in answer["text"]
    assert _block(response.text, SOURCES_EVENT) is not None
    assert _block(response.text, DONE_EVENT) is not None
    assert llm.call_count == 0


async def test_an_exhausted_day_also_answers_the_degraded_answer(
    domain: SeededDomain, db_session: AsyncSession
) -> None:
    """The two docs meet here: docs/04 fixes the `429` and its `Retry-After`,
    docs/06 fixes the body. Both hold — the `429` carries the degraded stream."""
    llm = _fake_llm()
    _override(get_llm_client, lambda: llm)
    conversation_id = await _conversation_of(db_session, domain, plot_id=domain.plot_id)
    await _questions(
        db_session,
        conversation_id=conversation_id,
        count=DAILY_QUESTION_LIMIT,
        at=datetime.now(UTC),
    )

    response = _ask(domain, conversation_id=conversation_id)

    assert response.status_code == 429
    answer = _block(response.text, ANSWER_EVENT)
    assert answer is not None
    assert answer["degraded"] is True
    assert answer["reason"] == "daily_limit"
    assert f"{DAILY_QUESTION_LIMIT} preguntas" in answer["reason_text"]
    assert "Datos de la parcela" in answer["text"]
    assert _block(response.text, SOURCES_EVENT) is not None
    assert _block(response.text, DONE_EVENT) is not None
    assert llm.call_count == 0


async def test_the_degraded_answer_carries_the_citations_retrieval_resolved(
    domain: SeededDomain, db_session: AsyncSession
) -> None:
    _override(get_budget_cap, Decimal("5"))
    conversation_id = await _conversation_of(db_session, domain, plot_id=domain.plot_id)
    await _billed_answers(db_session, conversation_id=conversation_id, costs=[Decimal("5")])
    await _seed_corpus_fragment(db_session, question=QUESTION)

    response = _ask(domain, conversation_id=conversation_id)

    assert response.status_code == 200
    sources = _block(response.text, SOURCES_EVENT)
    assert sources is not None
    assert [citation["title"] for citation in sources["citations"]] == ["Guía de riego por goteo"]


async def test_the_degraded_answer_is_stored_without_a_model_tokens_or_a_cost(
    domain: SeededDomain, db_session: AsyncSession
) -> None:
    """D-T12.1: `model IS NULL` is what distinguishes "the LLM was never called"
    from "it was called and cost nothing"; the missing figures are `null`."""
    conversation_id = await _conversation_of(db_session, domain, plot_id=domain.plot_id)
    await _questions(
        db_session,
        conversation_id=conversation_id,
        count=DAILY_QUESTION_LIMIT,
        at=datetime.now(UTC),
    )

    response = _ask(domain, conversation_id=conversation_id)

    assert response.status_code == 429
    done = _block(response.text, DONE_EVENT)
    assert done is not None
    assert done["model"] is None
    assert done["prompt_tokens"] is None
    assert done["completion_tokens"] is None
    assert done["cost_cop"] is None

    stored = (
        (
            await db_session.execute(
                select(MessageRow)
                .where(
                    MessageRow.conversation_id == conversation_id,
                    MessageRow.role == MessageRole.ASSISTANT.value,
                )
                .order_by(MessageRow.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .one()
    )
    assert stored.model is None
    assert stored.input_tokens is None
    assert stored.output_tokens is None
    assert stored.cost_cop is None


async def test_a_refused_question_is_stored_all_the_same(
    domain: SeededDomain, db_session: AsyncSession
) -> None:
    """The question was asked either way: a refused turn is a turn of the
    transcript, which is what keeps tomorrow's count true."""
    conversation_id = await _conversation_of(db_session, domain, plot_id=domain.plot_id)
    await _questions(
        db_session,
        conversation_id=conversation_id,
        count=DAILY_QUESTION_LIMIT,
        at=datetime.now(UTC),
    )

    response = _ask(domain, conversation_id=conversation_id)

    assert response.status_code == 429
    questions = (
        (
            await db_session.execute(
                select(MessageRow).where(
                    MessageRow.conversation_id == conversation_id,
                    MessageRow.role == MessageRole.USER.value,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(questions) == DAILY_QUESTION_LIMIT + 1


async def test_a_question_with_no_plot_is_degraded_too(
    domain: SeededDomain, db_session: AsyncSession
) -> None:
    """A general question has no facts to print, but it still may not reach the
    provider when the budget is gone."""
    llm = _fake_llm()
    _override(get_llm_client, lambda: llm)
    _override(get_budget_cap, Decimal("5"))
    conversation_id = await _conversation_of(db_session, domain)
    await _billed_answers(db_session, conversation_id=conversation_id, costs=[Decimal("5")])

    response = _ask(domain, conversation_id=conversation_id)

    assert response.status_code == 200
    answer = _block(response.text, ANSWER_EVENT)
    assert answer is not None
    assert answer["degraded"] is True
    assert "no nombra ninguna" in answer["text"]
    assert llm.call_count == 0


async def test_the_answers_that_cost_nothing_do_not_spend_the_budget(
    domain: SeededDomain, db_session: AsyncSession
) -> None:
    """D-T12.1: `SUM(cost_cop)` ignores `null`; a response without a model does
    not consume the month's budget."""
    llm = _fake_llm()
    _override(get_llm_client, lambda: llm)
    _override(get_budget_cap, Decimal("10"))
    conversation_id = await _conversation_of(db_session, domain, plot_id=domain.plot_id)
    await _billed_answers(db_session, conversation_id=conversation_id, costs=[None, None, None])

    response = _ask(domain, conversation_id=conversation_id)

    assert response.status_code == 200
    assert llm.call_count == 1
    assert _block(response.text, ANSWER_EVENT) is None


# --- isolation and the shape of the body ----------------------------------


async def test_another_users_conversation_is_404_even_for_a_producer_over_the_limit(
    domain: SeededDomain, db_session: AsyncSession
) -> None:
    llm = _fake_llm()
    _override(get_llm_client, lambda: llm)
    conversation_id = await _conversation_of(db_session, domain, plot_id=domain.plot_id)
    await _questions(
        db_session,
        conversation_id=conversation_id,
        count=DAILY_QUESTION_LIMIT,
        at=datetime.now(UTC),
    )

    response = _ask(domain, conversation_id=conversation_id, token=domain.outsider_token)

    assert response.status_code == 404
    assert llm.call_count == 0


async def test_another_organizations_plot_is_404_even_for_a_producer_over_the_limit(
    domain: SeededDomain, db_session: AsyncSession
) -> None:
    llm = _fake_llm()
    _override(get_llm_client, lambda: llm)
    conversation_id = await _conversation_of(db_session, domain, plot_id=domain.plot_id)
    await _questions(
        db_session,
        conversation_id=conversation_id,
        count=DAILY_QUESTION_LIMIT,
        at=datetime.now(UTC),
    )

    response = _ask(domain, plot_id=domain.other_plot_id)

    assert response.status_code == 404
    assert llm.call_count == 0


async def test_the_degraded_body_has_the_same_frame_order_as_the_answered_one(
    domain: SeededDomain, db_session: AsyncSession
) -> None:
    """One contract for T7: `answer` replaces `token`, and `sources` and `done`
    come in the same order whichever of the two a producer meets."""
    answered = _ask(domain, plot_id=domain.plot_id)
    assert answered.status_code == 200
    answered_events = [block["event"] for block in _blocks(answered.text)]
    assert answered_events[0] == "token"
    assert answered_events[-2:] == [SOURCES_EVENT, DONE_EVENT]

    conversation_id = await _conversation_of(db_session, domain, plot_id=domain.plot_id)
    await _questions(
        db_session,
        conversation_id=conversation_id,
        count=DAILY_QUESTION_LIMIT,
        at=datetime.now(UTC),
    )
    degraded = _ask(domain, conversation_id=conversation_id)

    assert degraded.status_code == 429
    degraded_events = [block["event"] for block in _blocks(degraded.text)]
    assert degraded_events == [ANSWER_EVENT, SOURCES_EVENT, DONE_EVENT]
