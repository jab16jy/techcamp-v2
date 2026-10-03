"""The predictor registry (docs/06-diseno-detallado.md §8 "Sin modelo promovido";
docs/08-ml.md §M2 "Línea base servida", "Factores").

T6b ships the port, the registry and the double used here; T9 registers the real
predictors, one per registered version, with the artifacts it promotes
(ADR-0020 paso 10). What is pinned here is the resolution rule: a version is
answered by the predictor registered for it and for nothing else, and a version
with no predictor resolves to nothing rather than to a fabricated probability.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime

from techcamp.risk.application.ports import (
    PredictionOutcome,
    Predictor,
    PredictorRegistry,
)
from techcamp.risk.domain.models import ModelVersion

_CREATED = datetime(2026, 10, 2, tzinfo=UTC)


def _version(*, name: str = "risk_flood", version: str = "2026-10-02") -> ModelVersion:
    return ModelVersion(
        id=uuid.uuid4(),
        name=name,
        version=version,
        artifact_uri="s3://models/flood.ubj",
        is_baseline=False,
        thresholds={"high": 0.7, "critical": 0.85},
        promoted=True,
        created_at=_CREATED,
    )


class _ConstantPredictor:
    """The double a registered predictor is: it answers one probability and
    records the version and the features it was called with."""

    def __init__(self, probability: float) -> None:
        self._probability = probability
        self.calls: list[tuple[ModelVersion, Mapping[str, float | None]]] = []

    def predict(
        self, version: ModelVersion, features: Mapping[str, float | None]
    ) -> PredictionOutcome:
        self.calls.append((version, dict(features)))
        return PredictionOutcome(
            probability=self._probability,
            top_factors=[{"feature": "precip_sum_1m", "value": 12.0, "contribution": 0.4}],
        )


def test_a_registered_version_resolves_the_predictor_registered_for_it() -> None:
    predictor = _ConstantPredictor(0.8)
    registry: PredictorRegistry = PredictorRegistry()
    registry.register("risk_flood", "2026-10-02", predictor)

    assert registry.resolve(_version()) is predictor


def test_another_version_of_the_same_event_resolves_nothing() -> None:
    """The negative of the rule: the artifact, its calibration and its
    thresholds belong to one registered version, so a predictor of another
    version would answer with a probability this one never saw
    (docs/08-ml.md §M2 "Severidad")."""
    registry: PredictorRegistry = PredictorRegistry()
    registry.register("risk_flood", "2026-10-02", _ConstantPredictor(0.8))

    assert registry.resolve(_version(version="2026-09-01")) is None


def test_another_event_resolves_nothing() -> None:
    registry: PredictorRegistry = PredictorRegistry()
    registry.register("risk_flood", "2026-10-02", _ConstantPredictor(0.8))

    assert registry.resolve(_version(name="risk_drought")) is None


def test_an_empty_registry_resolves_nothing() -> None:
    """Before T9 registers a real predictor there is none, and the job must find
    that out instead of inventing a probability (docs/06 §8 "Sin modelo
    promovido")."""
    assert PredictorRegistry().resolve(_version()) is None


def test_the_registry_resolves_a_predictor_before_use() -> None:
    predictor: Predictor = _ConstantPredictor(0.42)
    registry: PredictorRegistry = PredictorRegistry({("risk_drought", "spi3-1"): predictor})

    assert registry.resolve(_version(name="risk_drought", version="spi3-1")) is predictor
