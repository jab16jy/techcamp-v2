"""The `LLMClient` adapter for DeepSeek (E12 T6; ADR-0026; ADR-0007:14; H2).

ADR-0026 pinned `deepseek-chat` as the provisional provider and ADR-0028 renames
it to `deepseek-flash` — the API retired the alias on 2026-07-24 — and records
why the endpoint stands: the API
is OpenAI-compatible, so `POST /chat/completions` with `stream: true` speaks
`Authorization: Bearer`, one `data:` line per chunk, a `delta.content` piece per
chunk and `data: [DONE]` to close — and the **last** chunk carries `usage`, which
is where `message.cost_cop` comes from (docs/06 §9, D-T12.1). All of that was
verified against the provider's own documentation with ctx7 on 2026-10-08, not
recalled.

**One call per request, and nothing else** (ADR-0007:26, H2). The adapter is a
single `async for` over the response's SSE lines: it yields one `StreamToken` per
content delta and puts the `usage` and the `model` on the last one, exactly as the
port promises. There is no tool loop, no framework and no second round trip,
because there is no code here that could start one.

**The HTTP call is behind a transport protocol** for the same reason every other
external I/O in this module is behind a port: the provider is the one thing a test
must not reach. `ChatCompletionTransport` is implemented by `httpx` in production
and by a double in tests, and it is the whole of the adapter's I/O — the parsing
below is pure over the lines the transport hands back.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from techcamp.assistant.application.ports import StreamToken, TokenUsage
from techcamp.assistant.domain.models import ChatMessage
from techcamp.shared.config import LLM_API_KEY_ENV, llm_api_key, llm_base_url, llm_model


class LLMUnavailableError(Exception):
    """The provider could not answer, or answered something unreadable.

    Raised rather than turned into an empty stream: `stream_assistant_answer`
    treats a failed call as a turn that persists nothing, and a stream that ended
    without a single token but without an error would look like an answer the
    producer should read.
    """


class ChatCompletionTransport(Protocol):
    """The one HTTP call the adapter makes: POST the request, read the SSE lines.

    Returns the `data:` payloads in order, with the `[DONE]` sentinel **removed**,
    so the adapter parses chunks and never sentinels. An implementation that
    cannot reach the provider raises, and the adapter lets that propagate as
    `LLMUnavailableError`.

    Declared the same way as `LLMClient.stream` — a plain method that *returns* an
    async iterator — because an adapter implements it as an async generator:
    calling it opens the stream, and `async for` drains it.
    """

    def chat_completion_lines(
        self, *, url: str, api_key: str, model: str, messages: Sequence[ChatMessage]
    ) -> AsyncIterator[str]:
        """The JSON `data:` payloads of one streamed chat completion."""
        ...


class DeepSeekLLMClient:
    """DeepSeek's chat completions, behind the `LLMClient` port (ADR-0026)."""

    def __init__(self, transport: ChatCompletionTransport, *, model: str | None = None) -> None:
        self._transport = transport
        self._model = model if model is not None else llm_model()

    @property
    def model(self) -> str:
        """The model this client asks for, which is also what it records.

        Read back for the wiring test that an operator's `TECHCAMP_LLM_MODEL` is
        the model the answer will be stamped with, not a name chosen here.
        """
        return self._model

    async def stream(self, messages: Sequence[ChatMessage]) -> AsyncIterator[StreamToken]:
        """The tokens of one answer, the last one carrying `usage` and `model`.

        The key is read here and not in the constructor, so a deployment that starts
        without the variable fails on its first question — naming the variable — and
        not at import time.
        """
        # Resolved per call and not in the constructor: a deployment that starts
        # without the variable fails on its first question, with a message naming
        # the variable, rather than never being able to build the client at all.
        api_key = llm_api_key()
        if not api_key:
            raise LLMUnavailableError(f"no provider key is configured (set {LLM_API_KEY_ENV})")

        answer: list[str] = []
        usage: TokenUsage | None = None
        # The model recorded is the last one the provider **named**, falling back to
        # the configured alias: DeepSeek may send `model` only on the opening chunk,
        # which are the chunks `_delta_text` reads as text and no further, and a
        # `None` here would leave the transcript row silent about its own source.
        recordable_model = self._model
        try:
            lines = self._transport.chat_completion_lines(
                url=llm_base_url(),
                api_key=api_key,
                model=self._model,
                messages=messages,
            )
            async for payload in lines:
                chunk = json.loads(payload)
                named_model = chunk.get("model") if isinstance(chunk, dict) else None
                if isinstance(named_model, str) and named_model:
                    recordable_model = named_model
                delta = _delta_text(chunk)
                if delta:
                    answer.append(delta)
                    yield StreamToken(text=delta)
                chunk_usage = _usage_of(chunk)
                if chunk_usage is not None:
                    usage = chunk_usage
        except LLMUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 - the port sees one failure shape
            raise LLMUnavailableError("the provider's stream could not be read") from exc

        if not answer:
            raise LLMUnavailableError("the provider's stream carried no answer")

        # The port promises the last token carries the usage and the model, and
        # `stream_assistant_answer` refuses to persist an answer without them
        # (D-T12.1). Re-emitting the last token with them attached is how the
        # adapter keeps that promise for a provider that sends them in a separate
        # chunk, which DeepSeek does: the final chunk has `finish_reason` and
        # `usage`, and an empty `delta.content`.
        yield StreamToken(
            text="",
            usage=usage or TokenUsage(prompt_tokens=0, completion_tokens=0),
            model=recordable_model,
        )


def _delta_text(chunk: object) -> str:
    """The `delta.content` of one chunk, or `""` when it carries none.

    A chunk without `choices` is the final one DeepSeek sends (usage and
    `finish_reason`), and the first one of a stream carries the role with an empty
    content. Both are read as "no text", never as a token.
    """
    choices = chunk.get("choices") if isinstance(chunk, dict) else None
    if not choices:
        return ""
    delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
    content = delta.get("content") if isinstance(delta, dict) else None
    return content if isinstance(content, str) else ""


def _usage_of(chunk: object) -> TokenUsage | None:
    """The `usage` of one chunk, or `None` for every chunk that carries none."""
    usage = chunk.get("usage") if isinstance(chunk, dict) else None
    if not isinstance(usage, dict):
        return None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if not isinstance(prompt, int) or not isinstance(completion, int):
        return None
    return TokenUsage(prompt_tokens=prompt, completion_tokens=completion)
