"""The served climatology baseline (docs/08-ml.md §M2 "Escalera", first rung,
"Factores"; docs/06-diseno-detallado.md §8).

T9's server-side first rung of the ladder: the frequency of the flood event per
calendar month of train, fitted by `ml/` and served here. What is pinned here
is the serving contract of docs/08 §M2 and the parity with `ml/`:

* the month is read off `month_sin`/`month_cos` with the SAME nearest-pair rule
  `techcamp_ml.models.flood_m2.baselines.calendar_month` uses on the training
  side (docs/08 §Reglas de gobierno "Paridad de features"), so December is not
  answered with January's frequency;
* a month train never saw answers with the prevalence of the whole train block
  (`0.0` would be "never floods" and `1.0` "always floods": claims about a
  month nobody observed);
* features without the two seasonality features answer with the prevalence and
  NO factor, never with an invented probability and never with `0`
  (missing evidence is a third state, `risk/domain/features.py`).

The severity is not pinned here because this predictor never chooses one: it is
`severity_for` reading the probability against the version's own thresholds
(docs/08 §M2 "Severidad").
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, date, datetime

import pytest

from techcamp.risk.adapters.climatology import ClimatologyPredictor
from techcamp.risk.application.ports import PredictionOutcome
from techcamp.risk.domain.features import FEATURE_NAMES, build_features, seasonality
from techcamp.risk.domain.models import ModelVersion

_VERSION = "2026-10-02"
_URI = f"s3://ml-artifacts/models/risk_flood/{_VERSION}/climatology.json"
_CREATED = datetime(2026, 10, 2, tzinfo=UTC)


def _version() -> ModelVersion:
    return ModelVersion(
        id=uuid.UUID(int=1),
        name="risk_flood",
        version=_VERSION,
        artifact_uri=_URI,
        is_baseline=True,
        thresholds={},
        promoted=False,
        created_at=_CREATED,
    )


def _features(issue_month: date) -> dict[str, float | None]:
    """The M2 vector the daily job builds, with empty series: every window is
    missing evidence and only the seasonality features carry a value, which is
    all this predictor reads (docs/08-ml.md §M2 "Escalera")."""
    return build_features(
        issue_month=issue_month, precipitation={}, soil_moisture={}, elevation_m=None
    )


def _predictor(**overrides: object) -> ClimatologyPredictor:
    fields: dict[str, object] = {
        "version": _VERSION,
        "prevalence": 0.13,
        "by_month": {},
        "train_years": (2019, 2020, 2021, 2022),
        "fitted_rows": 1404,
        "fitted_positives": 182,
    }
    return ClimatologyPredictor(**{**fields, **overrides})  # type: ignore[arg-type]


def test_the_month_of_the_features_answers_with_that_months_frequency() -> None:
    """The positive case: October features, October frequency."""
    predictor = _predictor(by_month={10: 0.41, 11: 0.05})

    outcome = predictor.predict(_version(), _features(date(2026, 10, 1)))

    assert isinstance(outcome, PredictionOutcome)
    assert outcome.probability == pytest.approx(0.41)


def test_a_month_train_never_saw_answers_with_the_prevalence() -> None:
    """The negative of the rule: March holds no estimate, so the answer is the
    prevalence every part of train agrees on, and not `0.0` — "never floods" is
    a claim about a month nobody observed (`ml/`'s `ClimatologyBaseline.score`)."""
    predictor = _predictor(by_month={10: 0.41})

    outcome = predictor.predict(_version(), _features(date(2026, 3, 1)))

    assert outcome.probability == pytest.approx(0.13)


def test_december_is_not_answered_with_januarys_frequency() -> None:
    """The parity case (docs/08-ml.md §Reglas de gobierno "Paridad de features").

    December and January sit next to each other on the circle, so an `arctan` of
    the two rounded features — or a plain `round` of the angle — puts the
    boundary in the wrong place and reads December as January. The nearest of
    the twelve pairs `seasonality` produces is what both sides do.
    """
    predictor = _predictor(by_month={12: 0.55, 1: 0.05})

    december = predictor.predict(_version(), _features(date(2026, 12, 1)))
    january = predictor.predict(_version(), _features(date(2027, 1, 1)))

    assert december.probability == pytest.approx(0.55)
    assert january.probability == pytest.approx(0.05)


def test_every_calendar_month_reads_back_its_own_frequency() -> None:
    """The twelve pairs are recovered exactly, which is the whole parity claim:
    whatever `build_features` writes for month M, serving reads M back."""
    predictor = _predictor(by_month=dict.fromkeys(range(1, 13), 0.2))

    for month in range(1, 13):
        outcome = predictor.predict(_version(), _features(date(2026, month, 1)))
        assert outcome.top_factors[0]["value"] == float(month)


def test_top_factors_is_the_rule_input_in_the_shape_the_api_declares() -> None:
    """docs/04-api.md §Riesgo, métricas y asistente: `top_factors` is
    `{ feature, value, contribution }`, and docs/08 §M2 "Factores" asks for the
    rule's own inputs with their value — for the climatology the month, and the
    frequency that month contributes."""
    predictor = _predictor(by_month={10: 0.41})

    outcome = predictor.predict(_version(), _features(date(2026, 10, 1)))

    assert len(outcome.top_factors) == 1
    factor = outcome.top_factors[0]
    assert set(factor) == {"feature", "value", "contribution"}
    assert factor["feature"] == "month"
    assert factor["value"] == 10.0
    assert factor["contribution"] == pytest.approx(0.41)


def test_a_month_train_never_saw_reports_the_prevalence_as_its_contribution() -> None:
    """The factor states the frequency the predictor answered with, which for an
    unobserved month is the prevalence — the same number the probability is, so
    the API never shows a factor the probability does not carry."""
    predictor = _predictor(by_month={10: 0.41})

    outcome = predictor.predict(_version(), _features(date(2026, 3, 1)))

    assert outcome.top_factors[0]["contribution"] == pytest.approx(0.13)


@pytest.mark.parametrize(
    "missing",
    [
        pytest.param({}, id="neither feature"),
        pytest.param({"month_sin": None, "month_cos": None}, id="both missing"),
        pytest.param({"month_sin": 0.0, "month_cos": None}, id="cos missing"),
        pytest.param(
            {"month_sin": math.nan, "month_cos": 1.0}, id="sin is NaN, as ml/ marks a missing day"
        ),
    ],
)
def test_features_without_a_readable_month_answer_with_the_prevalence_and_no_factor(
    missing: dict[str, float | None],
) -> None:
    """Missing evidence is a third state (`risk/domain/features.py`), never a
    probability and never `0`: the predictor has no month, so it says the
    prevalence of the whole train block and names no rule input, because it did
    not read one."""
    predictor = _predictor(by_month={10: 0.41})

    outcome = predictor.predict(_version(), missing)

    assert outcome.probability == pytest.approx(0.13)
    assert outcome.top_factors == []


def test_the_predictor_reads_only_the_shared_feature_contract() -> None:
    """The vector the job builds is `FEATURE_NAMES`; a month outside that
    contract is not an input this predictor may read (the gate scores the blocked
    block with exactly this contract, `ml/`'s `baselines.py`)."""
    predictor = _predictor(by_month={10: 0.41})

    features = _features(date(2026, 10, 1))

    assert set(features) == set(FEATURE_NAMES)
    assert predictor.predict(_version(), features).probability == pytest.approx(0.41)
    sin, cos = seasonality(10)
    assert features["month_sin"] == sin
    assert features["month_cos"] == cos
