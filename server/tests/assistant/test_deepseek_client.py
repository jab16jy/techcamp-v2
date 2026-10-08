"""The DeepSeek adapter (E12 T6; ADR-0026; ADR-0007:14,26; H1, H2).

The provider is external I/O, so it sits behind `ChatCompletionTransport` and the
tests use a double at that seam — the same rule the rest of this module applies to
every port. What is asserted here is the parsing, because that is the part that
turns a provider's own wire format into the `StreamToken`s the port promises:

* the `data:` lines are chunks, the `delta.content` of each is one token, and the
  `[DONE]` sentinel is not a token;
* the **last** chunk carries `usage` and `model` (DeepSeek sends it with an empty
  `delta.content` and `finish_reason`), and the port promises the last token holds
  them — which is where `message.cost_cop` comes from (D-T12.1);
* a provider that answers nothing is a failure, not an empty answer: a stream with
  no tokens would leave `stream_assistant_answer` with nothing to persist and no
  error to report.

The request shape itself is pinned too — one `stream: true` call with
`include_usage`, the bearer key and the model from `shared.config` — because an
adapter that asked for a non-streamed answer, or one that never asked for usage,
would silently produce turns with no cost.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest

from techcamp.assistant.adapters.api.deepseek import DeepSeekLLMClient, LLMUnavailableError
from techcamp.assistant.domain.models import ChatMessage, MessageRole
from techcamp.shared.config import llm_api_key, llm_model

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _provider_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A key for every test that is not about its absence.

    `llm_api_key()` has no committed default on purpose (a committed default for a
    provider credential would be a live key in the repository), so a test that
    exercises the stream has to configure one the same way a deployment does.
    """
    monkeypatch.setenv("TECHCAMP_LLM_API_KEY", "sk-test")


_MESSAGES = (ChatMessage(role=MessageRole.USER, content="¿Por qué no regué ayer?"),)


def _chunk(delta: str, *, finish_reason: str | None = None, usage: dict[str, int] | None = None):
    payload: dict[str, Any] = {
        "id": "1f633d8b",
        "model": llm_model(),
        "choices": [
            {
                "index": 0,
                "delta": {"content": delta, "role": "assistant"},
                "finish_reason": finish_reason,
            }
        ],
    }
    if usage is not None:
        payload["usage"] = usage
    return json.dumps(payload, ensure_ascii=False)


class _FakeTransport:
    """A transport that replays given `data:` lines and records the request.

    Recording is what lets the request shape be asserted without a provider: the
    adapter's whole job is to turn the profile's configuration into one HTTP call,
    and a call that had been made differently would never be visible otherwise.
    """

    def __init__(self, lines: Sequence[str], *, status_error: Exception | None = None) -> None:
        self._lines = list(lines)
        self._status_error = status_error
        self.calls: list[dict[str, Any]] = []

    def chat_completion_lines(
        self, *, url: str, api_key: str, model: str, messages: Sequence[ChatMessage]
    ) -> AsyncIterator[str]:
        self.calls.append(
            {
                "url": url,
                "api_key": api_key,
                "model": model,
                "messages": [
                    {"role": message.role.value, "content": message.content} for message in messages
                ],
            }
        )
        return self._replay()

    async def _replay(self) -> AsyncIterator[str]:
        if self._status_error is not None:
            raise self._status_error
        for line in self._lines:
            yield line


def _client(lines: Sequence[str] = (), **kwargs: Any) -> DeepSeekLLMClient:
    return DeepSeekLLMClient(_FakeTransport(lines, **kwargs), model=llm_model())


async def test_the_answer_arrives_one_token_per_delta() -> None:
    client = _client(
        [
            _chunk("La parcela "),
            _chunk("necesita 12,5 mm "),
            _chunk("de riego hoy."),
            _chunk("", finish_reason="stop"),
        ]
    )

    tokens = [token.text async for token in client.stream(_MESSAGES)]

    assert tokens == ["La parcela ", "necesita 12,5 mm ", "de riego hoy.", ""]


async def test_the_last_token_carries_the_usage_and_the_model() -> None:
    """The port's promise, and the source of `message.cost_cop`: the last token
    holds the counts the provider reported on its final chunk."""
    client = _client(
        [
            _chunk("La parcela "),
            _chunk("", finish_reason="stop", usage={"prompt_tokens": 17, "completion_tokens": 9}),
        ]
    )

    collected = [token async for token in client.stream(_MESSAGES)]

    assert collected[-1].usage is not None
    assert collected[-1].usage.prompt_tokens == 17
    assert collected[-1].usage.completion_tokens == 9
    assert collected[-1].model == llm_model()


async def test_the_http_transport_drops_the_done_sentinel() -> None:
    """The transport is what strips `data: [DONE]`, so the adapter parses chunks and
    never a sentinel: a `[DONE]` that reached the parser would be a JSON error in
    the middle of an answer. Driven with `httpx.MockTransport`, the same seam the
    Open-Meteo clients use."""
    import httpx

    from techcamp.assistant.adapters.api.deps import HttpChatCompletionTransport

    lines = [
        'data: {"choices": [{"delta": {"content": "La parcela "}}]}',
        'data: {"choices": [{"delta": {"content": ""}}], "usage": {"prompt_tokens": 17,'
        ' "completion_tokens": 9}}',
        "data: [DONE]",
    ]
    body = "".join(f"{line}\n\n" for line in lines)

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer sk-http"
        payload = json.loads(request.content)
        assert payload["stream"] is True
        assert payload["stream_options"] == {"include_usage": True}
        assert payload["model"] == "deepseek-chat"
        return httpx.Response(200, text=body)

    transport = HttpChatCompletionTransport(httpx.MockTransport(respond))

    payloads = [
        payload
        async for payload in transport.chat_completion_lines(
            url="https://example.invalid/v1/",
            api_key="sk-http",
            model="deepseek-chat",
            messages=(),
        )
    ]

    assert payloads == [lines[0].removeprefix("data: "), lines[1].removeprefix("data: ")]


async def test_a_stream_with_no_answer_is_a_failure() -> None:
    """A provider that closed the stream without a single token has not answered,
    and an empty answer would leave the turn with nothing to persist and no error
    frame to explain it."""
    client = _client([_chunk("", finish_reason="stop")])

    with pytest.raises(LLMUnavailableError):
        [token async for token in client.stream(_MESSAGES)]


async def test_a_provider_that_cannot_be_reached_is_one_failure_shape() -> None:
    import httpx

    client = _client(status_error=httpx.ConnectError("no route to host"))

    with pytest.raises(LLMUnavailableError):
        [token async for token in client.stream(_MESSAGES)]


async def test_a_chunk_that_is_not_json_is_not_an_answer() -> None:
    client = _client(["not json at all"])

    with pytest.raises(LLMUnavailableError):
        [token async for token in client.stream(_MESSAGES)]


async def test_a_stream_without_usage_ends_with_zero_counts() -> None:
    """A provider that kept the port's shape but sent no `usage` cannot have its
    cost computed. The adapter reports zero rather than inventing a figure, and
    `_evidence_from` refuses to persist a turn whose counts it cannot trust — so
    the zero is a visible number, not a cost."""
    client = _client([_chunk("La parcela "), _chunk("", finish_reason="stop")])

    collected = [token async for token in client.stream(_MESSAGES)]

    assert collected[-1].usage == (await _zero_usage())


async def _zero_usage():
    from techcamp.assistant.application.ports import TokenUsage

    return TokenUsage(prompt_tokens=0, completion_tokens=0)


async def test_one_call_per_stream_and_the_request_the_profile_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H2: one call per request. The double counts the calls, so a stream that had
    been retried or fanned out would show here."""
    monkeypatch.setenv("TECHCAMP_LLM_API_KEY", "sk-deepseek")
    monkeypatch.setenv("TECHCAMP_LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("TECHCAMP_LLM_MODEL", "deepseek-chat")
    transport = _FakeTransport([_chunk("La parcela "), _chunk("", finish_reason="stop")])
    client = DeepSeekLLMClient(transport, model=llm_model())
    client._api_key = llm_api_key()

    [token async for token in client.stream(_MESSAGES)]

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["url"] == "https://example.invalid/v1"
    assert call["api_key"] == "sk-deepseek"
    assert call["model"] == "deepseek-chat"
    assert call["messages"] == [{"role": "user", "content": "¿Por qué no regué ayer?"}]


async def test_a_missing_key_is_reported_at_the_call_and_not_at_the_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The key is resolved per call: a deployment that starts without the variable
    fails on the first question, with the provider's own complaint, rather than
    never being able to import at all."""
    monkeypatch.delenv("TECHCAMP_LLM_API_KEY", raising=False)
    client = _client([_chunk("La parcela ")])

    with pytest.raises(LLMUnavailableError):
        [token async for token in client.stream(_MESSAGES)]
