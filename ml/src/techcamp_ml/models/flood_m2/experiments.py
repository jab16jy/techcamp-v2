"""The quasi-random search over the M2 ladder's noise hyperparameters (ADR-0020 step 6).

docs/08 §M2 "Escalera" ends at LightGBM, and the two rungs above `baselines.py` — a
logistic regression and a booster — are the only places where hyperparameters are chosen at
all. Both are fitted on **train only** and judged on **validation only**: the blocked test
block is not reachable from this module (`harness.split.split` hands out train and
validation and nothing else; the test is `harness.promotion.decide_promotion`'s, and
`gate_run.py` calls it once).

Three choices are worth stating, because each of them could have been made the other way:

* **The budget is fixed and drawn at random, not a grid.** `TRIALS` draws from
  `np.random.default_rng(TUNING_SEED)` over the ranges below. With no conditional
  hyperparameters to fill, a seeded uniform draw *is* the quasirandom search of the
  [Tuning Playbook](https://github.com/google-research/tuning_playbook): a grid spends its
  budget repeating points near the middle of a range and never visits its edges.
* **Only noise hyperparameters are tuned.** The learning rate, the tree size, the
  subsampling and the regularization move; the feature set, the target, the metric and the
  class weighting are fixed. Tuning a modelling decision by validation would be fitting the
  validation block.
* **Early stopping reads validation, never test** (ADR-0020 step 6). That is what the step
  asks for, and it is why the number of rounds is a search *result* and not a
  hyperparameter.

`design` is the shared feature contract and nothing else, because that is exactly what
`harness.promotion._design` hands to `predict_proba` at the gate.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd
from lightgbm import LGBMClassifier, early_stopping
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss
from sklearn.pipeline import Pipeline
from techcamp.risk.domain.features import FEATURE_NAMES

TUNING_SEED = 20261005
"""The seed of the quasi-random draws, fixed so the same register is reproduced on any
machine and any day (docs/08 §Reglas de gobierno, "Reproducible o no existe"). The harness
has its own seed for its own resamples; this one is for the search."""

TRIALS = 12
"""The budget of ADR-0020 step 6: twelve draws per model, whatever the result."""

EARLY_STOPPING_ROUNDS = 50
"""Rounds without an improvement of the validation average precision before the boosting
stops. Validation is the early-stopping signal because that is the block ADR-0020 step 6
names, and the test block is not reachable from this module at all."""

TREE_BUDGET = 1000
"""Rounds offered to the booster. It is not a hyperparameter: early stopping decides how
many of them are used, and `Trial.rounds` records how many were."""

LOGISTIC_RANGE = (1e-3, 1e2)
"""The inverse regularization strength, drawn log-uniform: the range scikit-learn documents,
which spans "underfits" to "barely regularized at all"."""

LIGHTGBM_RANGES: Mapping[str, tuple[float, float]] = {
    "learning_rate": (0.01, 0.3),
    "num_leaves": (8.0, 255.0),
    "min_child_samples": (5.0, 100.0),
    "subsample": (0.6, 1.0),
    "colsample_bytree": (0.6, 1.0),
    "reg_lambda": (1e-3, 10.0),
}
"""The noise hyperparameters of the booster and their ranges, in log space except the two
fractions and the two counts, which are linear (Google Tuning Playbook, "Ranges and
distributions")."""

INTEGER_HYPERPARAMETERS = ("num_leaves", "min_child_samples")
LINEAR_HYPERPARAMETERS = ("subsample", "colsample_bytree")
"""The two fractions are drawn linearly: they are proportions of a whole, so a log scale
would spend most of its draws on a difference no rainfall fraction notices."""

SUBSAMPLE_FREQUENCY = 1
"""`subsample` is silent in LightGBM unless `subsample_freq` is on, so drawing it without
this would tune a parameter the booster never reads."""


@dataclass(frozen=True, slots=True)
class Trial:
    """One draw of the search and the validation numbers it earned.

    Recorded even when it loses: ADR-0020 step 5 asks for every experiment in the register,
    and a search whose failures are not written down is a search nobody can tell apart from
    one that was never run.
    """

    number: int
    params: Mapping[str, Any]
    pr_auc: float
    brier: float
    rounds: int | None = None
    note: str = field(default="")


def design(frame: pd.DataFrame) -> pd.DataFrame:
    """The shared feature contract and nothing else, in its order.

    `harness.promotion._design` hands `predict_proba` exactly `FEATURE_NAMES`, so an
    estimator fitted on the whole training frame — `label` and all — could not answer the
    gate: the gate's frame has one column fewer than the one the estimator expects, and the
    mismatch surfaces as a warning about feature names rather than as an error.
    """
    missing = [column for column in FEATURE_NAMES if column not in frame.columns]
    if missing:
        raise ValueError(
            f"the frame is missing {missing}; the design matrix is the shared contract of "
            f"techcamp.risk.domain.features.FEATURE_NAMES ({len(FEATURE_NAMES)} columns)"
        )
    return frame[list(FEATURE_NAMES)]


def logistic_search(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    *,
    trials: int = TRIALS,
    seed: int = TUNING_SEED,
) -> tuple[Pipeline, Trial]:
    """The best of `trials` draws of the inverse regularization strength, judged on
    validation.

    The imputer is inside the pipeline and fitted on train, because a median read from
    validation or test would be that block's own distribution (owner, 2026-10-05; docs/08
    §Reglas de gobierno, "Frecuencia real"). `class_weight` is fixed and not drawn:
    weighing the classes is a modelling decision, and a decision tuned on validation would
    be the validation block being fitted.
    """
    generator = np.random.default_rng(seed)
    labels = validation["label"].to_numpy(dtype=np.int64)
    best: tuple[Trial, Pipeline] | None = None
    for number in range(1, trials + 1):
        strength = float(np.exp(generator.uniform(*np.log(LOGISTIC_RANGE))))
        pipeline = Pipeline(
            [
                ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
                (
                    "model",
                    LogisticRegression(
                        C=strength,
                        class_weight="balanced",
                        max_iter=1000,
                        random_state=seed,
                    ),
                ),
            ]
        )
        pipeline.fit(design(train), train["label"].to_numpy(dtype=np.int64))
        scores = positive_class(pipeline.predict_proba(design(validation)))
        trial = Trial(
            number=number,
            params={"C": strength},
            pr_auc=pr_auc(labels, scores),
            brier=brier(labels, scores),
        )
        if best is None or trial.pr_auc > best[0].pr_auc:
            best = (trial, pipeline)
    if best is None:
        raise ValueError("the logistic search drew no trial; the budget is never zero")
    return best[1], best[0]


def lightgbm_search(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    *,
    trials: int = TRIALS,
    seed: int = TUNING_SEED,
) -> tuple[LGBMClassifier, Trial]:
    """The best of `trials` draws of the booster's noise hyperparameters, early-stopped on
    validation average precision.

    LightGBM reads a missing value natively, so there is no imputation step here (owner,
    2026-10-05) — and there must not be one: a column the booster is allowed to split on a
    "missing" branch for carries information a median would have thrown away.
    """
    generator = np.random.default_rng(seed)
    labels = validation["label"].to_numpy(dtype=np.int64)
    best: tuple[Trial, LGBMClassifier] | None = None
    for number in range(1, trials + 1):
        params = _draw(generator)
        booster = LGBMClassifier(
            objective="binary",
            n_estimators=TREE_BUDGET,
            random_state=seed,
            verbosity=-1,
            subsample_freq=SUBSAMPLE_FREQUENCY,
            **params,
        )
        # `eval_X`/`eval_y` and not `eval_set`: `eval_set` is deprecated in the locked
        # lightgbm 4.7 (lightgbm/sklearn.py:552) and would warn on every one of the trials.
        booster.fit(
            design(train),
            train["label"].to_numpy(dtype=np.int64),
            eval_X=design(validation),
            eval_y=labels,
            eval_metric="average_precision",
            callbacks=[
                early_stopping(EARLY_STOPPING_ROUNDS, first_metric_only=True, verbose=False)
            ],
        )
        scores = positive_class(booster.predict_proba(design(validation)))
        rounds = int(booster.best_iteration_ or TREE_BUDGET)
        trial = Trial(
            number=number,
            params=params,
            pr_auc=pr_auc(labels, scores),
            brier=brier(labels, scores),
            rounds=rounds,
            note=(
                f"quasirandom draw {number}, seed {seed}, {rounds} rounds kept by early "
                "stopping on validation"
            ),
        )
        if best is None or trial.pr_auc > best[0].pr_auc:
            best = (trial, booster)
    if best is None:
        raise ValueError("the LightGBM search drew no trial; the budget is never zero")
    return best[1], best[0]


def positive_class(matrix: Any) -> npt.NDArray[np.float64]:
    """The positive class of a `(n, 2)` probability matrix, whatever the estimator's own
    return type says it is."""
    return np.asarray(matrix, dtype=np.float64)[:, 1]


def pr_auc(labels: npt.NDArray[np.int64], scores: npt.NDArray[np.float64]) -> float:
    """The average precision of a ranking over these labels (docs/08 §M2 "Uso operativo")."""
    return float(average_precision_score(labels, scores))


def brier(labels: npt.NDArray[np.int64], scores: npt.NDArray[np.float64]) -> float:
    """The mean squared error of the probability itself, the calibration half of the gate."""
    return float(brier_score_loss(labels, scores))


def _draw(generator: np.random.Generator) -> dict[str, Any]:
    """One draw of `LIGHTGBM_RANGES`: linear for the two fractions, log-uniform for the
    ratios, and rounded to an int for the two counts."""
    drawn: dict[str, Any] = {}
    for name, (low, high) in LIGHTGBM_RANGES.items():
        value = (
            float(generator.uniform(low, high))
            if name in LINEAR_HYPERPARAMETERS
            else float(np.exp(generator.uniform(*np.log((low, high)))))
        )
        drawn[name] = int(round(value)) if name in INTEGER_HYPERPARAMETERS else value
    return drawn
