"""Climate-risk domain data and the severity rule (docs/03-modelo-datos.md
§`municipality`, `model_version` y `risk_prediction`: riesgo climático (E10);
docs/08-ml.md §M2 "Severidad", D-T0.4).

Pure data and one pure rule, no I/O: `ModelVersion` is what registration
(T9) writes and what serving reads to know *which* thresholds apply,
`RiskPrediction` is the row a cell's risk is read from, and `severity_for` is
the single place a probability becomes a severity — the serving job (T6b) and
the baseline ladder (`ml/`) both go through it, so a prediction can never be
stored with a severity another writer would not have produced.

The thresholds are the model's, not the server's: `ModelVersion.thresholds` is
`{"high": p, "critical": p | None}` calibrated in validation (docs/03
§Umbrales), and a missing threshold is missing evidence, never a zero one —
the version that was never calibrated for an operating point cannot serve it.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    """The event a prediction is about (docs/03: `flood|drought`; docs/06 §8).

    Flood is M2 (docs/08 §Inventario); drought is M3, which follows only if the
    positive count allows it, so both codes exist from the start and the
    vocabulary does not change with the epic.
    """

    FLOOD = "flood"
    DROUGHT = "drought"

    @property
    def version_name(self) -> str:
        """The `model_version.name` this event is registered under
        (docs/03-modelo-datos.md:319-333: `risk_flood|risk_drought|suitability`).

        A prediction stores the event (`flood|drought`) and a model version stores
        the name it serves, so the writer of both needs this one mapping. It lives
        with the vocabulary rather than in the job that happens to need it first.
        """
        return _VERSION_NAMES[self]


_VERSION_NAMES: Mapping[EventType, str] = {
    EventType.FLOOD: "risk_flood",
    EventType.DROUGHT: "risk_drought",
}
"""Spelled out instead of derived from the value: the three names docs/03
declares are a vocabulary of their own, and a fourth event must be registered here
rather than guessed into a `risk_*` name nothing reads."""


class Severity(StrEnum):
    """How urgent a prediction is (docs/08-ml.md §M2 "Severidad").

    The stored and served codes are `low`, `high`, `critical` while the docs'
    prose says `bajo`, `alto`, `crítico`; these are the machine codes
    (docs/04 §Riesgo, métricas y asistente).
    """

    LOW = "low"
    HIGH = "high"
    CRITICAL = "critical"


Thresholds = Mapping[str, float | None]
"""`{"high": p, "critical": p | None}` of one `model_version` (docs/03
§Umbrales): the operating points its validation produced, not constants."""


@dataclass(frozen=True, slots=True)
class ArchiveDay:
    """One day of the Open-Meteo ERA5 archive for one point (docs/08-ml.md §M2
    "Clima").

    The two measures the M2 features read, kept together because they come from
    the same day of the same response. Both are `float | None`: ERA5 has no
    value for a day it has not aggregated, and a `0` mm would be a claim about
    the weather that the archive does not make (the same rule
    `domain/features.py` follows on the series it is handed).
    """

    day: date
    precipitation_mm: float | None
    soil_moisture_m3_m3: float | None


@dataclass(frozen=True, slots=True)
class ModelVersion:
    """One registered model or baseline (docs/03-modelo-datos.md:319-333).

    `is_baseline` marks the registered baselines that are served when no model
    passes the gate (docs/08 §M2 "Línea base servida", D-T0.5), which is why
    every prediction has a `model_version_id` even before any model exists.
    `artifact_sha256`, `dataset_hash` and `git_commit` are the traceability the
    governance rules ask for (docs/03 §Integridad del artefacto, §Trazabilidad,
    docs/08 §Reglas de gobierno).
    """

    id: uuid.UUID
    name: str
    version: str
    artifact_uri: str
    is_baseline: bool
    thresholds: dict[str, float | None]
    promoted: bool
    created_at: datetime
    metrics: dict[str, Any] | None = None
    baseline_metrics: dict[str, Any] | None = None
    artifact_sha256: str | None = None
    dataset_hash: str | None = None
    git_commit: str | None = None
    promotion_reason: str | None = None


@dataclass(frozen=True, slots=True)
class RiskPrediction:
    """One cell's risk for one event and one month (docs/03-modelo-datos.md:335-347).

    `horizon_start` is day 1 of the predicted month M and `horizon_days` its
    length; the prediction is issued with data through the last day of M-1
    (docs/08 §M2 "Horizonte", D-T0.3). `probability` is the model's own output
    and `severity` the rule's reading of it against the version's thresholds.
    """

    id: uuid.UUID
    cell_id: int
    model_version_id: uuid.UUID
    event_type: EventType
    horizon_start: date
    horizon_days: int
    probability: float
    severity: Severity
    top_factors: list[dict[str, Any]]
    created_at: datetime


def severity_for(probability: float, thresholds: Thresholds) -> Severity:
    """The severity a probability carries under one version's thresholds
    (docs/08-ml.md §M2 "Severidad").

    `critical` is checked first because the operating points are nested: a
    critical probability also satisfies `high`, and reporting it as `high`
    would hide the alert the version was calibrated to raise. Both boundaries
    belong to the higher severity — `alto` is probability **>=** the threshold
    that gives precision >= 0.7 — because the threshold is the smallest
    probability that passed in validation.

    A threshold of `None` (or absent) is missing evidence, not a zero one: a
    version whose calibration never reached an operating point cannot serve
    it, so its ceiling stays at the severity below.
    """
    critical = thresholds.get("critical")
    if critical is not None and probability >= critical:
        return Severity.CRITICAL
    high = thresholds.get("high")
    if high is not None and probability >= high:
        return Severity.HIGH
    return Severity.LOW
