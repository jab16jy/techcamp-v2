"""The metrics of the M2 harness: PR-AUC, Brier, the paired IC95 and the thresholds.

docs/08 §Reglas de gobierno fixes two of them by name. "Compuerta estadística": the
improvement over the **best** baseline has to carry a **paired** bootstrap IC95 whose lower
bound is above 0, and the Brier may not get worse. "Test intocable": the test block is read
once per final candidate, so every interval here is seeded and its seed is a constant —
an interval that moved between two runs of the same candidate would be evidence of the
resample, not of the model.

Nothing here changes the frequency of anything. docs/08 §Reglas de gobierno "Frecuencia
real" keeps validation and test at the real class frequency and allows weighting only
inside train, so no function in this module samples, balances or subsamples a block: the
bootstrap draws row indices and nothing else, and a resample that holds no positive is left
out instead of being read as a zero average precision, which would drag the lower bound
under an improvement that really happened.

The operating thresholds come from validation only (ADR-0020 step 7), and a precision the
scores cannot reach is `None` — missing evidence, never `0.0` and never `1.0` (D-T0.4,
docs/08 §M2 "Severidad": `crítico` exists only if a threshold reaches it).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from sklearn.metrics import average_precision_score, brier_score_loss, precision_recall_curve

BOOTSTRAP_SEED = 20261002
"""The seed of the resamples. Pinned so the same candidate gives the same interval on any
machine and any day (docs/08 §Reglas de gobierno, "Reproducible o no existe")."""

BOOTSTRAP_RESAMPLES = 1000
"""How many paired resamples the IC95 is read from. Athousand is the usual floor for a
2.5% tail and it is written down here so the interval is reproducible from this file."""

HIGH_PRECISION = 0.7
CRITICAL_PRECISION = 0.85
"""docs/08 §M2 "Uso operativo" and "Severidad": `alto` is the probability at or above the
threshold that reaches precision 0.7 in validation, `crítico` the one that reaches 0.85
"si existe" (D-T0.4)."""

CONFIDENCE = 0.95
"""The interval the gate reads; docs/08 names IC95 and never another."""

Scores = Sequence[float] | npt.NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class Interval:
    """A point estimate with its bootstrap interval around it."""

    point: float
    lower: float
    upper: float


@dataclass(frozen=True, slots=True)
class Threshold:
    """One operating cut and what it costs: its precision and its recall, both published."""

    value: float
    precision: float
    recall: float


@dataclass(frozen=True, slots=True)
class Thresholds:
    """The two cuts of `model_version.thresholds` (docs/08 §M2 "Severidad").

    `None` is the answer "this block cannot reach that precision", which is different from
    a cut of 0.0 (alert on everything) and from a cut above every score (alert on nothing).
    """

    high: Threshold | None
    critical: Threshold | None


def pr_auc(y_true: Scores, y_score: Scores) -> float:
    """The average precision of the ranking `y_score` gives over `y_true`.

    PR-AUC and not ROC-AUC: with the real frequency of M2 — 1 508 reports over 16 380
    municipality-months, about 9% (data card §Conteos) — ROC-AUC flatters a model that says
    nothing useful, and docs/08 §M2 "Uso operativo" is written in terms of precision.
    """
    return float(average_precision_score(_labels(y_true), _scores(y_score)))


def brier(y_true: Scores, y_score: Scores) -> float:
    """The mean squared error of the probability itself, the calibration half of the gate."""
    return float(brier_score_loss(_labels(y_true), _scores(y_score)))


def paired_improvement_ci95(
    y_true: Scores,
    candidate: Scores,
    baseline: Scores,
    *,
    seed: int = BOOTSTRAP_SEED,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> Interval:
    """The PR-AUC of `candidate` over `baseline`, with its paired bootstrap IC95.

    Paired on purpose: one set of row indices per replicate scores both, so the difference
    removes the sampling noise the two blocks share instead of adding it twice. A resample
    holding no positive (or no negative) has no average precision, and is left out of the
    interval rather than read as a zero — counting it would pull the lower bound down under
    a real improvement and quietly make the gate harder to pass than the docs ask.
    """
    labels, candidate_scores, baseline_scores = (
        _labels(y_true),
        _scores(candidate),
        _scores(baseline),
    )
    generator = np.random.default_rng(seed)
    improvements: list[float] = []
    for _ in range(resamples):
        rows = generator.integers(0, labels.size, labels.size)
        sampled = labels[rows]
        if not sampled.any() or sampled.all():
            continue
        improvements.append(
            float(
                average_precision_score(sampled, candidate_scores[rows])
                - average_precision_score(sampled, baseline_scores[rows])
            )
        )
    if not improvements:
        raise ValueError(
            f"none of the {resamples} resamples held both classes; the block is too small "
            "to say anything about an improvement"
        )
    tail = (1.0 - CONFIDENCE) / 2
    return Interval(
        point=pr_auc(y_true, candidate) - pr_auc(y_true, baseline),
        lower=float(np.quantile(improvements, tail)),
        upper=float(np.quantile(improvements, 1.0 - tail)),
    )


def operating_thresholds(
    y_true: Scores,
    y_score: Scores,
    *,
    high_precision: float = HIGH_PRECISION,
    critical_precision: float = CRITICAL_PRECISION,
) -> Thresholds:
    """The `alto` and `crítico` cuts of the validation block (ADR-0020 step 7).

    The lowest threshold that still reaches the target precision, because recall is what
    falls as the cut rises and a model that can hold 0.7 precision further down should
    alert further down. `precision_recall_curve` answers `(precision, recall, thresholds)`
    in that order, with `precision` and `recall` one element longer than `thresholds`: the
    extra last pair is a `(1.0, 0.0)` sentinel that belongs to no threshold. Only the first
    `thresholds.size` pairs are cuts, or every block would answer a precision of 1.0 and
    `crítico` would never be missing.
    """
    precisions, recalls, thresholds = precision_recall_curve(_labels(y_true), _scores(y_score))
    return Thresholds(
        high=_cut(thresholds, precisions, recalls, high_precision),
        critical=_cut(thresholds, precisions, recalls, critical_precision),
    )


def _cut(
    thresholds: npt.NDArray[np.float64],
    precisions: npt.NDArray[np.float64],
    recalls: npt.NDArray[np.float64],
    target: float,
) -> Threshold | None:
    """The lowest real threshold whose precision reaches `target`, or `None`."""
    reached = [index for index in range(thresholds.size) if precisions[index] >= target]
    if not reached:
        return None
    lowest = min(reached, key=lambda index: thresholds[index])
    return Threshold(
        value=float(thresholds[lowest]),
        precision=float(precisions[lowest]),
        recall=float(recalls[lowest]),
    )


def _labels(values: Scores) -> npt.NDArray[np.int64]:
    return np.asarray(values).astype(np.int64)


def _scores(values: Scores) -> npt.NDArray[np.float64]:
    return np.asarray(values, dtype=np.float64)
