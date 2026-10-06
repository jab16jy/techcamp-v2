"""The served climatology baseline: the flood frequency of train by calendar
month (docs/08-ml.md §M2 "Escalera", first rung, "Factores", "Línea base
servida"; docs/06-diseno-detallado.md §8; ADR-0020 paso 10).

`ml/` fits the ladder's first rung — `ClimatologyBaseline.fit` over the train
split alone — and publishes its numbers as the artifact of the `model_version`
row that serves it (docs/03-modelo-datos.md §Integridad del artefacto,
docs/adr/0018). This module is the other half of that pair: the same rule,
answered from a `Mapping[str, float | None]` instead of a DataFrame, with no
pandas and no model in the serving process.

Four decisions are the rule's, not this adapter's:

* **The month comes from the shared contract.** `month_sin`/`month_cos` are
  inside `FEATURE_NAMES` (docs/08 §M2 "Features"), and the month is recovered as
  the NEAREST of the twelve pairs `risk/domain/features.py::seasonality`
  produces — the same criterion `ml/`'s `baselines.calendar_month` applies on
  the training side. An `arctan` of the two rounded features would put the
  December/January boundary in the wrong place and read one as the other, which
  is exactly the train/serve divergence docs/08 §Reglas de gobierno "Paridad de
  features" forbids.
* **A month train never saw answers with the prevalence.** `0.0` would be
  "never floods" and `1.0` "always floods"; both are claims about a month
  nobody observed (`ml/`'s `ClimatologyBaseline.score` says the same).
* **Missing evidence is a third state.** When `features` carries no readable
  `month_sin`/`month_cos` this predictor has no month at all, so it answers the
  prevalence of the whole train block and returns NO `top_factors`: it did not
  read a rule input, so naming one would be a factor no evidence supports. It
  never invents a probability and never answers `0` (docs/08 §M2 "Features", the
  rule `risk/domain/features.py` states for the series it is handed).
* **The severity is not chosen here.** `probability` is the model's own output;
  the severity is `severity_for` reading it against the thresholds that travel
  with the version (docs/08 §M2 "Severidad"; `risk/domain/models.py`). A
  baseline row registered with `thresholds={}` therefore serves `low`, which is
  a missing operating point rather than a claim that risk is low.

`top_factors` is the rule's own input with its value (docs/08 §M2 "Factores":
"las entradas de la regla en la heurística"), in the shape docs/04-api.md
§Riesgo, métricas y asistente declares: exactly one entry, the month and the
frequency that month contributed. There is one input, so there is one entry, not
three padded with the features this rule never reads.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from techcamp.risk.application.ports import PredictionOutcome
from techcamp.risk.domain.features import seasonality
from techcamp.risk.domain.models import ModelVersion

ARTIFACT_KIND = "climatology"
"""The `kind` the artifact body carries and the reader requires (the artifact
contract of ADR-0020 paso 10): bytes whose `kind` is anything else are another
model's artifact, however well their sha256 verifies."""

CALENDAR_MONTHS: tuple[int, ...] = tuple(range(1, 13))
"""The twelve levels a frequency can be estimated for."""

MONTH_SIN, MONTH_COS = "month_sin", "month_cos"
"""The two seasonality features the month is read from, named as
`FEATURE_NAMES` names them (`risk/domain/features.py`)."""

MONTH_FACTOR = "month"
"""The name of the rule's own input in `top_factors` (docs/04 §Riesgo: `feature`
is a string). The climatology reads no other feature of the contract."""

_REQUIRED_KEYS: tuple[str, ...] = (
    "kind",
    "version",
    "train_years",
    "prevalence",
    "by_month",
    "fitted_rows",
    "fitted_positives",
)
"""What the artifact body must carry, stated here rather than left to a
`KeyError` in the middle of building the predictor: an artifact that does not
describe itself completely is refused whole, never read with defaults filled in."""


@dataclass(frozen=True, slots=True)
class ClimatologyPredictor:
    """One registered climatology version's own inference (docs/08-ml.md §M2
    "Escalera", "Línea base servida").

    `version` is the row's own version string and is what
    `adapters/registry.py` checks the artifact against: the frequencies, the
    train split they were fitted on and the thresholds of one version belong
    together (docs/08 §M2 "Severidad"), so an artifact that names another
    version is not this row's artifact.

    `train_years`, `fitted_rows` and `fitted_positives` are carried and never
    read by the rule: they are the traceability the artifact travels with
    (docs/08 §Reglas de gobierno "Trazabilidad"), and the
    `prevalence`/`by_month` they explain are what answers.
    """

    version: str
    prevalence: float
    by_month: Mapping[int, float]
    train_years: tuple[int, ...] = ()
    fitted_rows: int = 0
    fitted_positives: int = 0

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ClimatologyPredictor:
        """The predictor the artifact body describes, or `ValueError` when the
        body is not a climatology artifact.

        `by_month` keys are integers here and strings in the body: JSON object
        keys are always strings, so the mapping the rule reads is keyed by the
        calendar month it is looked up with.
        """
        if not isinstance(payload, dict):
            raise ValueError(f"the artifact body is a {type(payload).__name__}, not an object")
        missing = [key for key in _REQUIRED_KEYS if key not in payload]
        if missing:
            raise ValueError(f"the climatology artifact has no {missing}")
        if payload["kind"] != ARTIFACT_KIND:
            raise ValueError(f"the artifact is a {payload['kind']!r}, not a {ARTIFACT_KIND!r}")
        raw_by_month = payload["by_month"]
        # Refused as a VALUE and not merely iterated: a `by_month` of null or of a
        # list has no `.items()`, so the comprehension below would raise
        # AttributeError — and the serving factory answers a malformed artifact by
        # returning None so the daily job skips that version and carries on
        # (docs/06-diseno-detallado.md §8 "Sin modelo promovido"). An
        # AttributeError escapes that instead and takes the run down.
        if not isinstance(raw_by_month, dict):
            raise ValueError(
                f"the artifact by_month is a {type(raw_by_month).__name__}, not an object of "
                f"calendar months"
            )
        by_month = {int(month): float(frequency) for month, frequency in raw_by_month.items()}
        outside = sorted(month for month in by_month if month not in CALENDAR_MONTHS)
        if outside:
            raise ValueError(f"the climatology artifact has frequencies for {outside}")
        return cls(
            version=str(payload["version"]),
            prevalence=float(payload["prevalence"]),
            by_month=by_month,
            train_years=tuple(int(year) for year in payload["train_years"]),
            fitted_rows=int(payload["fitted_rows"]),
            fitted_positives=int(payload["fitted_positives"]),
        )

    def predict(
        self, version: ModelVersion, features: Mapping[str, float | None]
    ) -> PredictionOutcome:
        """The frequency of train for the month of `features`, and that month as
        the rule's only input (docs/08-ml.md §M2 "Escalera", "Factores").

        `version` is accepted because the port asks for it and because it is the
        severity's business, not this rule's: the row is what decides which
        thresholds the caller reads, and this predictor's own `version` is the
        one the registry verified.

        Without a readable month the answer is `prevalence` and NO factor: the
        rule read no input, and missing evidence is a third state, never an
        invented probability and never `0`.
        """
        month = _calendar_month(features)
        if month is None:
            return PredictionOutcome(probability=self.prevalence, top_factors=[])
        frequency = self.by_month.get(month, self.prevalence)
        return PredictionOutcome(
            probability=frequency,
            top_factors=[
                {
                    "feature": MONTH_FACTOR,
                    "value": float(month),
                    "contribution": frequency,
                }
            ],
        )


def _calendar_month(features: Mapping[str, float | None]) -> int | None:
    """The calendar month of the row, or `None` when it cannot be read.

    The nearest of the twelve `seasonality` pairs, over both features at once,
    which is the criterion `ml/`'s `baselines.calendar_month` uses so that both
    sides recover the month from the same numbers (docs/08 §Reglas de gobierno
    "Paridad de features"). December sits next to January on that circle, and
    the boundary between two months is where a trigonometry shortcut would put
    a feature of the next month.

    `None` and NaN are both unreadable: `None` is how `build_features` reports
    an absent feature and NaN is how `ml/` marks a missing day, and neither is a
    point on the circle (docs/08 §M2 "Features").
    """
    sin = features.get(MONTH_SIN)
    cos = features.get(MONTH_COS)
    if sin is None or cos is None:
        return None
    if math.isnan(sin) or math.isnan(cos):
        return None
    return min(
        CALENDAR_MONTHS,
        key=lambda month: (seasonality(month)[0] - sin) ** 2 + (seasonality(month)[1] - cos) ** 2,
    )
