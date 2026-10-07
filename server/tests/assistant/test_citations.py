"""The citation an answer carries (E12 T1; docs/06-diseno-detallado.md §9;
docs/04-api.md §Riesgo, métricas y asistente).

A citation without a source is not one, so `source` is required and everything
else is optional. The one rule that is not optional is the prediction-shaped
citation: when the answer speaks about risk it carries the `model_version` that
produced the prediction and says whether that version is a baseline, because the
served `climatology_month` baseline reports every prediction as `low` for want of
calibration (D-T9.2) and an unmarked `low` reads as a verdict the system never
made.
"""

from __future__ import annotations

from datetime import date

import pytest

from techcamp.assistant.domain.citations import (
    Citation,
    CitationKind,
    validate_citation,
)
from techcamp.assistant.domain.errors import (
    CitationMissingBaselineMarkError,
    CitationMissingModelVersionError,
)


def _bulletin() -> Citation:
    return Citation(
        source="Boletín Agroclimático Nacional",
        title="Boletín Agroclimático Nacional, octubre 2026",
        kind=CitationKind.AGROCLIMATIC_BULLETIN,
        source_url="https://example.org/ban-oct-2026",
        published_on=date(2026, 10, 1),
        similarity=0.83,
    )


def _prediction(*, model_version: str | None, is_baseline: bool | None) -> Citation:
    return Citation(
        source="Predicción de riesgo",
        kind=CitationKind.RISK_PREDICTION,
        model_version=model_version,
        is_baseline=is_baseline,
    )


def test_a_citation_only_needs_its_source() -> None:
    citation = Citation(source="Guía de manejo del ají")

    assert citation.source == "Guía de manejo del ají"
    assert citation.title is None
    assert citation.kind is None
    assert citation.model_version is None
    assert citation.is_baseline is None


def test_a_document_citation_carries_its_provenance_and_its_similarity() -> None:
    """ADR-0008:12 — every document records its source, so a cited fragment can
    be traced; `similarity` is the retrieval score that selected it."""
    citation = _bulletin()

    assert citation.title is not None
    assert citation.source_url == "https://example.org/ban-oct-2026"
    assert citation.published_on == date(2026, 10, 1)
    assert citation.similarity == 0.83


def test_a_citation_is_a_frozen_slotted_dataclass() -> None:
    """A citation is written once and read by the endpoint, the transcript and the
    test: nothing may rewrite it between the moment it is validated and the
    moment it is served."""
    citation = _bulletin()

    assert not hasattr(citation, "__dict__")
    with pytest.raises(AttributeError):
        citation.source = "otro documento"


def test_the_kinds_are_the_documented_kb_kinds_plus_a_prediction() -> None:
    # docs/03-modelo-datos.md:347-355, `kb_document.kind`: guide |
    # agroclimatic_bulletin. The prediction is not a corpus document: it is the
    # served `risk_prediction`, which answers about the plot (docs/04 §Riesgo).
    assert {kind.value for kind in CitationKind} == {
        "guide",
        "agroclimatic_bulletin",
        "risk_prediction",
    }


def test_a_document_citation_needs_no_model_version() -> None:
    """A bulletin says nothing about a model: requiring a version there would be
    a rule the docs do not state."""
    validate_citation(_bulletin())


def test_a_prediction_citation_that_names_its_version_is_valid() -> None:
    validate_citation(_prediction(model_version="climatology_month/2026-10", is_baseline=True))


def test_a_prediction_without_its_model_version_is_a_domain_error() -> None:
    with pytest.raises(CitationMissingModelVersionError) as raised:
        validate_citation(_prediction(model_version=None, is_baseline=True))

    assert raised.value.source == "Predicción de riesgo"


def test_a_prediction_that_does_not_say_whether_it_is_a_baseline_is_a_domain_error() -> None:
    """`is_baseline = None` is missing evidence. Reading it as `False` would
    present a baseline's `low` as a calibrated verdict (D-T9.2)."""
    with pytest.raises(CitationMissingBaselineMarkError):
        validate_citation(_prediction(model_version="climatology_month/2026-10", is_baseline=None))


def test_the_served_baseline_is_marked_rather_than_silent() -> None:
    """The acceptance case: an answer about risk carries the version that
    produced the prediction, and a baseline is marked as `linea base`."""
    citation = _prediction(model_version="climatology_month/2026-10", is_baseline=True)

    validate_citation(citation)

    assert citation.is_baseline is True


def test_a_promoted_version_is_marked_as_not_a_baseline() -> None:
    validate_citation(_prediction(model_version="risk_flood/3", is_baseline=False))


def test_a_blank_version_is_missing_evidence_and_not_a_name() -> None:
    """An answer that renders an empty label instead of a version name has cited
    nothing: `""` passes an `is None` test and says the same thing."""
    with pytest.raises(CitationMissingModelVersionError):
        validate_citation(_prediction(model_version="   ", is_baseline=True))
