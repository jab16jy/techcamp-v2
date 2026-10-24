"""ADR-0028's wiring regressions: the production rate card, its fail-closed rule,
and the malformed-cap 503 that stopped being masked once the card stopped raising.

The verification probe of T8's wiring unit, promoted to regression tests: the
card answers for `deepseek-flash` only, a model with no card is
`UnknownModelPriceError` — never a zero (D-T12.1) — and the prices 503 that used
to mask the budget-cap error (T6's recorded caveat) is gone with the wired card.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from assistant.db_fixtures import SeededDomain, seed_domain
from assistant.doubles import FakeEmbedder, FakeLLMClient
from techcamp.assistant.adapters.api.deps import get_embedder, get_llm_client, get_prices
from techcamp.assistant.adapters.api.pricing import UnknownModelPriceError, price_for
from techcamp.main import app

pytestmark = pytest.mark.anyio

_KEY_ENV = "TECHCAMP_LLM_API_KEY"
_CAP_ENV = "TECHCAMP_LLM_BUDGET_CAP_COP"
_MESSAGES_URL = "/api/v1/assistant/messages"
_QUESTION = "¿Por qué no regué ayer?"


@pytest.fixture
async def domain(db_session: AsyncSession) -> SeededDomain:
    """The seeded rows, with the two non-database ports doubled.

    `get_prices` and `get_budget_cap` are deliberately **not** overridden: these
    tests are about the profile configuration itself, so the real card and the
    real cap rule are the gates a production request meets.
    """
    seeded = await seed_domain(db_session)
    app.dependency_overrides[get_llm_client] = lambda: FakeLLMClient(
        tokens=["La parcela "], prompt_tokens=10, completion_tokens=1
    )
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    return seeded


def _ask(domain: SeededDomain, **payload: Any) -> Any:
    body: dict[str, Any] = {"content": _QUESTION}
    body.update(payload)
    return TestClient(app).post(
        _MESSAGES_URL, json=body, headers={"Authorization": f"Bearer {domain.token}"}
    )


def test_an_operator_pointing_the_model_elsewhere_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production table answers for `deepseek-flash` only: any other
    configured model is `UnknownModelPriceError`, never a zero (D-T12.1)."""
    monkeypatch.delenv(_KEY_ENV, raising=False)
    monkeypatch.setenv("TECHCAMP_PROFILE", "production")

    for unpriced in ("deepseek-chat", "deepseek-v4-pro"):
        with pytest.raises(UnknownModelPriceError) as failure:
            price_for(get_prices(), unpriced)
        assert unpriced in str(failure.value)


async def test_a_malformed_cap_surfaces_once_the_card_is_wired(
    domain: SeededDomain, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production, a key and a malformed cap must answer the cap's 503: the old
    prices 503 that masked it (T6's recorded caveat) is gone with the wired card."""
    monkeypatch.setenv("TECHCAMP_PROFILE", "production")
    monkeypatch.setenv(_KEY_ENV, "probe-key")
    monkeypatch.setenv(_CAP_ENV, "not-a-number")

    response = _ask(domain)

    assert response.status_code == 503
    assert "budget cap misconfigured" in response.text


async def test_an_unpriced_operator_model_ends_in_an_error_frame_never_a_zero(
    domain: SeededDomain, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A turn whose answering model has no card ends in an error frame naming it:
    no cost row of `0` is persisted for a call whose price nobody recorded."""
    monkeypatch.setenv("TECHCAMP_PROFILE", "production")
    monkeypatch.setenv(_KEY_ENV, "probe-key")
    monkeypatch.setenv(_CAP_ENV, "999999")
    app.dependency_overrides[get_llm_client] = lambda: FakeLLMClient(
        tokens=["respuesta "], model="deepseek-v4-pro", prompt_tokens=10, completion_tokens=1
    )

    try:
        response = _ask(domain)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "no hay precio configurado para el modelo: deepseek-v4-pro" in response.text
