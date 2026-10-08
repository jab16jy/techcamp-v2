"""The assistant's provider seam (E12 T6; ADR-0026; ADR-0007:14; ADR-0021;
docs/09-cuellos-de-botella.md §Seguridad).

ADR-0026 pins `deepseek-chat` as the provisional provider, and the API keys belong
to `shared/config.py` — which is this task's surface. What this module pins is
where the key comes from and what the profile does without one:

* **no committed default**, in either profile. A committed default for a provider
  credential would be a live key in the repository, and the seminar profile does
  not need one — it answers with the local stand-in;
* **the seminar profile still answers locally**, which is what ADR-0021 wants from
  the profile that runs without a provider;
* **production answers `503` until the key exists**, and a `503` that names the
  variable an operator has to set rather than a title nobody can act on. Once the
  key exists the client is DeepSeek behind the port, and the same key is what the
  transport sends.

The stand-in's model name is pinned as not-a-provider's, because
`message.model` records which model produced an answer (D-T12.1) and a seminar
name that could be mistaken for `deepseek-chat` would be a false record.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.anyio

from techcamp.assistant.adapters.api.deps import (
    SEMINAR_MODEL,
    SeminarLLMClient,
    get_llm_client,
    get_prices,
)
from techcamp.shared.config import (
    is_seminar_profile,
    llm_api_key,
    llm_base_url,
    llm_model,
)
from techcamp.shared.errors import ProblemError

_KEY_ENV = "TECHCAMP_LLM_API_KEY"


def _clear_assistant_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.delenv("TECHCAMP_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("TECHCAMP_LLM_MODEL", raising=False)
    monkeypatch.delenv("TECHCAMP_PROFILE", raising=False)


def test_the_provider_key_has_no_committed_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_assistant_environment(monkeypatch)

    assert llm_api_key() is None


def test_the_provider_key_and_endpoint_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_assistant_environment(monkeypatch)
    monkeypatch.setenv(_KEY_ENV, "sk-deepseek")
    monkeypatch.setenv("TECHCAMP_LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("TECHCAMP_LLM_MODEL", "deepseek-chat")

    assert llm_api_key() == "sk-deepseek"
    assert llm_base_url() == "https://example.invalid/v1"
    assert llm_model() == "deepseek-chat"


def test_the_endpoint_defaults_to_the_provisional_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_assistant_environment(monkeypatch)

    assert llm_base_url() == "https://api.deepseek.com"
    assert llm_model() == "deepseek-chat"


def test_the_seminar_profile_still_answers_locally(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_assistant_environment(monkeypatch)

    assert is_seminar_profile() is True
    assert isinstance(get_llm_client(), SeminarLLMClient)


def test_production_without_keys_still_answers_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 503 of T5 is not removed by T6: the production profile still refuses to
    answer before its provider key exists (ADR-0026 pins `deepseek-chat`; the real
    rate card is T8's)."""
    _clear_assistant_environment(monkeypatch)
    monkeypatch.setenv("TECHCAMP_PROFILE", "production")

    with pytest.raises(ProblemError) as llm_failure:
        get_llm_client()
    with pytest.raises(ProblemError):
        get_prices()

    assert llm_failure.value.status == 503
    # The detail names the variable, or an operator has nothing to set.
    assert _KEY_ENV in (llm_failure.value.detail or "")


def test_production_with_keys_builds_the_deepseek_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured key is what turns the 503 into a real client: the adapter is
    wired to the keys, so an operator who sets one gets a provider and not a
    half-configured profile."""
    _clear_assistant_environment(monkeypatch)
    monkeypatch.setenv("TECHCAMP_PROFILE", "production")
    monkeypatch.setenv(_KEY_ENV, "sk-test")

    from techcamp.assistant.adapters.api.deepseek import DeepSeekLLMClient

    client = get_llm_client()

    assert isinstance(client, DeepSeekLLMClient)
    assert client.model == llm_model()


def test_the_stand_in_model_is_not_a_provider_s_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-T12.1: an answer records which model produced it, and a seminar name that
    could be mistaken for `deepseek-chat` would be a false record in the
    transcript."""
    _clear_assistant_environment(monkeypatch)

    assert SEMINAR_MODEL != llm_model()


async def test_the_http_transport_drops_the_done_sentinel() -> None:
    """The transport is what strips `data: [DONE]`, so the adapter parses chunks and
    never a sentinel: a `[DONE]` that reached the parser would be a JSON error in
    the middle of an answer. Driven with `httpx.MockTransport`, the same seam the
    Open-Meteo clients use."""
    import json

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
