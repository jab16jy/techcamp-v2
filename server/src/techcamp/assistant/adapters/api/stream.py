"""The assistant's SSE body for `POST /assistant/messages` (E12 T5; D-T12.4;
ADR-0027; docs/04-api.md §Riesgo, métricas y asistente; ADR-0026; D-T12.1).

docs/04:227 answers `text/event-stream` with "tokens + fuentes citadas". This
module is that body, and the frame names below are the contract the web client
of T7 reads with `fetch` + `ReadableStream` + `createSseParser`
(`web/src/lib/api/sse.ts`), **never** with `EventSource` — which cannot `POST`
nor send `Authorization` (ADR-0027).

**Three frames, one order.** `token` while the provider answers, then `sources`
with the citations retrieval resolved, then `done` with the identifiers and the
evidence of the turn. `done` is last because it is emitted **after** the write:
a client that stores `done.message_id` stores the id of a row that exists.

**The degraded body is the same stream without a provider** (T6; docs/06 §9).
When the day's limit or the month's budget is exhausted, `answer` replaces the
`token` frames — one frame with the whole text, `degraded: true`, the `reason`
and the sentence the producer reads — and `sources` and `done` follow in the same
order. The `done` evidence is all `null`: `model IS NULL` is what distinguishes
"the LLM was never called" from "it was called and cost nothing" (D-T12.1).

**No database session is held here** (D-T12.4). That is the whole of
`R3-stream-holds-db-session`, the bug recorded in
`telemetry/adapters/api/router.py:446-485`: a `yield`-scoped session is torn
down only when the response finishes, so a session alive across tokens keeps its
pooled connection checked out for as long as the client reads. This body holds
no session at all — it calls a `persist` callable that opens and closes its own
one, and it calls it **after** the last token has been yielded.

**No `id:` and no keepalive.** This stream has no replay: ADR-0027 records that
the client does not reconnect and that a half-read answer is lost, which is
acceptable for one turn. A keepalive would be a lie about a body that finishes in
seconds.

**What a failed turn looks like.** Once the body has started, the status code is
already sent, so a failure ends the stream with an `error` frame and **no**
`done`. A provider that fails mid-answer persists nothing: a half answer stored
as if it were complete is a transcript claiming the system said something it did
not finish saying. The same holds for a stream whose last token carried no
`usage`: D-T12.1's evidence moves all four columns together, and inventing the
tokens or the cost of an unaccounted call would write a figure the system never
measured.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from decimal import Decimal
from typing import Any

from techcamp.assistant.adapters.api.pricing import (
    PriceTable,
    UnknownModelPriceError,
    cost_cop,
    price_for,
)
from techcamp.assistant.application.ports import LLMClient, StreamToken
from techcamp.assistant.domain.citations import Citation
from techcamp.assistant.domain.models import ChatMessage, MessageEvidence

LOGGER = logging.getLogger(__name__)

TOKEN_EVENT = "token"
"""One piece of the answer, as the provider streamed it. The client concatenates
them in order; a token boundary in the middle of a word is not a word boundary."""

ANSWER_EVENT = "answer"
"""The whole answer without a model, in one frame: `text`, `degraded: true`, the
`reason` and the sentence a producer reads. It replaces the `token` frames of an
answered turn; `sources` and `done` follow in the same order either way."""

SOURCES_EVENT = "sources"
"""The cited sources, as data, after the last token (docs/04:227). Empty when the
corpus retrieved nothing, which the client shows as "no sources" rather than as
a truncated frame."""

DONE_EVENT = "done"
"""The turn's persisted evidence: `conversation_id`, `message_id`, the model, the
two token counts and the cost (D-T12.1)."""

ERROR_EVENT = "error"
"""The turn failed. Terminal: no `done` follows, so a client that reads `done` as
completion never mistakes a truncated answer for a finished one."""

_USAGE_MISSING = (
    "el proveedor no devolvió el uso de la llamada, así que no hay tokens ni costo que registrar"
)

PersistAnswer = Callable[[MessageEvidence, str], Awaitable[Any]]
"""How the body stores the finished answer: given the evidence of the call and
the whole answer, return the stored message.

A callable rather than a session or a repository, and that is the point of
D-T12.4: the body cannot hold a session even if it wanted to, so the only way
in is a call the wiring implements by opening and closing its own."""


async def stream_assistant_answer(
    *,
    llm: LLMClient,
    prompt: Sequence[ChatMessage],
    citations: Sequence[Citation],
    prices: PriceTable,
    persist: PersistAnswer,
) -> AsyncIterator[bytes]:
    """The whole body of one assistant answer.

    `llm.stream` is called **once** (ADR-0007:26 — one call, no tool loop, no
    framework), its tokens are yielded as they arrive, and the answer is written
    after the stream ends.

    `prices` is the profile's rate card rather than a resolved price, because the
    model that answered is only known **after** the stream: the cost of a turn is
    the price of the model the provider says answered it (ADR-0026 — the `usage`
    of the last frame is the source of `message.cost_cop`).
    """
    answer: list[str] = []
    usage: StreamToken | None = None
    try:
        async for token in llm.stream(prompt):
            if token.text:
                answer.append(token.text)
                yield _frame(TOKEN_EVENT, {"text": token.text})
            if token.usage is not None or token.model is not None:
                usage = token
    except Exception as exc:  # noqa: BLE001 - the frame is the report
        LOGGER.warning("assistant stream failed before the answer was complete", exc_info=exc)
        yield _frame(ERROR_EVENT, {"detail": "no se pudo completar la respuesta del asistente"})
        return

    try:
        evidence = _evidence_from(usage, prices)
    except UnknownModelPriceError as exc:
        LOGGER.warning("assistant stream ended with an unpriced model", exc_info=exc)
        yield _frame(
            ERROR_EVENT, {"detail": f"no hay precio configurado para el modelo: {exc.model}"}
        )
        return
    if evidence is None:
        LOGGER.warning("assistant stream ended without provider usage: nothing was persisted")
        yield _frame(ERROR_EVENT, {"detail": _USAGE_MISSING})
        return

    text = "".join(answer)
    try:
        stored = await persist(evidence, text)
    except Exception as exc:  # noqa: BLE001 - the frame is the report
        LOGGER.warning("assistant answer could not be stored", exc_info=exc)
        yield _frame(ERROR_EVENT, {"detail": "no se pudo guardar la respuesta del asistente"})
        return
    yield _frame(SOURCES_EVENT, {"citations": [_citation_view(c) for c in citations]})
    yield _frame(
        DONE_EVENT,
        {
            "conversation_id": str(stored.conversation_id),
            "message_id": str(stored.id),
            "model": evidence.model,
            "prompt_tokens": evidence.prompt_tokens,
            "completion_tokens": evidence.completion_tokens,
            "cost_cop": _decimal_text(evidence.cost_cop),
        },
    )


async def stream_degraded_answer(
    *,
    answer: str,
    citations: Sequence[Citation],
    reason: str | None,
    reason_text: str,
    persist: PersistAnswer,
) -> AsyncIterator[bytes]:
    """The whole body of one degraded answer (T6; docs/06 §9).

    The decision and the text are already made when this runs: the quota gate
    refused the call, `application.degraded_answer` wrote the same facts the model
    would have been given, and retrieval resolved the sources. There is no
    provider to wait for, so the body is **one** `answer` frame instead of a
    token stream, then the same `sources` and `done` frames as an answered turn.

    The answer is stored **before** the first frame with `MessageEvidence.no_llm()`:
    `model IS NULL` is what distinguishes "the LLM was never called" from "it was
    called and cost nothing" (D-T12.1), and a body that starts with a complete
    answer has no truncation for the client to reason about. A write that fails
    ends the stream with `error` and no `done`, exactly like the answered path.
    """
    evidence = MessageEvidence.no_llm()
    try:
        stored = await persist(evidence, answer)
    except Exception as exc:  # noqa: BLE001 - the frame is the report
        LOGGER.warning("assistant degraded answer could not be stored", exc_info=exc)
        yield _frame(ERROR_EVENT, {"detail": "no se pudo guardar la respuesta del asistente"})
        return
    yield _frame(
        ANSWER_EVENT,
        {
            "text": answer,
            "degraded": True,
            "reason": reason,
            "reason_text": reason_text,
        },
    )
    yield _frame(SOURCES_EVENT, {"citations": [_citation_view(c) for c in citations]})
    yield _frame(
        DONE_EVENT,
        {
            "conversation_id": str(stored.conversation_id),
            "message_id": str(stored.id),
            "model": evidence.model,
            "prompt_tokens": evidence.prompt_tokens,
            "completion_tokens": evidence.completion_tokens,
            "cost_cop": _decimal_text(evidence.cost_cop),
        },
    )


def _frame(event: str, data: dict[str, object]) -> bytes:
    """One SSE block: `event:`, `data:` and the blank line that ends it.

    No `id:` — see the module docstring. `ensure_ascii=False` because the answer
    is Spanish and the corpus is Spanish, and `\\u` escapes would make the wire
    unreadable for the producer's own logs.
    """
    payload = json.dumps(data, ensure_ascii=False, default=_json_default)
    return f"event: {event}\ndata: {payload}\n\n".encode()


def _citation_view(citation: Citation) -> dict[str, object]:
    """One citation as the client needs it: what it points at, and the evidence
    behind it, with every optional field honestly `null` when it does not exist
    (D-T12.1 — never `0` or `False` standing in for missing evidence)."""
    return {
        "source": citation.source,
        "title": citation.title,
        "kind": citation.kind.value if citation.kind is not None else None,
        "source_url": citation.source_url,
        "published_on": citation.published_on.isoformat() if citation.published_on else None,
        "similarity": citation.similarity,
    }


def _evidence_from(token: StreamToken | None, prices: PriceTable) -> MessageEvidence | None:
    """The evidence of the call, or `None` when the provider reported none.

    `MessageEvidence` refuses a half-filled shape (D-T12.1), so this returns
    `None` rather than building one out of what arrived: a model without `usage`,
    or `usage` without a model, is a provider that did not keep the port's
    promise and the answer's cost cannot be computed from what it sent.

    The cost comes from the price table, never from a literal
    (`adapters.api.pricing`), and a model the table does not know raises
    `UnknownModelPriceError` — which the caller turns into an `error` frame, so
    the turn is not stored with a cost nobody measured.
    """
    if token is None or token.usage is None or not token.model:
        return None
    price = price_for(prices, token.model)
    return MessageEvidence.from_call(
        model=token.model,
        prompt_tokens=token.usage.prompt_tokens,
        completion_tokens=token.usage.completion_tokens,
        cost_cop=cost_cop(token.usage, price),
    )


def _decimal_text(value: Decimal | None) -> str | None:
    """A `Decimal` as text, so `numeric(12,6)` survives the wire exactly; `None`
    stays `null` and never becomes `"0"`."""
    return None if value is None else str(value)


def _json_default(value: object) -> object:
    return str(value)
