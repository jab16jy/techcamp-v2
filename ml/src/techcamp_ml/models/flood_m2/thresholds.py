"""Calibration and the operating cut of M2, read on validation only (ADR-0020 step 7).

Two questions are answered here and both are answered on **validation**, never on test:

* **Is the probability a probability?** The Brier score of the gate is half of what
  `harness.promotion.decide_promotion` refuses on, and a ranking that is good but badly
  scaled loses the gate on a calibration it never had a chance to fix. The fix is Platt
  scaling — a logistic on the logit of the candidate's own score — fitted on validation,
  because that is the block ADR-0020 step 7 names.
* **Where does `alto` start?** `harness.metrics.operating_thresholds` reads the two cuts of
  docs/08 §M2 "Severidad" at precision ≥ 0.7 and ≥ 0.85, and reports the precision *and* the
  recall of each. The recall is published (docs/08 §M2 "Uso operativo", model card §7): a
  threshold nobody can state a recall for is a threshold nobody can plan a season around.

**Platt scaling and not isotonic regression.** Isotonic fits one step per distinct score and
would put up to `n_positives` of them on 184 validation positives; a calibration curve that
wiggles that finely is a curve that will not survive the next year. Platt has two
parameters, which is what 184 positives can carry.

Calibration is monotone by construction, so it cannot change a candidate's PR-AUC: the
ranking the ladder chose on is the ranking the gate will read. That is a property worth
having rather than a coincidence, and `test_calibration_never_reorders_the_ranking` pins it.

A cut that the block cannot reach is `None` — the harness says so and this module says so
again — and `severity` never answers `critical` when `critical` is `None`. A third state is
missing evidence (D-T0.4), never `0.0` and never a score above every prediction.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd
from sklearn.linear_model import LogisticRegression

from techcamp_ml.harness.metrics import Threshold, Thresholds, operating_thresholds
from techcamp_ml.harness.promotion import Scored
from techcamp_ml.models.flood_m2.experiments import design, positive_class

CLIP = 1e-6
"""How far from 0 and 1 a score may sit before its logit is read. A score of exactly 0.0 or
1.0 is a model's rounding, not a certainty, and `log(0)` is not a number a calibrator can
be fitted on."""

CALIBRATOR_STRENGTH = 1e3
"""Platt scaling in everything but the closed form: a logistic on the logit with a weakly
regularized fit. The default `C=1.0` on a logit-scale feature would shrink every slope
towards zero and answer the train prevalence for a model whose scores run from 0.01 to
0.99."""

LOW, HIGH, CRITICAL = "low", "high", "critical"
"""The severity codes of docs/08 §M2 "Severidad", which travel with `model_version`."""


@dataclass(frozen=True, slots=True)
class CalibratedCandidate:
    """A candidate whose probability is the candidate's score put on a probability scale.

    It keeps the `harness.promotion.Scored` protocol, so the object the gate scores is the
    one the ladder ranked: the wrapper adds a calibration and nothing else.
    """

    base: Scored
    calibrator: LogisticRegression

    @classmethod
    def fit(cls, base: Scored, validation: pd.DataFrame) -> CalibratedCandidate:
        """Calibrate `base` on the validation block. One class and no calibration is
        possible, and it is refused rather than answered with a constant."""
        labels = validation["label"].to_numpy(dtype=np.int64)
        if labels.min() == labels.max():
            raise ValueError(
                f"the validation block holds {int(labels.sum())} positives of {labels.size} "
                "rows and only one class; a calibrator fitted on it would answer that class "
                "for every month, which is a prevalence and not a calibration"
            )
        calibrator = LogisticRegression(C=CALIBRATOR_STRENGTH, max_iter=1000)
        calibrator.fit(_logit(positive_class(base.predict_proba(design(validation)))), labels)
        return cls(base=base, calibrator=calibrator)

    def predict_proba(self, features: pd.DataFrame) -> npt.NDArray[np.float64]:
        """The two-column matrix the gate reads, from the calibrated score."""
        calibrated = self.calibrator.predict_proba(
            _logit(positive_class(self.base.predict_proba(design(features))))
        )
        positive = np.asarray(calibrated, dtype=np.float64)[:, 1]
        return np.column_stack([1.0 - positive, positive])

    def score(self, features: pd.DataFrame) -> npt.NDArray[np.float64]:
        """The calibrated positive class, for a caller that wants the number."""
        return positive_class(self.predict_proba(features))


@dataclass(frozen=True, slots=True)
class OperatingCuts:
    """The two cuts of docs/08 §M2 "Severidad" and the severities they imply.

    `Thresholds` is the harness's answer and is kept whole: `high` and `critical` are its
    `Threshold | None` fields, so the precision and the recall of every cut are already in
    the object this returns.
    """

    thresholds: Thresholds

    @property
    def high(self) -> Threshold | None:
        return self.thresholds.high

    @property
    def critical(self) -> Threshold | None:
        return self.thresholds.critical

    def recall_at_high(self) -> float | None:
        """The published recall of the `alto` cut, or `None` when validation never reached
        precision 0.7 and there is no cut to publish a recall for."""
        return None if self.thresholds.high is None else self.thresholds.high.recall

    def severity(self, scores: Sequence[float] | npt.NDArray[np.float64]) -> npt.NDArray[Any]:
        """The severity code of every score (docs/08 §M2 "Severidad": `high` at or above the
        0.7 cut, `critical` at or above the 0.85 one, `low` the rest).

        A score above every prediction and a cut the block could not reach are the same
        question with the same answer `None`, and both leave the score at `low`: there is no
        `alto` or `crítico` in a version whose cuts do not exist (D-T0.4).
        """
        values = np.asarray(scores, dtype=np.float64)
        coded = np.full(values.shape, LOW, dtype=object)
        if self.thresholds.high is not None:
            coded[values >= self.thresholds.high.value] = HIGH
        if self.thresholds.critical is not None:
            coded[values >= self.thresholds.critical.value] = CRITICAL
        return coded

    def report(self) -> str:
        """One line per cut, in the words the model card §7 publishes."""
        return " | ".join(
            f"{name}: {_line(cut)}"
            for name, cut in (("high", self.high), ("critical", self.critical))
        )


def read_cuts(candidate: Scored, validation: pd.DataFrame) -> OperatingCuts:
    """The operating cuts of `candidate` on validation, calibrated first (ADR-0020 paso 7).

    Both halves read the same block, and that is what step 7 asks for: the calibration is
    fitted on validation and the cut is read on validation. It is the one place where
    validation is used twice, so it is named here rather than left to be discovered.
    """
    calibrated = CalibratedCandidate.fit(candidate, validation)
    labels = validation["label"].to_numpy(dtype=np.int64)
    return OperatingCuts(thresholds=operating_thresholds(labels, calibrated.score(validation)))


def _logit(scores: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    clipped = np.clip(np.asarray(scores, dtype=np.float64), CLIP, 1.0 - CLIP)
    return np.log(clipped / (1.0 - clipped)).reshape(-1, 1)


def _line(cut: Threshold | None) -> str:
    if cut is None:
        return "no cut reached its target precision in validation"
    return f"threshold {cut.value:.6f}, precision {cut.precision:.6f}, recall {cut.recall:.6f}"
