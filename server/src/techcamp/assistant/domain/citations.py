"""The citation an answer carries (E12 T1; docs/06-diseno-detallado.md §9;
docs/04-api.md §Riesgo, métricas y asistente).

A citation is what makes an answer traceable: `source` is required because a
document without its source is not citable (ADR-0008:12), and the rest of the
fields are evidence that may legitimately be absent — a corpus document has no
`model_version`, a prediction has no `similarity`.

`kind` is what decides which of those absences is legitimate, and
`validate_citation` is the rule that turns it into an error instead of a silent
gap. A prediction-shaped citation has to name the `model_version` that produced
it and say whether that version is a baseline. The reason is specific and not
cosmetic: the served version is the `climatology_month` baseline, which carries
`thresholds={}` and therefore reports **every** prediction as `low` (D-T9.2), so
an unmarked `low` is a missing calibration presented as a finding. The Spanish
"línea base" mark the producer reads is the adapter's copy (T4/T5); this flag is
the evidence behind it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from techcamp.assistant.domain.errors import (
    CitationMissingBaselineMarkError,
    CitationMissingModelVersionError,
)


class CitationKind(StrEnum):
    """What a citation points at.

    `guide` and `agroclimatic_bulletin` are the two kinds
    `kb_document.kind` declares (docs/03-modelo-datos.md:347-355). `risk_prediction`
    is not a corpus document: it is the served `risk_prediction` of the plot's
    cell, which docs/04-api.md §Riesgo, métricas y asistente serves with its
    `model_version`.
    """

    GUIDE = "guide"
    AGROCLIMATIC_BULLETIN = "agroclimatic_bulletin"
    RISK_PREDICTION = "risk_prediction"


@dataclass(frozen=True, slots=True)
class Citation:
    """One cited source of an answer, with the evidence that backs it.

    Every optional field is `None` when there is no evidence of that kind, and
    never `0` or `False` standing in for it (D-T12.1): `similarity = 0.0` would
    claim the fragment was retrieved as the least similar one instead of saying
    nothing about a similarity, and `is_baseline = False` on a prediction would
    claim a calibrated version served it.
    """

    source: str
    """The provenance of the citation, always present: the institution or
    publisher behind it ("Boletín Agroclimático Nacional"). `source_url` is the
    link when the source publishes one (ADR-0008:12 records source **and**
    licence per document)."""
    title: str | None = None
    kind: CitationKind | None = None
    source_url: str | None = None
    published_on: date | None = None
    similarity: float | None = None
    model_version: str | None = None
    """Which version produced the prediction, for a prediction-shaped citation
    (docs/04-api.md §Riesgo: `model_version: { name, version, is_baseline,
    metrics }`). The label is T4's to render from `query_plot_risk`'s flattened
    `version_name` / `version_version`; the shape only requires the answer to
    carry *which* version produced it."""
    is_baseline: bool | None = None
    """Whether that version is a registered baseline (docs/06 §9: "Si la
    predicción viene de la heurística de línea base, se indica"). `None` on a
    prediction is rejected rather than read as `False`."""


def validate_citation(citation: Citation) -> None:
    """Check that a citation may be carried by an answer.

    Only the prediction-shaped citation has a required evidence of its own; every
    other kind is a corpus document and needs nothing beyond its source. Raises
    `CitationMissingModelVersionError` or `CitationMissingBaselineMarkError`.

    A blank string counts as missing for the version: an answer that renders an
    empty label instead of a name has cited nothing, and `""` would pass a
    `is None` test while saying the same thing.
    """
    if citation.kind is not CitationKind.RISK_PREDICTION:
        return
    if citation.model_version is None or not citation.model_version.strip():
        raise CitationMissingModelVersionError(citation.source)
    if citation.is_baseline is None:
        raise CitationMissingBaselineMarkError(citation.source)
