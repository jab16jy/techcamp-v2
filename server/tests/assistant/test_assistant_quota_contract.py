"""The T6 quota contract **on the endpoint** (E12 T6; docs/04-api.md:22;
docs/06-diseno-detallado.md §9; D-T6.1, D-T12.1, D-T12.3).

`test_usage.py` decides the quota, `test_usage_quota.py` decides it in Postgres and
`test_limits.py` owns the pure rules. None of them exercises the wire, and the
wire is where docs/04:22 becomes a contract a producer and the web client of T7
meet: a status code, a `Retry-After` in seconds, and a body that says which of the
two limits stopped the model. This module is that contract, one clause per test:

* **A** — the eleventh question of the Bogota day answers `429` with a numeric
  `Retry-After` counted to local midnight; the tenth is answered normally.
* **B** — a refusal answers the degraded body (`answer` → `sources` → `done`) and
  calls the provider **zero** times, whether the day or the budget stopped it.
* **C** — the degraded answer persists `NULL` evidence and the month's `SUM`
  ignores it (D-T12.1: missing evidence is never a `0`).
* **D** — the cap is per-profile configuration (`seminar` → `None`, `production`
  → the configured figure or `0`), and a malformed figure is a `503` on the wire,
  never a silent zero.
* **E** — with no cap the budget never refuses, and there is no `/metrics`: the
  metric name is recorded for E14, not built here.
* **P** — **a budget refusal with room in the day reads as the budget**: the day
  still had a free slot, so the budget is what stopped this call, and a budget
  refusal is `200` with the degraded body, never the `429` that promises the
  producer he can ask again tomorrow.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
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
from techcamp.assistant.adapters.api.stream import ANSWER_EVENT, DONE_EVENT, SOURCES_EVENT
from techcamp.assistant.adapters.orm import MessageRow
from techcamp.assistant.adapters.repositories import SqlAlchemyConversationRepository
from techcamp.assistant.domain.limits import DAILY_QUESTION_LIMIT
from techcamp.assistant.domain.models import MessageRole
from techcamp.main import app
from techcamp.shared.config import llm_budget_cap_cop

pytestmark = pytest.mark.anyio

MESSAGES_URL = "/api/v1/assistant/messages"
QUESTION = "¿Por qué no regué ayer?"
BOGOTA_TZ = ZoneInfo("America/Bogota")
_CAP_ENV = "TECHCAMP_LLM_BUDGET_CAP_COP"


@pytest.fixture(autouse=True)
def _clear_dependency_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


@pytest.fixture
async def domain(db_session: AsyncSession) -> SeededDomain:
    """The seeded rows and the two non-database ports doubled.

    `get_budget_cap` is **not** overridden here: only the tests that are about the
    budget install a figure, so every other clause meets the default profile's
    `None` — the day's limit is then the only gate — exactly as a seminar
    deployment does.
    """
    seeded = await seed_domain(db_session)
    _override(get_llm_client, _fake_llm())
    _override(get_embedder, lambda: FakeEmbedder())
    return seeded


def _override(dependency: Any, value: Any) -> None:
    app.dependency_overrides[dependency] = value if callable(value) else (lambda: value)


def _fake_llm() -> FakeLLMClient:
    return FakeLLMClient(
        tokens=["La parcela "], model=SEMINAR_MODEL, prompt_tokens=10, completion_tokens=1
    )


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


def _events(body: str) -> list[str]:
    return [block["event"] for block in _blocks(body)]


def _block(body: str, event: str) -> dict[str, Any] | None:
    return next((block["data"] for block in _blocks(body) if block["event"] == event), None)


async def _seed(
    db_session: AsyncSession,
    domain: SeededDomain,
    *,
    questions: int,
    cost: Decimal | None = None,
) -> UUID:
    """A conversation of `domain`'s user holding `questions` questions and, when
    `cost` is given, one billed answer of that amount."""
    conversation = await SqlAlchemyConversationRepository(db_session).create(
        user_id=domain.user_id, plot_id=domain.plot_id
    )
    for index in range(questions):
        db_session.add(
            MessageRow(
                id=UUID(int=index + 1),
                conversation_id=conversation.id,
                role=MessageRole.USER.value,
                content=f"pregunta {index}",
                created_at=datetime.now(UTC),
            )
        )
    if cost is not None:
        db_session.add(
            MessageRow(
                id=UUID(int=900),
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT.value,
                content="respuesta",
                created_at=datetime.now(UTC),
                model=SEMINAR_MODEL,
                cost_cop=cost,
            )
        )
    await db_session.commit()
    return conversation.id


def _ask(domain: SeededDomain, **payload: Any) -> Any:
    body: dict[str, Any] = {"content": QUESTION}
    body.update(payload)
    return TestClient(app).post(
        MESSAGES_URL, json=body, headers={"Authorization": f"Bearer {domain.token}"}
    )


# --- A: the 429 of the local day -------------------------------------------


async def test_A_the_eleventh_question_is_429_with_numeric_retry_after(
    domain: SeededDomain, db_session: AsyncSession
) -> None:
    """docs/04:22 — the eleventh question of the day, and a `Retry-After` in
    **seconds** to local midnight rather than an HTTP-date the client would have to
    trust the server's clock to read."""
    llm = _fake_llm()
    _override(get_llm_client, lambda: llm)
    conversation_id = await _seed(db_session, domain, questions=DAILY_QUESTION_LIMIT)

    response = _ask(domain, conversation_id=str(conversation_id))

    assert response.status_code == 429
    retry_after = response.headers["retry-after"]
    assert retry_after.strip().isdigit()
    local_now = datetime.now(UTC).astimezone(BOGOTA_TZ)
    expected = math.ceil(
        (
            datetime.combine(local_now.date() + timedelta(days=1), time.min, tzinfo=BOGOTA_TZ)
            - local_now
        ).total_seconds()
    )
    assert 1 <= int(retry_after) <= 24 * 3600
    assert abs(int(retry_after) - expected) <= 5
    assert llm.call_count == 0


async def test_A_the_tenth_question_is_answered_normally(
    domain: SeededDomain, db_session: AsyncSession
) -> None:
    llm = _fake_llm()
    _override(get_llm_client, lambda: llm)
    conversation_id = await _seed(db_session, domain, questions=DAILY_QUESTION_LIMIT - 1)

    response = _ask(domain, conversation_id=str(conversation_id))

    assert response.status_code == 200
    assert response.headers.get("retry-after") is None
    assert _events(response.text) == ["token", "sources", "done"]
    assert llm.call_count == 1


# --- B: the degraded body without a provider call --------------------------


@pytest.mark.parametrize("exhausted", ["day", "budget"])
async def test_B_a_refusal_answers_the_degraded_body_without_a_provider_call(
    domain: SeededDomain, db_session: AsyncSession, exhausted: str
) -> None:
    """docs/06 §9: limit or budget exhausted → the answer without the model. The
    body keeps the same frame order in both branches (one contract for T7)."""
    llm = _fake_llm()
    _override(get_llm_client, lambda: llm)
    if exhausted == "day":
        conversation_id = await _seed(db_session, domain, questions=DAILY_QUESTION_LIMIT)
    else:
        _override(get_budget_cap, Decimal("20"))
        conversation_id = await _seed(db_session, domain, questions=0, cost=Decimal("20"))

    response = _ask(domain, conversation_id=str(conversation_id))

    assert _events(response.text) == [ANSWER_EVENT, SOURCES_EVENT, DONE_EVENT]
    answer = _block(response.text, ANSWER_EVENT)
    assert answer is not None
    assert answer["degraded"] is True
    assert llm.call_count == 0


# --- C: missing evidence is null, never zero -------------------------------


async def test_C_the_degraded_answer_persists_nulls_and_the_sum_ignores_them(
    domain: SeededDomain, db_session: AsyncSession
) -> None:
    """D-T12.1: `model IS NULL` is what distinguishes "the LLM was never called"
    from "it was called and cost nothing", and SQL `SUM` ignores the `NULL` cost so
    a degraded turn does not spend the month's budget."""
    _override(get_budget_cap, Decimal("20"))
    conversation_id = await _seed(db_session, domain, questions=0, cost=Decimal("20"))

    assert _ask(domain, conversation_id=str(conversation_id)).status_code == 200

    rows = (
        (
            await db_session.execute(
                select(MessageRow)
                .where(MessageRow.conversation_id == conversation_id)
                .order_by(MessageRow.created_at, MessageRow.id)
            )
        )
        .scalars()
        .all()
    )
    degraded = [
        row for row in rows if row.role == MessageRole.ASSISTANT.value and row.model is None
    ]
    assert len(degraded) == 1
    assert degraded[0].input_tokens is None
    assert degraded[0].output_tokens is None
    assert degraded[0].cost_cop is None
    total = (
        await db_session.execute(
            select(func.coalesce(func.sum(MessageRow.cost_cop), 0)).where(
                MessageRow.conversation_id == conversation_id
            )
        )
    ).scalar_one()
    assert Decimal(total) == Decimal("20")


# --- D: the cap is configuration -------------------------------------------


def test_D_the_cap_is_per_profile_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TECHCAMP_PROFILE", "seminar")
    monkeypatch.setenv(_CAP_ENV, "999999")
    assert llm_budget_cap_cop() is None
    monkeypatch.setenv("TECHCAMP_PROFILE", "production")
    monkeypatch.delenv(_CAP_ENV, raising=False)
    assert llm_budget_cap_cop() == Decimal("0")
    monkeypatch.setenv(_CAP_ENV, "12345.67")
    assert llm_budget_cap_cop() == Decimal("12345.67")
    for bad in ("abc", "-1", "NaN", "Infinity", "1,000", "100 COP"):
        monkeypatch.setenv(_CAP_ENV, bad)
        with pytest.raises(ValueError):
            llm_budget_cap_cop()


async def test_D_a_malformed_cap_is_a_503_not_a_silent_zero(
    domain: SeededDomain, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cap nobody can read is a configuration error, and the endpoint says so
    before any model call instead of spending nothing while it looks configured."""
    monkeypatch.setenv("TECHCAMP_PROFILE", "production")
    monkeypatch.setenv("TECHCAMP_LLM_API_KEY", "probe-key")
    monkeypatch.setenv(_CAP_ENV, "not-a-number")
    _override(get_embedder, lambda: FakeEmbedder())
    _override(get_prices, lambda: {SEMINAR_MODEL: ModelPrice.local(SEMINAR_MODEL)})

    response = _ask(domain, plot_id=str(domain.plot_id))

    assert response.status_code == 503
    assert "budget cap misconfigured" in response.text


# --- E: no cap, no refusal, no /metrics ------------------------------------


async def test_E_without_a_cap_the_budget_never_refuses(
    domain: SeededDomain, db_session: AsyncSession
) -> None:
    """`None` is "this profile does not measure money", so a month that spent
    everything it liked is not a reason to stop (D-T6.1)."""
    llm = _fake_llm()
    _override(get_llm_client, lambda: llm)
    _override(get_budget_cap, None)
    conversation_id = await _seed(db_session, domain, questions=0, cost=Decimal("999999"))

    response = _ask(domain, conversation_id=str(conversation_id))

    assert response.status_code == 200
    assert llm.call_count == 1


def test_E_there_is_no_metrics_endpoint() -> None:
    """T6f: `llm_cost_cop_total` is recorded and `gasto del mes / tope` stays
    derivable from `message.cost_cop`; `/metrics` is E14's, not this epic's."""
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/metrics" not in paths
    assert "/api/v1/metrics" not in paths


# --- P: the reason must name what stopped this call ------------------------


async def test_P_a_budget_refusal_with_room_in_the_day_reads_as_the_budget(
    domain: SeededDomain, db_session: AsyncSession
) -> None:
    """Nine questions today (one slot free) and the month's budget exhausted.

    The day still had room, so the budget is what stopped the call: a budget
    refusal is a `200` with the degraded body, never a `429` with `Retry-After`.
    The `429` tells the producer he can ask again after local midnight, and with
    the budget spent he would be degraded again tomorrow: router's own contract
    (docs/04:22, docs/06 §9) is that the `429` names the **day** only.
    """
    llm = _fake_llm()
    _override(get_llm_client, lambda: llm)
    _override(get_budget_cap, Decimal("20"))
    conversation_id = await _seed(
        db_session, domain, questions=DAILY_QUESTION_LIMIT - 1, cost=Decimal("20")
    )

    response = _ask(domain, conversation_id=str(conversation_id))

    assert response.status_code == 200, response.text
    assert response.headers.get("retry-after") is None
    answer = _block(response.text, ANSWER_EVENT)
    assert answer is not None
    assert answer["reason"] == "budget_exhausted"
    assert llm.call_count == 0


async def test_a_conversation_of_another_user_is_404_before_the_quota_gate(
    domain: SeededDomain,
) -> None:
    """The gates keep their order (docs/04 §Convenciones HTTP): an absent
    conversation is `404`, never a `429` that would confirm a transcript exists."""
    response = _ask(domain, conversation_id=str(UUID(int=1)))

    assert response.status_code == 404
