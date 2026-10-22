"""How the assistant's endpoints get the two ports that are not a database
(E12 T5; ADR-0026; ADR-0021; ADR-0007:14; D-T12.3).

`SqlAlchemyKnowledgeRepository` and the transcript repositories need a session,
so they are built **inside** the route's own short-lived one (D-T12.4). What is
left for FastAPI to resolve are the three things that do not: the `LLMClient`,
the `Embedder` and the price table.

**Nothing here talks to a provider, and that is deliberate.** ADR-0026 pins
`deepseek-chat` as the provisional provider and says its real prices are T8's to
write into docs/02:78; the API keys belong to `shared/config.py`, which is T6's
surface (the feature doc's T6 row). So this task wires the *seam*: the seminar
profile gets deterministic local stand-ins, exactly as `telemetry/adapters/jobs.py`
already does with `SeminarHashEmbedder` for the corpus, and the production
profile answers `503` with a title that says the provider is not configured.

`SeminarHashEmbedder` is **reused**, not re-implemented: the corpus vectors were
built with it, so a question embedded by a different embedder would be compared
against a direction the index never saw, and every retrieval would miss. That is
the reason the seminar's answers cite the fixtures and not the field.

The stand-in also reports `usage` and a model on its last token, because the port
promises it (`StreamToken`) and the body refuses to persist an answer whose cost
it cannot compute (D-T12.1).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal
from typing import Annotated

import httpx
from fastapi import Depends

from techcamp.assistant.adapters.api.deepseek import DeepSeekLLMClient, LLMUnavailableError
from techcamp.assistant.adapters.api.pricing import ModelPrice, PriceTable
from techcamp.assistant.adapters.jobs import SeminarHashEmbedder
from techcamp.assistant.application.ports import Embedder, LLMClient, StreamToken, TokenUsage
from techcamp.assistant.domain.models import ChatMessage
from techcamp.shared.config import (
    is_seminar_profile,
    llm_api_key,
    llm_base_url,
    llm_budget_cap_cop,
)
from techcamp.shared.errors import ProblemError

_TIMEOUT = 60.0
"""Seconds for one socket operation of the stream. It bounds a stalled read, not the
answer: `httpx` applies it per operation, so a provider that keeps sending a byte
before each timeout never trips it."""

_STREAM_DEADLINE = 60.0
"""Seconds for the **whole** stream, on the monotonic clock.

`_TIMEOUT` alone does not bound a request (R1-llm-stream-has-no-whole-stream-deadline):
a provider that drips bytes satisfies every socket timeout while holding the
connection, the worker and the producer's open stream well past a minute. The
endpoint has somebody waiting on the other end, so the body is cancelled at this
wall-clock deadline and the turn ends in an `error` frame instead of a slow drip."""


class HttpChatCompletionTransport:
    """`ChatCompletionTransport` over `httpx`, the client the repo already uses.

    `httpx.AsyncClient.stream` with an injectable transport is what the Open-Meteo
    clients do (`weather/adapters/open_meteo.py`), so reading DeepSeek's SSE is the
    same shape as an HTTP dependency this repository already runs — and the same
    seam is what lets a test drive it without a provider.

    The `data:` lines are the transport's whole output: the `[DONE]` sentinel is
    dropped **here**, so the adapter parses chunks and never sentinels, and the
    bearer key is read from `shared.config` at the call rather than bound into the
    object.
    """

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        stream_deadline: float = _STREAM_DEADLINE,
    ) -> None:
        self._transport = transport
        self._stream_deadline = stream_deadline

    def chat_completion_lines(
        self, *, url: str, api_key: str, model: str, messages: Sequence[ChatMessage]
    ) -> AsyncIterator[str]:
        return self._stream(url=url, api_key=api_key, model=model, messages=messages)

    async def _stream(
        self, *, url: str, api_key: str, model: str, messages: Sequence[ChatMessage]
    ) -> AsyncIterator[str]:
        payload = {
            "model": model,
            "messages": [
                {"role": message.role.value, "content": message.content} for message in messages
            ],
            "stream": True,
            # DeepSeek bills prompt and completion separately (docs/02:78's
            # formula) and puts both counts on the last chunk; this option is what
            # makes it send that chunk at all.
            "stream_options": {"include_usage": True},
        }
        try:
            async with asyncio.timeout(self._stream_deadline):
                async with httpx.AsyncClient(timeout=_TIMEOUT, transport=self._transport) as client:
                    async with client.stream(
                        "POST",
                        f"{url.rstrip('/')}/chat/completions",
                        json=payload,
                        headers={"Authorization": f"Bearer {api_key}"},
                    ) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[len("data:") :].strip()
                            if not data or data == "[DONE]":
                                continue
                            yield data
        except TimeoutError as error:
            # The deadline is on the whole stream, so a body that delivered a chunk
            # and then dripped is cancelled here — and reported as the provider
            # failure it is, never as an answer that simply ended.
            raise LLMUnavailableError(
                f"the provider's stream did not finish within {self._stream_deadline:.0f}s"
            ) from error


def _httpx_transport() -> HttpChatCompletionTransport:
    return HttpChatCompletionTransport()


SEMINAR_MODEL = "seminar-local"
"""The stand-in's model name, deliberately **not** a provider's: an answer in the
transcript has to say which model produced it (D-T12.1), and a name a deployment
could be mistaken for `deepseek-chat` would be a false record."""

SEMINAR_ANSWER = (
    "En el perfil seminario el asistente no llama a ningún proveedor: esta respuesta la "
    "produce el modelo local del seminario. Los datos de la parcela, las fuentes citadas y "
    "la recomendación de riego quedan arriba, en los hechos y en las fuentes de la respuesta."
)
"""What the stand-in says, in plain Spanish and in the glossary's vocabulary.

It says **what it is** instead of pretending to explain the plot: ADR-0021 makes
the seminar profile local by design, and an answer that read like a model
reading real data would be a claim the system cannot support in that profile."""

_CHARS_PER_TOKEN = 4
"""A rough, deterministic length for the stand-in's own accounting: ~4 characters
per token is the usual English/Spanish average and it only has to be stable, since
a stand-in that reports a different number per call would make its own
`cost_cop` — which is zero — the only constant thing about it."""

_STAND_IN_CHUNK = 24
"""How many characters each emitted token carries, so the stand-in streams
several frames instead of one (which is what a client reads)."""


class SeminarLLMClient:
    """The seminar profile's `LLMClient`: local, deterministic, no network.

    One call per request, like any `LLMClient` (ADR-0007:26), and the whole point
    is that it *cannot* reach a provider: the class holds no client, no key and no
    URL. A seminar answer is therefore always reproducible, which is what
    ADR-0021 wants from the profile.
    """

    async def stream(self, messages: Sequence[ChatMessage]) -> AsyncIterator[StreamToken]:
        prompt_chars = sum(len(message.content) for message in messages)
        usage = TokenUsage(
            prompt_tokens=prompt_chars // _CHARS_PER_TOKEN,
            completion_tokens=len(SEMINAR_ANSWER) // _CHARS_PER_TOKEN,
        )
        parts = [
            SEMINAR_ANSWER[index : index + _STAND_IN_CHUNK]
            for index in range(0, len(SEMINAR_ANSWER), _STAND_IN_CHUNK)
        ]
        for index, text in enumerate(parts):
            is_last = index == len(parts) - 1
            yield StreamToken(
                text=text,
                usage=usage if is_last else None,
                model=SEMINAR_MODEL if is_last else None,
            )


def get_llm_client() -> LLMClient:
    """The `LLMClient` behind the port.

    The seminar stand-in in the seminar profile — local, deterministic, no network
    (ADR-0021). In production, DeepSeek behind the port (ADR-0026) as soon as the
    key exists, and `503` until it does: an assistant that answered without a
    provider key would either fail on every question or, worse, answer from a
    stand-in while the deployment believed it was talking to the provider.

    The endpoint is validated too (`shared.config.llm_base_url`), and a refused one
    is the same `503` shape: the client sends `Authorization: Bearer <key>` to
    whatever host that variable names, so a typo or a redirected variable is
    refused here, before a client exists, rather than discovered by the host
    receiving the credential.
    """
    if is_seminar_profile():
        return SeminarLLMClient()
    if llm_api_key() is None:
        raise ProblemError(
            status=503,
            title="Assistant provider not configured",
            detail=(
                "No provider key is configured: set TECHCAMP_LLM_API_KEY "
                "(shared.config), or run the seminar profile to answer locally."
            ),
        )
    try:
        llm_base_url()
    except ValueError as error:
        raise ProblemError(
            status=503,
            title="Assistant provider endpoint refused",
            detail=str(error),
        ) from error
    return DeepSeekLLMClient(_httpx_transport())


def get_embedder() -> Embedder:
    """The `Embedder` behind the port.

    `SeminarHashEmbedder`, the very embedder T3's ingestion indexed the corpus
    with: a question embedded by anything else would be compared against vectors
    the HNSW index never saw.
    """
    if is_seminar_profile():
        return SeminarHashEmbedder()
    raise ProblemError(
        status=503,
        title="Assistant embedder not configured",
        detail="The production embedding model is wired in E12 T6; the seminar profile is local.",
    )


def get_prices() -> PriceTable:
    """The rate card of the profile (D-T12.3; `adapters.api.pricing`).

    The seminar stand-in bills nothing, and a local model genuinely costs zero
    pesos — which D-T12.1 accepts for a call that came free. The production card
    is T6's to fill with ADR-0026's real prices, and T8's to confirm in docs/02.
    """
    if is_seminar_profile():
        return {SEMINAR_MODEL: ModelPrice.local(SEMINAR_MODEL)}
    raise ProblemError(
        status=503,
        title="Assistant prices not configured",
        detail="The production rate card is wired in E12 T6 with the provider keys.",
    )


def get_budget_cap() -> Decimal | None:
    """The profile's monthly cap in COP, or `None` when the profile does not measure money.

    The rule lives in `shared.config.llm_budget_cap_cop()`, where every environment
    variable of the repository lives: `seminar` answers `None` because its rate
    card is zero and it never reaches a provider (ADR-0021); `production` answers
    the configured figure, or `0` when unset, which stops the model instead of
    answering for free (D-T6.1, D-T12.3). A malformed figure is a configuration
    error, which this dependency reports as `503` before any model call.
    """
    try:
        return llm_budget_cap_cop()
    except ValueError as error:
        raise ProblemError(
            status=503,
            title="Assistant budget cap misconfigured",
            detail=str(error),
        ) from error


LLMClientDep = Annotated[LLMClient, Depends(get_llm_client)]
EmbedderDep = Annotated[Embedder, Depends(get_embedder)]
PricesDep = Annotated[PriceTable, Depends(get_prices)]
BudgetCapDep = Annotated[Decimal | None, Depends(get_budget_cap)]
