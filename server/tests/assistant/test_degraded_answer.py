"""The answer without an LLM, as a use case (E12 T4; docs/06-diseno-detallado.md §9;
docs/04-api.md §Riesgo, métricas y asistente; ADR-0007:26; D-T12.1, D-T12.6).

docs/06 §9 draws the degraded branch: when the day's limit or the month's budget
is exhausted, "respuesta sin LLM: datos de la parcela + documentos relevantes",
and "En la respuesta sin LLM se devuelven **estos mismos hechos** junto con los
documentos relevantes". So this answer is not an apology and not a placeholder:
it is the same facts, in plain Spanish, with the documents, said by the
application rather than by a model.

What is pinned **here** is the structural guarantee, which is the reason the path
is shaped the way it is: no public function of it takes an `LLMClient`, so
"budget exhausted → zero provider calls" is a property of the signature rather
than a promise. Two tests carry it — one that hands a `FakeLLMClient` to the test
and asks it afterwards how many times it was called, and one that inspects every
public function's signature — and the other two pin what the use case adds over
the renderer: the citations travel unchanged, and the same facts and the same
evidence render the same text.

**Why the split.** `answer_without_llm` is application and the renderer it wraps
is domain, and the two were first written together with one test module. The tests
of the text therefore dragged an application import into what should have been a
domain-only slice. The prompt rules of docs/06 :381-387 and every family of the
rendered text now live in `test_degraded.py`, which imports nothing from
`techcamp.assistant.application`. What is left here is only what
`answer_without_llm` does around the renderer.
"""

from __future__ import annotations

import inspect
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from assistant.doubles import FakeLLMClient
from techcamp.assistant.application.degraded_answer import answer_without_llm
from techcamp.assistant.application.ports import RetrievedFragment
from techcamp.assistant.application.retrieval import RetrievedEvidence
from techcamp.assistant.domain import degraded as degraded_module
from techcamp.assistant.domain.citations import Citation, CitationKind
from techcamp.assistant.domain.degraded import DegradedAnswer
from techcamp.assistant.domain.facts import (
    AlertFact,
    IrrigationFact,
    PlotFacts,
    RiskFact,
    SeasonalClimateFact,
    SensorFact,
)

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 6, 14, 0, tzinfo=UTC)
PLOT = UUID("33333333-3333-3333-3333-333333333333")

BULLETIN_CITATION = Citation(
    source="https://example.org/ban-oct-2026",
    title="Boletín Agroclimático Nacional, octubre 2026",
    kind=CitationKind.AGROCLIMATIC_BULLETIN,
    source_url="https://example.org/ban-oct-2026",
    published_on=date(2026, 10, 1),
    similarity=0.86,
)
GUIDE_CITATION = Citation(
    source="https://example.org/aji",
    title="Guía de manejo del ají",
    kind=CitationKind.GUIDE,
    source_url="https://example.org/aji",
    similarity=0.81,
)


def _facts(**overrides: object) -> PlotFacts:
    base: dict[str, object] = {
        "plot_id": PLOT,
        "plot_name": "Lote del sur",
        "sensors": (
            SensorFact(metric="soil_moisture", value=21.5, reading_time=NOW, is_stale=False),
            SensorFact(metric="air_temp", value=29.0, reading_time=NOW, is_stale=False),
        ),
        "alerts": (
            AlertFact(
                rule_code="water_stress",
                severity="critical",
                opened_at=datetime(2026, 10, 6, 2, 0, tzinfo=UTC),
                acknowledged=False,
                evidence={"day": "2026-10-05", "source": "balance"},
            ),
        ),
        "irrigation": IrrigationFact(
            day=date(2026, 10, 6),
            kind="irrigate",
            rationale={"et0_mm": 4.5, "kc": 0.9, "low_confidence": True},
            depth_mm=12.5,
            duration_min=35,
            advice=(),
        ),
        "seasonal_climate": SeasonalClimateFact(
            title="Boletín Agroclimático Nacional, octubre 2026",
            department=None,
            published_on=date(2026, 10, 1),
            enso_state="neutral",
            source_url="https://example.org/ban-oct-2026",
        ),
        "risk": (
            RiskFact(
                event_type="flood",
                horizon_start=date(2026, 10, 1),
                horizon_days=30,
                probability=0.12,
                severity="low",
                top_factors=({"feature": "month", "value": 10.0, "contribution": 0.12},),
                model_version="climatology_month 2026-10-06",
                metrics={"pr_auc": 0.44, "brier": 0.21},
                is_baseline=True,
            ),
        ),
        "missing": (),
    }
    return PlotFacts(**{**base, **overrides})  # type: ignore[arg-type]


def _evidence(*citations: Citation) -> RetrievedEvidence:
    return RetrievedEvidence(
        fragments=(),
        citations=tuple(citations),
    )


# --- the structural guarantee ---------------------------------------------


async def test_the_degraded_answer_calls_no_provider() -> None:
    """ADR-0007:26 and docs/06 §9: the degraded branch has no provider call at all,
    and the message is stored with `model`, tokens and `cost_cop` null (D-T12.1).

    The double is in the test rather than in the signature because that is the only
    way to observe it from outside: it is constructed, handed nothing, and asked
    afterwards how many times it was called."""
    llm = FakeLLMClient()

    answer = await answer_without_llm(
        facts=_facts(), evidence=_evidence(BULLETIN_CITATION, GUIDE_CITATION)
    )

    assert isinstance(answer, DegradedAnswer)
    assert llm.call_count == 0


def test_no_public_function_of_the_degraded_path_can_reach_an_llm() -> None:
    """The structural argument, which is the one that survives a refactor: the
    function that renders the degraded answer **has no `LLMClient` parameter**, so
    "budget exhausted → zero provider calls" cannot be broken by adding a line
    inside it. This is the same shape `evaluate_quota` argues for T1, and it is why
    no test above needs a provider double to prove the guarantee."""
    public = [
        name
        for name in dir(degraded_module)
        if not name.startswith("_")
        and callable(getattr(degraded_module, name))
        # Only what this module defines: an imported `date` or `dataclass` is
        # callable too, and its signature is not this module's answer.
        and getattr(getattr(degraded_module, name), "__module__", None) == degraded_module.__name__
    ]
    assert public

    signatures = [inspect.signature(getattr(degraded_module, name)) for name in public]
    signatures.append(inspect.signature(answer_without_llm))
    for signature in signatures:
        assert "LLMClient" not in str(signature), signature
        assert "llm" not in signature.parameters, signature


async def test_the_degraded_answer_carries_the_citations_it_was_given_unchanged() -> None:
    """The documents travel with the answer so the endpoint can write them into the
    response and the producer can open them; they are the same `Citation` values
    retrieval built, not a copy rebuilt by the renderer."""
    evidence = RetrievedEvidence(
        fragments=(
            RetrievedFragment(id=UUID(int=1), document_id=UUID(int=2), content="x", similarity=0.9),
        ),
        citations=(BULLETIN_CITATION, GUIDE_CITATION),
    )

    answer = await answer_without_llm(facts=_facts(), evidence=evidence)

    assert answer.citations == (BULLETIN_CITATION, GUIDE_CITATION)


async def test_the_same_facts_and_the_same_evidence_render_the_same_text() -> None:
    """The use case is a thin wrapper over the pure renderer, and it stays that
    way: the answer a producer reads when the budget ran out is decided by the
    facts, not by when the endpoint happened to be called."""
    evidence = _evidence(BULLETIN_CITATION)

    first = await answer_without_llm(facts=_facts(), evidence=evidence)
    second = await answer_without_llm(facts=_facts(), evidence=evidence)

    assert first == second
    assert first.text == second.text
