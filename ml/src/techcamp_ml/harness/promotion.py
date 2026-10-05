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

import hashlib
from dataclasses import dataclass, field
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
from techcamp_ml.harness.split import TEST_FIRST, TEST_LAST, TRAIN_LAST

CI_LOWER_BOUND_ABOVE_ZERO = "improvement_ic95_lower_bound_not_above_zero"
BRIER_NOT_WORSE = "brier_worse_than_the_best_baseline"
"""The two reasons this gate can refuse a candidate, in the order docs/08 names them. They
are slugs rather than sentences so a report can be compared across candidates without
re-reading prose, and neither of them is `None`."""


@dataclass(slots=True)
class SpentTestBlocks:
    """The test blocks one harness run has already spent.

    This is harness state and it is owned by the caller, not by the process: a module-level
    collection would outlive the run that made it. It is therefore the run's record and not
    the guard: what stops a second read is `_SPENT_READS`, which lives in the harness and is
    keyed by the content of the rows, so neither a second ledger nor a second frame buys an
    answer the first one already gave.

    What is stored is a digest of the block's content, never a row of the block itself.
    """

    digests: set[str] = field(default_factory=set)


_SPENT_READS: set[str] = set()
"""The content digests of the blocked test blocks this process has already read.

The receipt lives here rather than on anything the caller holds, because both of the
alternatives are things the caller can drop: a ledger object can be re-minted, and a
`table.attrs` entry can be cleared with `table.attrs.clear()`. A guard the caller disarms by
discarding what it owns is not a guard.

It is keyed by **content**, so the three ways of presenting the same block again — the same
frame, a copy, and a frame rebuilt out of the same rows — are one read and not three. That is
the whole of docs/08 §Reglas de gobierno, "Test intocable": one read of the test block for the
final candidate, and the gate is only ever called for that candidate."""


def _forget_spent_reads() -> None:
    """Empty the process-level read receipt. **Out of contract.**

    Every test in this module builds the same block from the same fixture, so each of them is
    legitimately a first read and the process-level receipt has to be emptied around them.
    Thirty independent cases over one fixture is not thirty reads of the test set, and refusing
    all but the first would be the guard working rather than a fault to route around by running
    each case in a separate interpreter.

    It is here because of that, and not as an affordance. A caller holding a block it has not
    read gains nothing here, and reaching for it is modifying the harness — which ADR-0020
    ("el agente no puede modificar el harness, el dataset de test ni la compuerta"),
    `CODEOWNERS` and `ml/harness/LOCK.sha256` already forbid at review and hash-check time. The
    guard therefore holds against every way of reaching the block through the gate's own API:
    a second ledger, a cleared frame, a rebuilt frame, a reordered frame. The one move left is
    not part of that API, and the repository already refuses it.
    """
    _SPENT_READS.clear()


def _read_once(blocked: pd.DataFrame, reads: SpentTestBlocks) -> None:
    """Spend this block's single read, or refuse a second one over the same rows.

    The key is the **content** of the blocked rows, never the identity of the frame they
    arrived in, and never the object the receipt is written to. `table.copy()` is a different
    object with a different `id` holding exactly the same rows, and a frame rebuilt from those
    rows is not even the same object; both are the read that was already spent. Rows are put
    in a canonical order before they are hashed, so presenting the same block reordered is
    the same read too.

    Called before `_design` builds anything, so a repeat call is refused while the rows are
    still just rows: no design matrix is built and no scorer is asked anything.
    """
    digest = _block_digest(blocked)
    if digest in _SPENT_READS or digest in reads.digests:
        raise ValueError(
            "the blocked test block has already been read, and docs/08 §Reglas de gobierno, "
            '"Test intocable", allows one read per final candidate; a second answer over the '
            "same rows is iterating against the test set"
        )
    _SPENT_READS.add(digest)
    reads.digests.add(digest)


def _block_digest(blocked: pd.DataFrame) -> str:
    """A content key for the blocked block, canonical over row order and index.

    Only the label and the shared feature columns are hashed, because those are the columns
    the decision is made of; hashing the whole frame would let an unrelated column decide
    whether two identical blocks count as the same read.
    """
    columns = [LABEL_COLUMN, *FEATURE_NAMES]
    canonical = blocked[columns].sort_values(by=columns, kind="mergesort")
    return hashlib.sha256(
        pd.util.hash_pandas_object(canonical, index=False).to_numpy().tobytes()
    ).hexdigest()


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
    reads: SpentTestBlocks,
) -> PromotionDecision:
    """Run the gate of ADR-0020 step 8 once over the blocked test block.

    `baseline` is the **best** baseline of the validation block (docs/08 §Reglas de gobierno
    names that one, not the trivial one), and it is scored on the same rows as the candidate
    so the paired interval compares like with like.
    """
    blocked = _test_block(table)
    _read_once(blocked, reads)
    labels, features = _design(blocked)
    # One frame each, because the paired interval below is only a comparison if both scorers
    # answered the same rows. Sharing one let a candidate that writes in place decide what the
    # baseline was measured against, and the pairing stopped pairing.
    candidate_scores = _positive_class(candidate.predict_proba(features.copy()), features)
    baseline_scores = _positive_class(baseline.predict_proba(features.copy()), features)

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


def _test_block(table: pd.DataFrame) -> pd.DataFrame:
    """The blocked block itself, proved to be the whole of the labelled year.

    It returns the frame rather than the model-facing arrays on purpose: `_read_once` has to
    be able to refuse a second read before a design matrix exists, so the two steps are kept
    apart. What is checked here is that the block is the complete declared year and not a
    fragment of it — every metric below answers happily on whatever rows it is handed, and two
    rows carrying one positive and one negative are enough for a PR-AUC, a paired interval and
    a Brier, so the gate could return `promote` off an arbitrarily incomplete test set.
    """
    _require(table, ("code", *FEATURE_NAMES, LABEL_COLUMN))
    months = pd.PeriodIndex(table["horizon_start"], freq="M")
    blocked = table[
        (months >= pd.Period(TEST_FIRST, freq="M")) & (months <= pd.Period(TEST_LAST, freq="M"))
    ]
    if blocked.empty:
        raise ValueError(
            f"the dataset holds no row between {TEST_FIRST} and {TEST_LAST}; the gate has "
            "nothing to decide on, and a gate that scored zero rows would promote anything"
        )
    _require_complete_year(blocked, months)
    return blocked


def _design(blocked: pd.DataFrame) -> tuple[npt.NDArray[np.int64], pd.DataFrame]:
    """The labels and the design matrix of the blocked block, and nothing else.

    The design matrix is the shared feature contract of T2 and not one column more: an
    identity column would let a model score a municipality it was fitted on, and a column
    the dataset does not hold would be a feature the serving job cannot build
    (docs/08 §Reglas de gobierno, "Paridad de features").
    """
    return (
        blocked[LABEL_COLUMN].to_numpy(dtype=np.int64),
        blocked[list(FEATURE_NAMES)],
    )


def _require_complete_year(blocked: pd.DataFrame, months: pd.PeriodIndex) -> None:
    """Refuse a block that is not the whole of the labelled year the test block declares.

    docs/08 §M2 "Partición" calls the test "el año etiquetado completo más reciente", and
    every number the gate reports would be computed happily on a fragment of it: a PR-AUC, a
    paired interval and a Brier all need only a nonempty frame, and two rows — one positive,
    one negative — are enough for a candidate to win all three. So completeness is checked
    here rather than left to the size of the table, because the caller does not choose it and
    must not be able to shrink it.
    """
    expected = pd.period_range(TEST_FIRST, TEST_LAST, freq="M")
    present = months[(months >= expected[0]) & (months <= expected[-1])].unique()
    missing = expected.difference(present)
    if len(missing):
        named = ", ".join(str(month) for month in missing[:3])
        raise ValueError(
            f"the test block holds {len(expected) - len(missing)} of the {len(expected)} "
            f"months of the complete labelled year {TEST_FIRST} to {TEST_LAST}, and is "
            f"missing {named}; the gate promotes off that whole year, not off a fragment of "
            "it, because every metric below would answer on whatever rows it was given"
        )

    # Presence of every month is a shape, not a population: a block thinned to a fraction of
    # the year still has all twelve months and can still decide. The bar is the **modal** month
    # of train rather than of the whole label window, because a bar read off the window is a
    # bar the block lowers for itself: thin the test year enough and the window's own mode
    # becomes the thin count, after which the block is complete by its own standard. Train is
    # the reference because it is not the scarce resource — docs/08 §M2 "Partición" gives the
    # experiment train and validation to work on and reserves the test year for this gate — and
    # the mode of a count does not depend on the size of the dataset, so the bar is the same
    # on the unit fixture and on the 195 municipalities of the real table.
    #
    # What this cannot catch is a window that is thin everywhere, train included: there the
    # mode is low because the data is, and no in-gate reference can tell a small region from a
    # truncated one. That limit belongs to the dataset build, which refuses to assemble a
    # table missing any municipality (ml/datasets/flood_m2, data card §Contrato de columnas),
    # and not to a gate that would have to trust the very rows it is judging.
    train_counts = months[months <= pd.Period(TRAIN_LAST, freq="M")].value_counts()
    typical = int(train_counts.mode().iloc[0])
    counts = months.value_counts()
    thin = sorted(str(month) for month in expected if int(counts.get(month, 0)) < typical)
    if thin:
        raise ValueError(
            f"the complete labelled year {TEST_FIRST} to {TEST_LAST} is thin in {len(thin)} "
            f"of its {len(expected)} months ({', '.join(thin[:3])}), each holding fewer than "
            f"the {typical} rows a typical month of train holds; a thinned month is "
            "a truncated block wearing a complete one's shape"
        )

    # A row count is manufactured by copying a row: duplicate one municipality-month and its
    # count is back at the typical one, with the municipalities still missing. The unit is
    # the **month**, not the timestamp, so the key is the normalized month — a second row for
    # the same municipality on another day of the same month is the same unit twice, and
    # comparing raw timestamps would wave it through (docs/08 §M2 "Unidad" is one row per
    # municipality and month).
    block_months = pd.PeriodIndex(blocked["horizon_start"], freq="M")
    unit = pd.DataFrame({"code": blocked["code"].to_numpy(), "month": block_months.to_numpy()})
    repeated = unit.duplicated(keep=False)
    if repeated.any():
        offenders = unit.loc[repeated].drop_duplicates()
        raise ValueError(
            f"the complete labelled year {TEST_FIRST} to {TEST_LAST} holds "
            f"{len(offenders)} municipality-months more than once; a duplicated row restores "
            "a count without restoring a municipality, and docs/08 §M2 'Unidad' is one row "
            "per municipality and month"
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

    The shape check is here and not in the caller because the paired interval below is only
    defined when both scorers answered every row of the same block; a scorer that answered
    a different number of rows would otherwise be compared against nothing.

    The values are checked too, and that is not pedantry: a matrix of the right shape can
    still hold numbers that are not probabilities — values off the unit interval, columns
    that do not sum to one, non-finite entries. Every metric below answers on whatever it
    is handed, so those numbers reach the Brier rule and the paired interval as
    calibration figures and decide a promotion on them. Refused here, the caller hears
    which scorer answered what; left to `roc_auc_score`, it hears a message about a metric
    two calls later and about a column of a matrix it never validated.
    """
    if probabilities.shape != (len(features), 2):
        raise ValueError(
            f"predict_proba answered {probabilities.shape} for the {len(features)} rows of "
            "the blocked test block; the gate reads the positive class of a two-column "
            "probability matrix, one row per block row"
        )
    if not np.isfinite(probabilities).all():
        raise ValueError(
            "predict_proba answered values that are not finite; a probability that is NaN or "
            "infinite cannot enter the Brier rule or the paired interval"
        )
    if not ((probabilities >= 0.0) & (probabilities <= 1.0)).all():
        raise ValueError(
            f"predict_proba answered values off the unit interval, from "
            f"{probabilities.min()} to {probabilities.max()}; the gate reads probabilities, "
            "and a number outside [0, 1] is not one"
        )
    if not np.allclose(probabilities.sum(axis=1), 1.0):
        raise ValueError(
            "predict_proba answered a row that does not sum to one; a probability matrix is "
            "one distribution per row, and an unnormalised one cannot be compared with "
            "another model's"
        )
    return probabilities[:, 1]
