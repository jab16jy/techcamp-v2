"""The promotion gate of M2: one read of the test block, and a typed decision.

ADR-0020 step 8 is the whole of this module: promote only if the candidate beats the **best**
baseline with the lower bound of its IC95 above 0 **and** its Brier no worse. Both rules come
from docs/08 §Reglas de gobierno, "Compuerta estadística", and both are checked here rather
than left to the caller, because the caller is an agent (ADR-0020, "Reglas para agentes de
IA que ejecutan el ciclo") and the rules are the ones it may not bend.

There is no `force_promote`. docs/08 §Reglas de gobierno says "No existe la opción", and
that is a real absence, not a documented one: `decide_promotion` takes no argument that could
carry it, and nothing else in this module can promote. When no model passes, what stays in
production is the best validation baseline, which is explainable (docs/08 §M2 "Línea base
servida", D-T0.5) — that decision belongs to the human promotion of step 10, not here.

This is also the only reader of the blocked test block. `harness.split` hands out train and
validation and no function there takes the test boundaries and gives rows back, so the rows
ADR-0020 step 3 locked are scored here, once, in a single pass over the block. The caller
cannot choose the block, cannot pass rows in, and gets the counts back in the report so it
can see what it was judged on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np
import numpy.typing as npt
import pandas as pd
from techcamp.risk.domain.features import FEATURE_NAMES

from techcamp_ml.datasets.flood_m2 import LABEL_COLUMN
from techcamp_ml.harness.metrics import (
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    Interval,
    brier,
    paired_improvement_ci95,
    pr_auc,
)
from techcamp_ml.harness.split import TEST_FIRST, TEST_LAST

CI_LOWER_BOUND_ABOVE_ZERO = "improvement_ic95_lower_bound_not_above_zero"
BRIER_NOT_WORSE = "brier_worse_than_the_best_baseline"
"""The two reasons this gate can refuse a candidate, in the order docs/08 names them. They
are slugs rather than sentences so a report can be compared across candidates without
re-reading prose, and neither of them is `None`."""


@runtime_checkable
class Scored(Protocol):
    """Something fitted that turns a design matrix into probabilities.

    Two real implementations answer it: an estimator of the ladder of docs/08 §M2
    "Escalera", and the heuristic baseline the same row names, so this is a port and not an
    abstraction invented for one caller. `predict_proba` rather than `predict`, because the
    Brier rule of the gate reads a probability and a hard label cannot answer it.
    """

    def predict_proba(self, features: pd.DataFrame) -> npt.NDArray[np.float64]: ...


@dataclass(frozen=True, slots=True)
class ScoreReport:
    """What one scorer answered on the test block."""

    pr_auc: float
    brier: float


@dataclass(frozen=True, slots=True)
class GateReport:
    """The numbers the decision was made of, so a refusal can be read and not guessed at.

    The counts of the block are here on purpose: a gate that says "not promoted" without
    saying how many rows and how many events it said it about cannot be told apart from one
    that scored an empty block.
    """

    test_rows: int
    test_positives: int
    candidate: ScoreReport
    baseline: ScoreReport
    improvement: Interval


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    """`promote` with the rules it failed and the report behind it.

    `reasons` is empty exactly when `promote` is true, and names every rule that failed
    otherwise, so a refusal is read rather than guessed at. Nothing here promotes anything:
    step 10 promotes by hand, after review.
    """

    promote: bool
    reasons: tuple[str, ...]
    report: GateReport


def decide_promotion(
    table: pd.DataFrame,
    *,
    candidate: Scored,
    baseline: Scored,
    seed: int = BOOTSTRAP_SEED,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> PromotionDecision:
    """Run the gate of ADR-0020 step 8 once over the blocked test block.

    `baseline` is the **best** baseline of the validation block (docs/08 §Reglas de gobierno
    names that one, not the trivial one), and it is scored on the same rows as the candidate
    so the paired interval compares like with like.
    """
    labels, features = _test_block(table)
    candidate_scores = _positive_class(candidate.predict_proba(features), features)
    baseline_scores = _positive_class(baseline.predict_proba(features), features)

    improvement = paired_improvement_ci95(
        labels, candidate_scores, baseline_scores, seed=seed, resamples=resamples
    )
    candidate_report = ScoreReport(
        pr_auc=pr_auc(labels, candidate_scores), brier=brier(labels, candidate_scores)
    )
    baseline_report = ScoreReport(
        pr_auc=pr_auc(labels, baseline_scores), brier=brier(labels, baseline_scores)
    )

    reasons: list[str] = []
    if not improvement.lower > 0:
        reasons.append(CI_LOWER_BOUND_ABOVE_ZERO)
    if candidate_report.brier > baseline_report.brier:
        reasons.append(BRIER_NOT_WORSE)

    return PromotionDecision(
        promote=not reasons,
        reasons=tuple(reasons),
        report=GateReport(
            test_rows=len(labels),
            test_positives=int(labels.sum()),
            candidate=candidate_report,
            baseline=baseline_report,
            improvement=improvement,
        ),
    )


def _test_block(table: pd.DataFrame) -> tuple[npt.NDArray[np.int64], pd.DataFrame]:
    """The labels and the design matrix of the blocked block, and nothing else.

    The design matrix is the shared feature contract of T2 and not one column more: an
    identity column would let a model score a municipality it was fitted on, and a column
    the dataset does not hold would be a feature the serving job cannot build
    (docs/08 §Reglas de gobierno, "Paridad de features").
    """
    _require(table, (*FEATURE_NAMES, LABEL_COLUMN))
    months = pd.PeriodIndex(table["horizon_start"], freq="M")
    blocked = table[
        (months >= pd.Period(TEST_FIRST, freq="M")) & (months <= pd.Period(TEST_LAST, freq="M"))
    ]
    if blocked.empty:
        raise ValueError(
            f"the dataset holds no row between {TEST_FIRST} and {TEST_LAST}; the gate has "
            "nothing to decide on, and a gate that scored zero rows would promote anything"
        )
    return (
        blocked[LABEL_COLUMN].to_numpy(dtype=np.int64),
        blocked[list(FEATURE_NAMES)],
    )


def _require(table: pd.DataFrame, columns: tuple[str, ...]) -> None:
    missing = [column for column in columns if column not in table.columns]
    if missing:
        raise ValueError(
            f"the dataset is missing {missing}, so the gate cannot score it; rebuild the "
            "dataset from the current features (docs/08 §M2 column contract)"
        )


def _positive_class(
    probabilities: npt.NDArray[np.float64], features: pd.DataFrame
) -> npt.NDArray[np.float64]:
    """The probability of the positive class, refusing anything that is not one row per
    block row in two columns.

    The check is here and not in the caller because the paired interval below is only
    defined when both scorers answered every row of the same block; a scorer that answered
    a different number of rows would otherwise be compared against nothing.
    """
    if probabilities.shape != (len(features), 2):
        raise ValueError(
            f"predict_proba answered {probabilities.shape} for the {len(features)} rows of "
            "the blocked test block; the gate reads the positive class of a two-column "
            "probability matrix, one row per block row"
        )
    return probabilities[:, 1]
