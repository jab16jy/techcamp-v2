"""`POST /assistant/messages` and `…:feedback` (E12 T5; D-T12.4; ADR-0027;
docs/04-api.md §Riesgo, métricas y asistente; docs/06-diseno-detallado.md §9;
D-T12.1, D-T12.5; docs/09-cuellos-de-botella.md §Seguridad).

The two endpoints of docs/04:227-228, exercised through the real app with real
Postgres rows and the two ports that are not the database replaced by doubles.

What these tests protect:

* **The stream is the response of its own request** (ADR-0027): `text/event-stream`
  with `token` frames, then `sources`, then `done` — and no `id:`, no `LISTEN`,
  no hub.
* **No database session is held while tokens are yielded** (D-T12.4,
  `R3-stream-holds-db-session`): every token chunk is asserted against the
  engine's pool, which is the bug's own reproduction
  (`tests/telemetry/test_stream_api.py` asserts the same thing for the hub
  stream).
* **Exactly one provider call per request** with the four send-list sections
  (ADR-0007:26, D-T12.6), and the answer's Spanish copy reaching the producer.
* **Both halves of the turn are stored**: the question and the answer, with the
  model, the two token counts and the cost (docs/06:365, D-T12.1).
* **Isolation is `404`, never `403` and never an empty `200`**: a conversation of
  another user, a plot of another organization, and a message of another user
  for the feedback endpoint.
* **The feedback endpoint stores `helpful` and answers `204`**
  (docs/04-api.md:228).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant.db_fixtures import SeededDomain, seed_domain
from assistant.doubles import FakeEmbedder, FakeLLMClient
from techcamp.assistant.adapters.api.deps import (
    SEMINAR_MODEL,
    get_embedder,
    get_llm_client,
)
from techcamp.assistant.adapters.api.pricing import ModelPrice
from techcamp.assistant.adapters.api.router import AskRequest, ask
from techcamp.assistant.adapters.api.stream import DONE_EVENT, SOURCES_EVENT, TOKEN_EVENT
from techcamp.assistant.adapters.orm import KbChunkRow, KbDocumentRow, MessageRow
from techcamp.assistant.application.ports import EMBEDDING_DIMENSION
from techcamp.main import app
from techcamp.shared.db import engine

pytestmark = pytest.mark.anyio

MESSAGES_URL = "/api/v1/assistant/messages"
QUESTION = "¿Por qué no regué ayer?"
ANSWER_TOKENS = ["La parcela ", "necesita 12,5 mm ", "de riego hoy."]


@pytest.fixture(autouse=True)
async def _wipe_corpus(db_session: AsyncSession) -> AsyncIterator[None]:
    yield
    await db_session.execute(delete(KbChunkRow))
    await db_session.execute(delete(KbDocumentRow))
    await db_session.commit()


@pytest.fixture
async def domain(db_session: AsyncSession) -> SeededDomain:
    """The seeded rows, and the two non-database ports doubled.

    The `Embedder` is the T1 double because embedding the question is not what
    these endpoints are about, and the corpus is empty in most of these tests;
    the `LLMClient` is the T1 double so a test can count the calls and read the
    exact prompt that left the server (D-T12.6).
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
    """Install a dependency override. A provider **instance** is passed straight
    through (FastAPI only calls a callable), and a factory is wrapped in one."""
    app.dependency_overrides[dependency] = value if callable(value) else (lambda: value)


def _fake_llm() -> FakeLLMClient:
    return FakeLLMClient(
        tokens=ANSWER_TOKENS, model="seminar-local", prompt_tokens=3500, completion_tokens=42
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _blocks(body: str) -> list[dict[str, Any]]:
    """The SSE body read the way the web client reads it: one block per blank
    line, `event:` for the name and JSON for the payload."""
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


def _client() -> TestClient:
    return TestClient(app)


# --- the streaming endpoint ------------------------------------------------


async def test_the_answer_arrives_as_a_stream_of_tokens_sources_and_done(
    domain: SeededDomain,
) -> None:
    """docs/04:227: `POST /assistant/messages` → `text/event-stream` with the
    tokens and the cited sources."""
    response = _client().post(
        MESSAGES_URL,
        json={"plot_id": str(domain.plot_id), "content": QUESTION},
        headers=_auth(domain.token),
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    blocks = _blocks(response.text)

    assert [block["event"] for block in blocks] == [
        TOKEN_EVENT,
        TOKEN_EVENT,
        TOKEN_EVENT,
        SOURCES_EVENT,
        DONE_EVENT,
    ]
    assert [block["data"]["text"] for block in blocks[:3]] == ANSWER_TOKENS
    sources = [block for block in blocks if block["event"] == SOURCES_EVENT][0]["data"]
    assert sources["citations"] == []
    assert blocks[-1]["data"]["model"] == SEMINAR_MODEL
    assert blocks[-1]["data"]["cost_cop"] == "0.000000"


async def test_the_stream_holds_no_database_session_while_the_tokens_flow(
    domain: SeededDomain,
) -> None:
    """D-T12.4, the `R3-stream-holds-db-session` bug of
    `telemetry/adapters/api/router.py:446-485`.

    The pool is the instrument: the route is called directly and its body is
    consumed chunk by chunk, so every assertion is taken **between** two tokens,
    with the answer still in flight. A `yield`-scoped session would be checked
    out for exactly that window — one pooled connection per open answer, starving
    every REST endpoint.
    """
    response = await ask(
        AskRequest(plot_id=domain.plot_id, content=QUESTION),
        user_id=domain.user_id,
        llm=_fake_llm(),
        embedder=FakeEmbedder(),
        prices={SEMINAR_MODEL: ModelPrice.local(SEMINAR_MODEL)},
        budget_cap_cop=None,
    )

    checked_out = 0

    def _on_checkout(*_args: object) -> None:
        nonlocal checked_out
        checked_out += 1

    def _on_checkin(*_args: object) -> None:
        nonlocal checked_out
        checked_out -= 1

    baseline = checked_out
    event.listen(engine.sync_engine, "checkout", _on_checkout)
    event.listen(engine.sync_engine, "checkin", _on_checkin)
    try:
        body = cast(AsyncIterator[bytes], response.body_iterator)
        checked_out_during_tokens: list[int] = []
        async for chunk in body:
            if TOKEN_EVENT.encode() in chunk:
                checked_out_during_tokens.append(checked_out)
    finally:
        event.remove(engine.sync_engine, "checkout", _on_checkout)
        event.remove(engine.sync_engine, "checkin", _on_checkin)

    assert checked_out_during_tokens, "the test saw no token frame"
    assert set(checked_out_during_tokens) == {baseline}


async def test_exactly_one_provider_call_carries_the_four_send_list_sections(
    domain: SeededDomain,
) -> None:
    """ADR-0007:26 (one call, no tool loop) and D-T12.6 (the send-list is code).

    The prompt is read from the double afterwards, because the evidence of what
    left the server is the only thing worth asserting about it.
    """
    llm = _fake_llm()
    _override(get_llm_client, lambda: llm)

    response = _client().post(
        MESSAGES_URL,
        json={"plot_id": str(domain.plot_id), "content": QUESTION},
        headers=_auth(domain.token),
    )
    assert response.status_code == 200

    assert llm.call_count == 1
    (messages,) = llm.messages
    assert [message.role.value for message in messages] == ["system", "user", "user", "user"]
    assert "Responder en español sencillo." in messages[0].content
    assert str(domain.plot_id) in messages[1].content
    assert QUESTION in messages[3].content


async def test_both_turns_are_stored_with_the_evidence_of_the_call(
    domain: SeededDomain,
    db_session: AsyncSession,
) -> None:
    """docs/06:365 — "guardar mensaje, tokens y costo": the question, the answer,
    the model, both token counts and the cost (D-T12.1)."""
    response = _client().post(
        MESSAGES_URL,
        json={"plot_id": str(domain.plot_id), "content": QUESTION},
        headers=_auth(domain.token),
    )
    done = _blocks(response.text)[-1]["data"]

    rows = (
        (
            await db_session.execute(
                select(MessageRow).order_by(MessageRow.created_at, MessageRow.id)
            )
        )
        .scalars()
        .all()
    )

    assert [row.role for row in rows] == ["user", "assistant"]
    assert rows[0].content == QUESTION
    assert rows[1].content == "".join(ANSWER_TOKENS)
    assert rows[1].model == "seminar-local"
    assert rows[1].input_tokens == 3500
    assert rows[1].output_tokens == 42
    assert rows[1].cost_cop == Decimal("0.000000")
    assert str(rows[1].id) == done["message_id"]


async def test_the_fragments_the_corpus_holds_reach_the_prompt_and_the_sources_frame(
    domain: SeededDomain, db_session: AsyncSession
) -> None:
    """The sources an answer cites are the corpus documents retrieval resolved —
    a client renders them, so they travel as structured data (docs/04:227)."""
    question = "¿Qué hago si el cultivo se seca?"
    document_id = await _seed_corpus_fragment(db_session, question=question)
    llm = _fake_llm()
    _override(get_llm_client, lambda: llm)

    response = _client().post(
        MESSAGES_URL,
        json={"plot_id": str(domain.plot_id), "content": question},
        headers=_auth(domain.token),
    )
    blocks = _blocks(response.text)

    sources = [block for block in blocks if block["event"] == SOURCES_EVENT][0]["data"]
    (citation,) = sources["citations"]
    assert citation["source_url"] == "https://example.example/guia"
    assert citation["title"] == "Guía de riego por goteo"
    assert document_id is not None
    assert "Guía de riego por goteo" in llm.messages[0][2].content


async def test_a_question_that_names_no_plot_still_answers(
    domain: SeededDomain,
) -> None:
    """docs/04:227: `plot_id` is optional — a general question belongs to a
    conversation about no plot, and its prompt carries no fact block."""
    llm = _fake_llm()
    _override(get_llm_client, lambda: llm)

    response = _client().post(
        MESSAGES_URL, json={"content": "¿Qué es el ICA?"}, headers=_auth(domain.token)
    )
    assert response.status_code == 200
    blocks = _blocks(response.text)

    assert blocks[0]["event"] == TOKEN_EVENT
    assert "sin datos de parcela" in llm.messages[0][1].content


async def test_a_conversation_carries_the_next_question_and_its_own_plot(
    domain: SeededDomain,
) -> None:
    """The second turn of a conversation reuses it, and a `plot_id` sent again is
    answered about that plot."""
    first = _client().post(
        MESSAGES_URL,
        json={"plot_id": str(domain.plot_id), "content": QUESTION},
        headers=_auth(domain.token),
    )
    conversation_id = _blocks(first.text)[-1]["data"]["conversation_id"]

    second = _client().post(
        MESSAGES_URL,
        json={"conversation_id": conversation_id, "content": "¿Y mañana?"},
        headers=_auth(domain.token),
    )
    assert second.status_code == 200
    assert _blocks(second.text)[-1]["data"]["conversation_id"] == conversation_id


async def test_the_answer_is_in_plain_spanish_with_the_glossary_vocabulary(
    domain: SeededDomain,
) -> None:
    """docs/06:381-387 and docs/00-glosario.md: the copy a producer reads is
    Spanish, and the system prompt the provider receives says so."""
    llm = FakeLLMClient(
        tokens=["La humedad del suelo está por debajo del umbral de riego."],
        model="seminar-local",
        prompt_tokens=10,
        completion_tokens=12,
    )
    _override(get_llm_client, lambda: llm)

    response = _client().post(
        MESSAGES_URL,
        json={"plot_id": str(domain.plot_id), "content": "¿Necesito regar?"},
        headers=_auth(domain.token),
    )
    blocks = _blocks(response.text)

    answer = "".join(block["data"]["text"] for block in blocks if block["event"] == TOKEN_EVENT)
    assert "humedad del suelo" in answer
    assert "Responder en español sencillo." in llm.messages[0][0].content


# --- isolation -------------------------------------------------------------


async def test_a_conversation_of_another_user_answers_404(domain: SeededDomain) -> None:
    """docs/04 §Convenciones HTTP: never `403`, never an empty `200`."""
    response = _client().post(
        MESSAGES_URL,
        json={"conversation_id": str(UUID(int=1)), "content": QUESTION},
        headers=_auth(domain.token),
    )
    assert response.status_code == 404
    assert "conversation" in response.text.lower()


async def test_a_plot_of_another_organization_answers_404(domain: SeededDomain) -> None:
    """The plot gate is `resolve_plot_access` (docs/09 §Seguridad): the outsider's
    plot is as invisible as a plot that does not exist."""
    response = _client().post(
        MESSAGES_URL,
        json={"plot_id": str(domain.other_plot_id), "content": QUESTION},
        headers=_auth(domain.token),
    )
    # Absent, not forbidden: the answer is the same one an id that does not exist
    # gets, and nothing of the plot is echoed back (docs/04 §Convenciones HTTP).
    assert response.status_code == 404
    assert "Lote ajeno" not in response.text


async def test_a_question_without_a_token_answers_401(domain: SeededDomain) -> None:
    response = _client().post(
        MESSAGES_URL, json={"plot_id": str(domain.plot_id), "content": QUESTION}
    )
    assert response.status_code == 401


async def test_an_empty_question_answers_422(domain: SeededDomain) -> None:
    """A question with no text is not a question: docs/04:227's body carries
    `content`, and an empty one would spend a provider call to answer nothing."""
    response = _client().post(
        MESSAGES_URL,
        json={"plot_id": str(domain.plot_id), "content": ""},
        headers=_auth(domain.token),
    )
    assert response.status_code == 422


# --- the feedback endpoint -------------------------------------------------


async def _answered(domain: SeededDomain) -> str:
    response = _client().post(
        MESSAGES_URL,
        json={"plot_id": str(domain.plot_id), "content": QUESTION},
        headers=_auth(domain.token),
    )
    answer = _blocks(response.text)[-1]["data"]
    return str(answer["message_id"])


async def test_feedback_is_stored_and_answers_204(
    domain: SeededDomain, db_session: AsyncSession
) -> None:
    """docs/04-api.md:228: `POST /assistant/messages/{message_id}:feedback
    { helpful }` → 204."""
    message_id = await _answered(domain)

    response = _client().post(
        f"{MESSAGES_URL}/{message_id}:feedback",
        json={"helpful": True},
        headers=_auth(domain.token),
    )
    assert response.status_code == 204
    assert response.content == b""

    row = (
        await db_session.execute(select(MessageRow).where(MessageRow.id == UUID(message_id)))
    ).scalar_one()
    assert row.helpful is True


async def test_feedback_can_be_withdrawn(domain: SeededDomain, db_session: AsyncSession) -> None:
    message_id = await _answered(domain)

    for helpful in (True, False):
        response = _client().post(
            f"{MESSAGES_URL}/{message_id}:feedback",
            json={"helpful": helpful},
            headers=_auth(domain.token),
        )
        assert response.status_code == 204

    row = (
        await db_session.execute(select(MessageRow).where(MessageRow.id == UUID(message_id)))
    ).scalar_one()
    assert row.helpful is False


async def test_feedback_on_a_message_of_another_user_answers_404(domain: SeededDomain) -> None:
    message_id = await _answered(domain)

    response = _client().post(
        f"{MESSAGES_URL}/{message_id}:feedback",
        json={"helpful": True},
        headers=_auth(domain.outsider_token),
    )
    assert response.status_code == 404


async def test_feedback_on_an_unknown_message_answers_404(domain: SeededDomain) -> None:
    response = _client().post(
        f"{MESSAGES_URL}/{UUID(int=9)}:feedback",
        json={"helpful": False},
        headers=_auth(domain.token),
    )
    assert response.status_code == 404


async def _seed_corpus_fragment(db_session: AsyncSession, *, question: str) -> UUID:
    """One corpus document and one fragment **embedded exactly as the embedder
    embeds `question`**, so the cosine is 1.0 and the retrieval's own floor and
    ranking are not what this test is about."""
    document_id = UUID("99999999-9999-9999-9999-999999999999")
    db_session.add(
        KbDocumentRow(
            id=document_id,
            title="Guía de riego por goteo",
            source_url="https://example.example/guia",
            license="CC-BY-4.0",
            kind="guide",
            sha256="sha-api-test",
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
