"""The M2 experiment cycle: the ladder of rungs and the search over their noise
hyperparameters (ADR-0020 steps 5-6).

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

`run_ladder` judges every rung on the same validation block and `select` picks the one the
gate sees: the highest validation PR-AUC, with the Brier as the tie (owner, 2026-10-05).
`pr_auc_ci95` is the interval the register carries for that score. It is not
`harness.metrics.paired_improvement_ci95`, which is the interval of a *difference* and is
the gate's number: the harness is locked and holds no bootstrap of an absolute PR-AUC. It
reuses the harness's `BOOTSTRAP_SEED` and `BOOTSTRAP_RESAMPLES`, so both intervals of the
same candidate are resamples of one rule, and it left out — rather than read as a zero — any
resample that holds one class only.

`append_register` writes one row of `ml/experiments/log.csv` per rung, with the header the
file was scaffolded with. Every rung is written, including the ones that lose: ADR-0020
("Todo experimento del agente queda en el registro con su hipótesis, aunque no mejore") and
a register that holds only the winner is a register nobody can tell apart from one where the
other twelve draws never ran. There is no column for a test metric and none is added: the
header is fixed, and a column of test numbers would be a place for leakage to accumulate.

`design` is the shared feature contract and nothing else, because that is exactly what
`harness.promotion._design` hands to `predict_proba` at the gate.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
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

from techcamp_ml.harness.metrics import BOOTSTRAP_RESAMPLES, BOOTSTRAP_SEED, Interval
from techcamp_ml.harness.promotion import Scored
from techcamp_ml.models.flood_m2.baselines import ClimatologyBaseline, RainfallBaseline

BASELINE = "baseline"
MODEL = "model"
"""Which ladder a rung belongs to. The gate's `baseline` argument is the best `BASELINE` and
its `candidate` argument is the best `MODEL`; a baseline can never be its own baseline, and
the gate has one slot for each."""

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

LOG_PATH = Path(__file__).resolve().parents[4] / "experiments" / "log.csv"
"""`ml/experiments/log.csv`, the register ADR-0020 step 5 names. Its header was scaffolded
with T1 and is not this module's to change."""

LOG_COLUMNS: tuple[str, ...] = (
    "id",
    "date",
    "hypothesis",
    "change",
    "model",
    "val_pr_auc",
    "val_pr_auc_ci_low",
    "val_pr_auc_ci_high",
    "val_brier",
    "decision",
    "note",
)
"""The header as T1 wrote it. `DECIMALS` figures, and never a test column."""

BASELINE, CANDIDATE, DISCARDED = "baseline", "candidate", "discarded"
DECISIONS: tuple[str, ...] = (BASELINE, CANDIDATE, DISCARDED)
"""What the run did with a rung: the one the gate compares against, the one it promotes
nothing yet, and every other. A rung that was fitted and not kept is still in the register
with the reason, which is what makes the discard auditable."""

DECIMALS = 6
"""Figures per metric. Six is more than the gate's Brier needs and few enough that the same
run produces byte-identical rows on any platform (docs/08 §Reglas de gobierno,
"Reproducible o no existe")."""

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


@dataclass(frozen=True, slots=True)
class Search:
    """One rung of the ladder, fitted and judged: the scorer and the numbers it earned.

    `scorer` is the `harness.promotion.Scored` protocol, so the rung the gate receives is
    the same object the ladder ranked, not a copy that could answer differently.
    """

    name: str
    kind: str
    scorer: Scored
    pr_auc: float
    interval: Interval
    brier: float
    params: Mapping[str, Any] = field(default_factory=dict)
    rounds: int | None = None
    note: str = ""

    def score(self, features: pd.DataFrame) -> npt.NDArray[np.float64]:
        """The positive class of the rung's two-column matrix, for a caller that wants the
        number rather than the matrix."""
        return positive_class(self.scorer.predict_proba(design(features)))


def run_ladder(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    *,
    trials: int = TRIALS,
    seed: int = TUNING_SEED,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> tuple[Search, ...]:
    """Every rung of docs/08 §M2 "Escalera", judged on the same validation block.

    The two baselines come first and the two models after, so the ladder is a table and not
    a claim; the order of the result is the selection rule of `select`, best first. Train is
    read for fitting and validation for judging, and nothing here can see the test block.

    `resamples` is the register's interval budget and defaults to the harness's own; the
    tests pass a smaller number for speed, because a fixture's interval is not a number
    anyone is going to act on.
    """
    labels = validation["label"].to_numpy(dtype=np.int64)
    rungs: list[Search] = []
    for name, estimator in (
        ("climatology_month", ClimatologyBaseline.fit(train)),
        ("rainfall_6m", RainfallBaseline.fit(train)),
    ):
        rungs.append(
            _scored(
                name,
                BASELINE,
                estimator,
                validation,
                labels,
                resamples,
                note="fitted on train only",
            )
        )
    for name, kind, scorer, trial in (
        (
            "logistic_regression",
            MODEL,
            *logistic_search(train, validation, trials=trials, seed=seed),
        ),
        ("lightgbm", MODEL, *lightgbm_search(train, validation, trials=trials, seed=seed)),
    ):
        rungs.append(
            _scored(
                name,
                kind,
                scorer,
                validation,
                labels,
                resamples,
                params=trial.params,
                rounds=trial.rounds,
                note=trial.note,
            )
        )
    return tuple(sorted(rungs, key=lambda rung: (-rung.pr_auc, rung.brier)))


def select(rungs: Sequence[Search], kind: str) -> Search:
    """The single rung of `kind` that goes to the gate: the highest validation PR-AUC, with
    the Brier as the tie (owner, 2026-10-05).

    The tie rule matters. The gate's second rule is `Brier no peor`, so handing it the less
    calibrated of two equally ranked rungs would let a promotion be decided on a rule that
    had already run, and refusing it on the Brier afterwards would be the harness working
    rather than a decision the experiment made.
    """
    of_kind = [rung for rung in rungs if rung.kind == kind]
    if not of_kind:
        raise ValueError(
            f"the ladder holds no {kind}, only {sorted({rung.kind for rung in rungs})}; the "
            "gate needs one baseline and one candidate, and a missing rung is not a score "
            "of zero"
        )
    return min(of_kind, key=lambda rung: (-rung.pr_auc, rung.brier))


def pr_auc_ci95(
    labels: npt.NDArray[np.int64],
    scores: npt.NDArray[np.float64],
    *,
    seed: int = BOOTSTRAP_SEED,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> Interval:
    """The PR-AUC of one rung with its bootstrap IC95, read on the same block.

    Same seed and resample count as the harness, and the same rule about a resample holding
    one class only: it is left out rather than read as a zero average precision, which would
    drag the lower bound under a score that really happened.
    """
    generator = np.random.default_rng(seed)
    values: list[float] = []
    for _ in range(resamples):
        rows = generator.integers(0, labels.size, labels.size)
        sampled = labels[rows]
        if not sampled.any() or sampled.all():
            continue
        values.append(pr_auc(sampled, scores[rows]))
    if not values:
        raise ValueError(
            f"none of the {resamples} resamples held both classes; the block is too small "
            "to put an interval around its average precision"
        )
    tail = 0.025
    return Interval(
        point=pr_auc(labels, scores),
        lower=float(np.quantile(values, tail)),
        upper=float(np.quantile(values, 1.0 - tail)),
    )


def _scored(
    name: str,
    kind: str,
    scorer: Any,
    validation: pd.DataFrame,
    labels: npt.NDArray[np.int64],
    resamples: int,
    *,
    params: Mapping[str, Any] | None = None,
    rounds: int | None = None,
    note: str = "",
) -> Search:
    scores = positive_class(scorer.predict_proba(design(validation)))
    return Search(
        name=name,
        kind=kind,
        scorer=scorer,
        pr_auc=pr_auc(labels, scores),
        interval=pr_auc_ci95(labels, scores, resamples=resamples),
        brier=brier(labels, scores),
        params=dict(params or {}),
        rounds=rounds,
        note=note,
    )


@dataclass(frozen=True, slots=True)
class Entry:
    """One row of the register: a hypothesis, the one change it made, and what validation
    said."""

    date: str
    hypothesis: str
    change: str
    model: str
    val_pr_auc: float
    interval: Interval
    val_brier: float
    decision: str
    note: str
    id: int = 0

    def row(self) -> dict[str, str]:
        """The row as the CSV holds it, `id` included."""
        if self.decision not in DECISIONS:
            raise ValueError(
                f"the decision {self.decision!r} is not one of {list(DECISIONS)}; a register "
                "row has to say what the run did with the rung"
            )
        return {
            "id": str(self.id),
            "date": self.date,
            "hypothesis": self.hypothesis,
            "change": self.change,
            "model": self.model,
            "val_pr_auc": _figure(self.val_pr_auc),
            "val_pr_auc_ci_low": _figure(self.interval.lower),
            "val_pr_auc_ci_high": _figure(self.interval.upper),
            "val_brier": _figure(self.val_brier),
            "decision": self.decision,
            "note": self.note,
        }


def entry_for(
    rung: Search,
    *,
    date: str,
    hypothesis: str,
    change: str,
    decision: str,
    note: str = "",
) -> Entry:
    """One rung as one register row: its validation score, its interval, and what the run
    did with it."""
    return Entry(
        date=date,
        hypothesis=hypothesis,
        change=change,
        model=rung.name,
        val_pr_auc=rung.pr_auc,
        interval=rung.interval,
        val_brier=rung.brier,
        decision=decision,
        note="; ".join(part for part in (rung.note, note) if part),
    )


def append_register(path: Path, entry: Entry) -> Entry:
    """Append one row, creating the register with its fixed header when it is not there yet.

    Returns the row as written, with the id the file gave it: ids are assigned by the file,
    so two runs cannot claim the same one and a row's place in the register is its id and
    not its line number.

    A header that is not `LOG_COLUMNS` is refused rather than extended. The header was
    scaffolded with T1 and is the shape every reader of the register expects; adding a
    column here would make two readers disagree about what a row means, and the natural
    column to add is a test metric.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.is_file()
    last = 0
    if not fresh:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            rows = list(reader)
        if header is not None and tuple(header) != LOG_COLUMNS:
            raise ValueError(
                f"{path} opens with {header} and the register is read with "
                f"{list(LOG_COLUMNS)}; a header that changed under its readers is not a "
                "register"
            )
        last = max((int(row[0]) for row in rows if row and row[0].isdigit()), default=0)
    numbered = replace(entry, id=last + 1)
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(LOG_COLUMNS), lineterminator="\n")
        if fresh:
            writer.writeheader()
        writer.writerow(numbered.row())
    return numbered


def _figure(value: float) -> str:
    return f"{value:.{DECIMALS}f}"


def refit(rung: Search, train: pd.DataFrame) -> Any:
    """The same configuration as `rung`, fitted again on other rows.

    The robustness report of ADR-0020 step 9 has to measure the candidate the ladder chose,
    seven times, on a department held out each time — so it needs the candidate's own
    hyperparameters without its fitted coefficients. `train` here is the fold's train: it is
    development data, never the blocked test, and it holds rows of both periods by design
    (`harness.split.department_holdouts`).
    """
    labels = train["label"].to_numpy(dtype=np.int64)
    if rung.name == "logistic_regression":
        pipeline = Pipeline(
            [
                ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
                (
                    "model",
                    LogisticRegression(
                        C=float(rung.params["C"]),
                        class_weight="balanced",
                        max_iter=1000,
                        random_state=TUNING_SEED,
                    ),
                ),
            ]
        )
        pipeline.fit(design(train), labels)
        return pipeline
    booster = LGBMClassifier(
        objective="binary",
        n_estimators=int(rung.rounds) if rung.rounds is not None else TREE_BUDGET,
        random_state=TUNING_SEED,
        verbosity=-1,
        subsample_freq=SUBSAMPLE_FREQUENCY,
        **rung.params,
    )
    booster.fit(design(train), labels)
    return booster
