"""The assistant's SSE body and what a call costs (E12 T5; D-T12.4; ADR-0027;
docs/04-api.md §Riesgo, métricas y asistente; docs/06-diseno-detallado.md §9;
ADR-0026; D-T12.1).

`POST /assistant/messages` returns `text/event-stream` (docs/04:227), and this
module is the body of that response: the frames the producer reads and the
persistence that happens **around** the tokens, never during them.

What these tests protect, each one a way a stream that "works" still fails:

* **The frame contract is explicit and parseable**: `event:` + `data:` +
  a blank line, JSON in `data`, no `id:` (ADR-0027: this stream has no replay —
  it is born and dies with its request, so a `Last-Event-ID` would mean nothing),
  and the three frames arrive in one fixed order: tokens, then the cited
  sources, then the closing `done`.
* **No database session is held while tokens are yielded** (D-T12.4, the
  `R3-stream-holds-db-session` bug `telemetry/adapters/api/router.py:446-485`
  records). The body holds no session at all: it calls a `persist` callable that
  opens and closes its own one, and it calls it **after** the last token has been
  yielded, so a producer who reads slowly cannot pin a pooled connection.
* **`done` carries the persisted evidence** — the `message_id` the transcript
  really has, the model, the tokens and the cost (D-T12.1) — because `done` is
  emitted after the write, never before.
* **A provider that fails mid-stream ends the stream with `error`**, never with
  a silent truncation: the status code can no longer be changed once the body
  has started, so the client learns from the body.
* **A stream with no `usage` persists nothing.** D-T12.1 makes the four evidence
  columns all-or-nothing, and inventing tokens or a cost of zero for an answer
  the provider did not account for would write a figure the system never
  measured; the question stays in the transcript and the answer does not.
* **Cost is arithmetic over an injected price table** and never a literal:
  ADR-0026 states docs/02:78's prices are obsolete and T8 writes the real ones,
  so a model with no price is a **wiring error**, never a free call.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from assistant.doubles import FakeLLMClient
from techcamp.assistant.adapters.api.pricing import (
    ModelPrice,
    PriceTable,
    UnknownModelPriceError,
    cost_cop,
    price_for,
)
from techcamp.assistant.adapters.api.stream import (
    DONE_EVENT,
    ERROR_EVENT,
    SOURCES_EVENT,
    TOKEN_EVENT,
    stream_assistant_answer,
)
from techcamp.assistant.application.ports import StreamToken, TokenUsage
from techcamp.assistant.domain.citations import Citation, CitationKind
from techcamp.assistant.domain.models import Message, MessageEvidence, MessageRole

pytestmark = pytest.mark.anyio

CONVERSATION_ID = UUID("77777777-7777-7777-7777-777777777777")
ANSWER_ID = UUID("88888888-8888-8888-8888-888888888888")
DEEP_SEEK = "deepseek-chat"
SEMINAR = "seminar-local"
_PRICES: PriceTable = {
    SEMINAR: ModelPrice.local(SEMINAR),
    DEEP_SEEK: ModelPrice(
        model=DEEP_SEEK,
        input_cop_per_million=Decimal("5600"),
        output_cop_per_million=Decimal("16800"),
    ),
}
"""The profile's rate card, as the wiring would supply it: the seminar stand-in
bills nothing and the provider is priced. `deepseek-chat` at 3.500 input and 400
output tokens costs 3500×5600/1e6 + 400×16800/1e6 = 19.600 + 6.720 = 26.320 COP."""


class BoomLLMClient:
    """An `LLMClient` that yields one token and then fails, the way a provider
    connection drops mid-answer."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, messages: object) -> AsyncIterator[StreamToken]:
        self.calls += 1
        yield StreamToken(text="La parcela ")
        raise TimeoutError("connection reset by peer")


class UnmeteredLLMClient:
    """An `LLMClient` whose last token carries **no** `usage` and **no** `model`,
    which the port says is where the evidence of the call lives."""

    async def stream(self, messages: object) -> AsyncIterator[StreamToken]:
        yield StreamToken(text="Una respuesta")
        yield StreamToken(text=" sin contabilidad.")


def _citation() -> Citation:
    return Citation(
        source="Boletín Agroclimático Nacional",
        title="Boletín Agroclimático Nacional",
        kind=CitationKind.AGROCLIMATIC_BULLETIN,
        source_url="https://example.example/boletin",
        published_on=datetime(2026, 9, 30, tzinfo=UTC).date(),
        similarity=0.83,
    )


def _message(*, evidence: MessageEvidence) -> Message:
    return Message(
        id=ANSWER_ID,
        conversation_id=CONVERSATION_ID,
        role=MessageRole.ASSISTANT,
        content="La parcela necesita 12,5 mm de riego.",
        created_at=datetime(2026, 10, 7, 15, 0, tzinfo=UTC),
        evidence=evidence,
    )


def _persist(
    *, evidence: MessageEvidence | None = None, events: list[str] | None = None
) -> Callable[[MessageEvidence, str], Awaitable[Message]]:
    """A `persist` double that records the order in which the body called it."""
    recorded = events if events is not None else []

    async def persist(received: MessageEvidence, text: str) -> Message:
        recorded.append("persist")
        return _message(evidence=received)

    return persist


async def _collect(body: AsyncIterator[bytes]) -> list[dict[str, object]]:
    """The body consumed, one entry per SSE block, with its `data` parsed."""
    blocks: list[dict[str, object]] = []
    buffer = ""
    async for chunk in body:
        buffer += chunk.decode()
        while "\n\n" in buffer:
            block, buffer = buffer.split("\n\n", 1)
            fields: dict[str, object] = {}
            data_lines: list[str] = []
            for line in block.split("\n"):
                name, _, value = line.partition(":")
                value = value.removeprefix(" ")
                if name == "data":
                    data_lines.append(value)
                else:
                    fields[name] = value
            fields["data"] = json.loads("\n".join(data_lines))
            blocks.append(fields)
    return blocks


# --- the frames -----------------------------------------------------------


async def test_the_tokens_arrive_as_token_frames_in_order() -> None:
    llm = FakeLLMClient(
        tokens=["La parcela ", "necesita 12,5 mm ", "de riego."],
        model=SEMINAR,
        prompt_tokens=100,
        completion_tokens=10,
    )

    blocks = await _collect(
        stream_assistant_answer(
            llm=llm,
            prompt=(),
            citations=(_citation(),),
            prices=_PRICES,
            persist=_persist(
                evidence=MessageEvidence.from_call(
                    model=SEMINAR,
                    prompt_tokens=100,
                    completion_tokens=10,
                    cost_cop=Decimal(0),
                )
            ),
        )
    )

    tokens = [block for block in blocks if block["event"] == TOKEN_EVENT]
    assert [block["data"]["text"] for block in tokens] == [
        "La parcela ",
        "necesita 12,5 mm ",
        "de riego.",
    ]


async def test_the_cited_sources_travel_in_their_own_frame_with_their_provenance() -> None:
    """docs/04:227 — the stream carries "tokens + fuentes citadas", so the sources
    are structured data the client can render, not prose inside a token."""
    llm = FakeLLMClient(tokens=["Respuesta."], model=SEMINAR, prompt_tokens=1, completion_tokens=1)

    blocks = await _collect(
        stream_assistant_answer(
            llm=llm,
            prompt=(),
            citations=(_citation(),),
            prices=_PRICES,
            persist=_persist(evidence=MessageEvidence.no_llm()),
        )
    )

    sources = [block for block in blocks if block["event"] == SOURCES_EVENT]
    assert len(sources) == 1
    (citation,) = sources[0]["data"]["citations"]
    assert citation == {
        "source": "Boletín Agroclimático Nacional",
        "title": "Boletín Agroclimático Nacional",
        "kind": "agroclimatic_bulletin",
        "source_url": "https://example.example/boletin",
        "published_on": "2026-09-30",
        "similarity": 0.83,
    }


async def test_a_stream_without_citations_says_so_in_its_own_frame() -> None:
    """A question the corpus could not answer has no sources, and the client
    must be able to tell that from a truncated frame."""
    llm = FakeLLMClient(
        tokens=["No encontré información suficiente."],
        model=SEMINAR,
        prompt_tokens=1,
        completion_tokens=1,
    )

    blocks = await _collect(
        stream_assistant_answer(
            llm=llm,
            prompt=(),
            citations=(),
            prices=_PRICES,
            persist=_persist(evidence=MessageEvidence.no_llm()),
        )
    )

    sources = [block for block in blocks if block["event"] == SOURCES_EVENT]
    assert sources[0]["data"]["citations"] == []


async def test_the_frames_arrive_as_tokens_then_sources_then_done() -> None:
    """One fixed order, so a client can rely on it: the answer, then what it
    cites, then the persisted evidence that closes the turn."""
    llm = FakeLLMClient(tokens=["a", "b"], model=SEMINAR, prompt_tokens=10, completion_tokens=2)

    blocks = await _collect(
        stream_assistant_answer(
            llm=llm,
            prompt=(),
            citations=(_citation(),),
            prices=_PRICES,
            persist=_persist(
                evidence=MessageEvidence.from_call(
                    model=SEMINAR,
                    prompt_tokens=10,
                    completion_tokens=2,
                    cost_cop=Decimal(0),
                )
            ),
        )
    )

    assert [block["event"] for block in blocks] == [
        TOKEN_EVENT,
        TOKEN_EVENT,
        SOURCES_EVENT,
        DONE_EVENT,
    ]


async def test_no_frame_carries_an_id_because_this_stream_has_no_replay() -> None:
    """ADR-0027: the client's read does not reconnect, and the stream is the body
    of its own request. An `id:` line would promise a resumption this endpoint
    does not implement."""
    llm = FakeLLMClient(tokens=["Respuesta."], model=SEMINAR, prompt_tokens=1, completion_tokens=1)

    raw = b""
    async for chunk in stream_assistant_answer(
        llm=llm,
        prompt=(),
        citations=(),
        prices=_PRICES,
        persist=_persist(
            evidence=MessageEvidence.from_call(
                model=SEMINAR,
                prompt_tokens=1,
                completion_tokens=1,
                cost_cop=Decimal(0),
            )
        ),
    ):
        raw += chunk

    assert b"id:" not in raw
    assert raw.startswith(f"event: {TOKEN_EVENT}\ndata: ".encode())


async def test_done_carries_the_persisted_message_and_its_evidence() -> None:
    """D-T12.1: the model, the tokens and the cost of the turn, as they were
    written — and `message_id` of the row the write produced.

    `deepseek-chat` is the answering model here, so the cost is the one its rate
    card produces (26.320 COP) rather than the zero of the local stand-in: the
    cost belongs to the model that answered, not to the profile that asked.
    """
    llm = FakeLLMClient(
        tokens=["Respuesta."], model=DEEP_SEEK, prompt_tokens=3500, completion_tokens=400
    )

    blocks = await _collect(
        stream_assistant_answer(
            llm=llm,
            prompt=(),
            citations=(),
            prices=_PRICES,
            persist=_persist(
                evidence=MessageEvidence.from_call(
                    model=DEEP_SEEK,
                    prompt_tokens=3500,
                    completion_tokens=400,
                    cost_cop=Decimal("26.320000"),
                )
            ),
        )
    )

    done = [block for block in blocks if block["event"] == DONE_EVENT][0]
    assert done["data"] == {
        "conversation_id": str(CONVERSATION_ID),
        "message_id": str(ANSWER_ID),
        "model": DEEP_SEEK,
        "prompt_tokens": 3500,
        "completion_tokens": 400,
        "cost_cop": "26.320000",
    }


async def test_an_answer_from_a_model_the_table_does_not_price_is_not_persisted() -> None:
    """A wiring gap, not a free call: an unpriced model would store a `cost_cop` of
    zero that no measurement supports, and `SUM(message.cost_cop)` would report it
    as spend (D-T12.1, docs/11:132)."""
    events: list[str] = []
    llm = FakeLLMClient(
        tokens=["Respuesta."], model="otro-modelo", prompt_tokens=1, completion_tokens=1
    )

    blocks = await _collect(
        stream_assistant_answer(
            llm=llm,
            prompt=(),
            citations=(),
            prices=_PRICES,
            persist=_persist(evidence=MessageEvidence.no_llm(), events=events),
        )
    )

    assert events == []
    assert [block["event"] for block in blocks] == [TOKEN_EVENT, ERROR_EVENT]
    assert "otro-modelo" in blocks[-1]["data"]["detail"]


# --- the session discipline ------------------------------------------------


async def test_the_persist_happens_after_the_last_token_was_yielded() -> None:
    """D-T12.4 / `R3-stream-holds-db-session`: the write happens **around** the
    stream, not during it. A body that persisted between two tokens would hold
    the session — and its pooled connection — for as long as the producer takes
    to read the answer."""
    events: list[str] = []
    llm = FakeLLMClient(
        tokens=["uno", "dos", "tres"],
        model=SEMINAR,
        prompt_tokens=1,
        completion_tokens=3,
    )

    body = stream_assistant_answer(
        llm=llm,
        prompt=(),
        citations=(_citation(),),
        prices=_PRICES,
        persist=_persist(
            evidence=MessageEvidence.from_call(
                model=SEMINAR,
                prompt_tokens=1,
                completion_tokens=3,
                cost_cop=Decimal(0),
            ),
            events=events,
        ),
    )

    blocks: list[dict[str, object]] = []
    async for chunk in body:
        events.append("yield")
        blocks.append({"raw": chunk.decode()})

    assert events.count("persist") == 1
    # The single persist call lands after every chunk the body produced except the
    # two closing frames, which follow it.
    assert events.index("persist") == len(events) - 3


async def test_the_answer_text_persisted_is_the_whole_streamed_answer() -> None:
    """What the producer read is what the transcript holds — including a token
    boundary in the middle of a word."""
    llm = FakeLLMClient(
        tokens=["El suelo ", "está seco."],
        model=SEMINAR,
        prompt_tokens=1,
        completion_tokens=2,
    )
    seen: list[str] = []

    async def persist(evidence: MessageEvidence, text: str) -> Message:
        seen.append(text)
        return _message(evidence=evidence)

    async for _ in stream_assistant_answer(
        llm=llm, prompt=(), citations=(), prices=_PRICES, persist=persist
    ):
        pass

    assert seen == ["El suelo está seco."]


# --- what the provider does wrong -----------------------------------------


async def test_a_provider_that_fails_mid_stream_ends_with_an_error_frame() -> None:
    """Once the body has started the status code cannot change, so the client
    learns the turn failed from the body: an `error` frame and no `done`."""
    llm = BoomLLMClient()

    blocks = await _collect(
        stream_assistant_answer(
            llm=llm,
            prompt=(),
            citations=(_citation(),),
            prices=_PRICES,
            persist=_persist(evidence=MessageEvidence.no_llm()),
        )
    )

    assert [block["event"] for block in blocks] == [TOKEN_EVENT, ERROR_EVENT]
    assert DONE_EVENT not in [block["event"] for block in blocks]


async def test_nothing_is_persisted_when_the_provider_fails_mid_stream() -> None:
    """A half answer stored as if it were complete is worse than no answer: the
    transcript would claim the system said something it did not finish saying."""
    events: list[str] = []

    async for _ in stream_assistant_answer(
        llm=BoomLLMClient(),
        prompt=(),
        citations=(),
        prices=_PRICES,
        persist=_persist(evidence=MessageEvidence.no_llm(), events=events),
    ):
        pass

    assert events == []


async def test_a_stream_without_usage_persists_nothing_and_says_why() -> None:
    """D-T12.1's all-or-nothing evidence: a provider that never reported `usage`
    cannot have tokens and a cost written for it, and `0` would be a figure the
    system never measured."""
    events: list[str] = []

    blocks = await _collect(
        stream_assistant_answer(
            llm=UnmeteredLLMClient(),
            prompt=(),
            citations=(),
            prices=_PRICES,
            persist=_persist(evidence=MessageEvidence.no_llm(), events=events),
        )
    )

    assert events == []
    assert [block["event"] for block in blocks] == [TOKEN_EVENT, TOKEN_EVENT, ERROR_EVENT]
    assert "uso" in blocks[-1]["data"]["detail"]


async def test_a_write_that_fails_ends_the_stream_with_an_error_frame() -> None:
    """The database is the last thing that can fail, and it fails *after* the
    status code is already sent: the client learns from the body. Nothing is
    closed with `done`, because nothing was stored."""
    llm = FakeLLMClient(tokens=["Respuesta."], model=SEMINAR, prompt_tokens=1, completion_tokens=1)

    async def failing_persist(evidence: MessageEvidence, text: str) -> Message:
        raise OSError("connection reset while writing the answer")

    blocks = await _collect(
        stream_assistant_answer(
            llm=llm,
            prompt=(),
            citations=(_citation(),),
            prices=_PRICES,
            persist=failing_persist,
        )
    )

    assert [block["event"] for block in blocks] == [TOKEN_EVENT, ERROR_EVENT]
    assert DONE_EVENT not in [block["event"] for block in blocks]


# --- the cost --------------------------------------------------------------


def test_the_cost_is_the_two_rates_over_a_million_tokens() -> None:
    """docs/02:78's formula, `costo = tokens × precio_por_millón`, applied to the
    two rates the provider publishes."""
    price = ModelPrice(
        model=DEEP_SEEK, input_cop_per_million=Decimal("280"), output_cop_per_million=Decimal("420")
    )
    usage = TokenUsage(prompt_tokens=3500, completion_tokens=400)

    assert cost_cop(usage, price) == Decimal("1.148000")


def test_a_call_that_came_free_costs_zero_and_says_so() -> None:
    """D-T12.1 accepts `cost_cop = 0` for a call that happened to be free, and the
    seminar stand-in is exactly that case: a local model has no bill."""
    usage = TokenUsage(prompt_tokens=3500, completion_tokens=400)

    assert cost_cop(usage, ModelPrice.local(SEMINAR)) == Decimal("0.000000")


def test_the_cost_is_quantized_to_the_column() -> None:
    """`message.cost_cop` is `numeric(12,6)` (docs/03 §Costo y tiempo): the value
    is rounded once, here, so a stored cost is the number this function returned
    and not a different rounding of it."""
    price = ModelPrice(
        model=DEEP_SEEK, input_cop_per_million=Decimal("1"), output_cop_per_million=Decimal("1")
    )
    usage = TokenUsage(prompt_tokens=1, completion_tokens=1)

    assert cost_cop(usage, price) == Decimal("0.000002")


def test_a_model_with_no_price_is_a_wiring_error_and_never_a_free_call() -> None:
    """ADR-0026 states docs/02:78's prices are obsolete and T8 writes the real
    ones, so the table is filled per profile. A model missing from it must fail
    loudly: a `0` here would be an unmeasured cost presented as a free call."""
    table: PriceTable = {SEMINAR: ModelPrice.local(SEMINAR)}

    with pytest.raises(UnknownModelPriceError):
        price_for(table, DEEP_SEEK)

    assert price_for(table, SEMINAR).input_cop_per_million == Decimal(0)


def test_a_price_table_is_the_wiring_own_and_not_a_literal_in_the_body() -> None:
    """The body takes the evidence, never a price: the price is resolved by the
    wiring that knows the profile (D-T12.3), and the body only multiplies."""
    import inspect

    signature = inspect.signature(cost_cop)

    assert list(signature.parameters) == ["usage", "price"]
