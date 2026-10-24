"""What the assistant's budget cap resolves to, per profile (E12 T6; D-T6.1, D-T12.3).

The cap is configuration, never a literal: `shared.config.llm_budget_cap_cop()`
reads `TECHCAMP_LLM_BUDGET_CAP_COP` in COP, because the sum it is compared against
is `SUM(message.cost_cop)` (docs/11:132), and the profile decides whether the
figure means anything.

`seminar` does not measure money: its rate card is `ModelPrice.local` — zero COP —
and it never reaches a provider (ADR-0021), so its cap is `None`, never a
converted USD 5. The USD 5 total of docs/04:22 and ADR-0021:30 is the provider
account's ceiling, where money exists, and no documented rate converts it.

`production` measures money, and the figure is the owner's (ADR-0007:17): unset is
`Decimal("0")`, the sense `limits.budget_exhausted` already gives a zero cap — a
profile that measures money and has no cap stops answering with the model instead
of answering for free. A value that is not a non-negative finite decimal is a
configuration error, and the endpoint reports it as `503` before any model call.

`None` therefore means "this profile does not measure money", not "this deployment
configured no cap" and never "unlimited".
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from assistant.db_fixtures import SeededDomain, seed_domain
from assistant.doubles import FakeEmbedder, FakeLLMClient
from techcamp.assistant.adapters.api.deps import (
    SEMINAR_MODEL,
    get_budget_cap,
    get_embedder,
    get_llm_client,
    get_prices,
)
from techcamp.assistant.adapters.api.pricing import ModelPrice
from techcamp.assistant.adapters.api.stream import ANSWER_EVENT, DONE_EVENT
from techcamp.main import app
from techcamp.shared.config import LLM_BUDGET_CAP_COP_ENV, llm_budget_cap_cop
from techcamp.shared.errors import ProblemError

pytestmark = pytest.mark.anyio

MESSAGES_URL = "/api/v1/assistant/messages"
QUESTION = "¿Por qué no regué ayer?"
ANSWER_TOKENS = ["La parcela ", "necesita 12,5 mm ", "de riego hoy."]


def _seminar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TECHCAMP_PROFILE", "seminar")


def _production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TECHCAMP_PROFILE", "production")


# --- the profile rule in shared.config ------------------------------------


def test_the_default_profile_does_not_measure_money(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TECHCAMP_PROFILE", raising=False)
    monkeypatch.delenv(LLM_BUDGET_CAP_COP_ENV, raising=False)

    assert llm_budget_cap_cop() is None


def test_the_seminar_profile_ignores_a_configured_figure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The profile decides, not the variable: a figure set on a deployment that
    never reaches a provider has no money to cap."""
    _seminar(monkeypatch)
    monkeypatch.setenv(LLM_BUDGET_CAP_COP_ENV, "0")

    assert llm_budget_cap_cop() is None


def test_production_without_a_figure_measures_money_with_no_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset is `0`, not `None`: the profile measures money and has no cap, which
    stops the model instead of answering for free (ADR-0007:17)."""
    _production(monkeypatch)
    monkeypatch.delenv(LLM_BUDGET_CAP_COP_ENV, raising=False)

    assert llm_budget_cap_cop() == Decimal("0")


@pytest.mark.parametrize("value", ["", "   "])
def test_a_figure_that_is_set_but_empty_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Unset is `0` — the profile measures money and has no cap — but a variable
    that *is* set and reads blank is an operator mid-edit or a compose file that
    interpolated nothing, and silently turning that into `0` stops the assistant
    for a month with no message saying why. The same rule the malformed figure
    gets: a configuration error, never a silent zero."""
    _production(monkeypatch)
    monkeypatch.setenv(LLM_BUDGET_CAP_COP_ENV, value)

    with pytest.raises(ValueError) as raised:
        llm_budget_cap_cop()

    assert LLM_BUDGET_CAP_COP_ENV in str(raised.value)


def test_production_reads_the_configured_figure(monkeypatch: pytest.MonkeyPatch) -> None:
    _production(monkeypatch)
    monkeypatch.setenv(LLM_BUDGET_CAP_COP_ENV, " 12345.50 ")

    assert llm_budget_cap_cop() == Decimal("12345.50")


@pytest.mark.parametrize("value", ["no-es-un-numero", "-1", "-0.01", "NaN", "Infinity"])
def test_a_malformed_production_figure_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    _production(monkeypatch)
    monkeypatch.setenv(LLM_BUDGET_CAP_COP_ENV, value)

    with pytest.raises(ValueError) as raised:
        llm_budget_cap_cop()

    assert LLM_BUDGET_CAP_COP_ENV in str(raised.value)


# --- the FastAPI dependency delegates there -------------------------------


def test_the_dependency_delegates_to_the_profile_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _production(monkeypatch)
    monkeypatch.setenv(LLM_BUDGET_CAP_COP_ENV, "75")

    assert get_budget_cap() == Decimal("75")


def test_a_malformed_figure_reaches_the_endpoint_as_a_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _production(monkeypatch)
    monkeypatch.setenv(LLM_BUDGET_CAP_COP_ENV, "no-es-un-numero")

    with pytest.raises(ProblemError) as raised:
        get_budget_cap()

    assert raised.value.status == 503
    assert LLM_BUDGET_CAP_COP_ENV in (raised.value.detail or "")


def test_a_blank_figure_reaches_the_endpoint_as_a_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The empty figure is the same 503 as the malformed one: the dependency is
    where an operator's mistake becomes a response, and the detail is what they
    read to fix it."""
    _production(monkeypatch)
    monkeypatch.setenv(LLM_BUDGET_CAP_COP_ENV, "  ")

    with pytest.raises(ProblemError) as raised:
        get_budget_cap()

    assert raised.value.status == 503
    assert LLM_BUDGET_CAP_COP_ENV in (raised.value.detail or "")


# --- the dependency decides the endpoint's branch -------------------------


@pytest.fixture
async def domain(db_session: AsyncSession) -> SeededDomain:
    """The seeded rows, and the non-database ports doubled.

    `get_budget_cap` is deliberately **not** overridden: these tests exercise the
    profile configuration itself, so the daily limit is the only other gate a
    request meets.
    """
    seeded = await seed_domain(db_session)
    _override(get_llm_client, _fake_llm())
    _override(get_embedder, lambda: FakeEmbedder())
    _override(get_prices, lambda: {SEMINAR_MODEL: ModelPrice.local(SEMINAR_MODEL)})
    return seeded


@pytest.fixture(autouse=True)
def _clear_dependency_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _override(dependency: Any, value: Any) -> None:
    app.dependency_overrides[dependency] = value if callable(value) else (lambda: value)


def _fake_llm() -> FakeLLMClient:
    return FakeLLMClient(
        tokens=ANSWER_TOKENS, model=SEMINAR_MODEL, prompt_tokens=3500, completion_tokens=42
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


def _ask(domain: SeededDomain) -> Any:
    return _client().post(MESSAGES_URL, json={"content": QUESTION}, headers=_auth(domain.token))


async def test_production_without_a_cap_degrades_instead_of_answering_for_free(
    domain: SeededDomain, monkeypatch: pytest.MonkeyPatch
) -> None:
    _production(monkeypatch)
    monkeypatch.delenv(LLM_BUDGET_CAP_COP_ENV, raising=False)
    llm = _fake_llm()
    _override(get_llm_client, lambda: llm)

    response = _ask(domain)

    assert response.status_code == 200
    answer = _block(response.text, ANSWER_EVENT)
    assert answer is not None
    assert answer["degraded"] is True
    assert answer["reason"] == "budget_exhausted"
    assert _block(response.text, DONE_EVENT) is not None
    assert llm.call_count == 0


async def test_production_with_a_configured_cap_answers_with_the_model(
    domain: SeededDomain, monkeypatch: pytest.MonkeyPatch
) -> None:
    _production(monkeypatch)
    monkeypatch.setenv(LLM_BUDGET_CAP_COP_ENV, "1000000")
    llm = _fake_llm()
    _override(get_llm_client, lambda: llm)

    response = _ask(domain)

    assert response.status_code == 200
    assert _block(response.text, ANSWER_EVENT) is None
    assert _block(response.text, DONE_EVENT) is not None
    assert llm.call_count == 1


async def test_the_seminar_profile_answers_even_with_a_zero_figure_configured(
    domain: SeededDomain, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A zero figure would exhaust a profile that measured money; `seminar` has no
    monthly sum to compare, so the model answers."""
    _seminar(monkeypatch)
    monkeypatch.setenv(LLM_BUDGET_CAP_COP_ENV, "0")
    llm = _fake_llm()
    _override(get_llm_client, lambda: llm)

    response = _ask(domain)

    assert response.status_code == 200
    assert _block(response.text, ANSWER_EVENT) is None
    assert _block(response.text, DONE_EVENT) is not None
    assert llm.call_count == 1


async def test_a_malformed_figure_is_a_503_before_any_model_call(
    domain: SeededDomain, monkeypatch: pytest.MonkeyPatch
) -> None:
    _production(monkeypatch)
    monkeypatch.setenv(LLM_BUDGET_CAP_COP_ENV, "no-es-un-numero")
    llm = _fake_llm()
    _override(get_llm_client, lambda: llm)

    response = _ask(domain)

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert LLM_BUDGET_CAP_COP_ENV in response.json()["detail"]
    assert llm.call_count == 0
