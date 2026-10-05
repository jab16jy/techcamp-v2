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
from techcamp_ml.harness.split import TEST_FIRST, TEST_LAST

CI_LOWER_BOUND_ABOVE_ZERO = "improvement_ic95_lower_bound_not_above_zero"
BRIER_NOT_WORSE = "brier_worse_than_the_best_baseline"
"""The two reasons this gate can refuse a candidate, in the order docs/08 names them. They
are slugs rather than sentences so a report can be compared across candidates without
re-reading prose, and neither of them is `None`."""


@dataclass(slots=True)
class SpentTestBlocks:
    """The test blocks one harness run has already spent.

    This is harness state and it is owned by the caller, not by the process: a module-level
    collection would outlive the run that made it and would refuse an unrelated later run
    that happens to score the same block. What makes it a guard rather than a suggestion is
    `_SPENT_READS`: the same spend is recorded on the table itself, so a second ledger over
    the same rows does not buy a second answer.

    What is stored is a digest of the block's content, never a row of the block itself.
    """

    digests: set[str] = field(default_factory=set)


_SPENT_READS = "techcamp_ml.spent_test_blocks"
"""`table.attrs` key under which the gate records the blocks it has read.

The spend rides on the frame and not on the ledger object because a ledger the caller owns
is a ledger the caller can re-mint: `SpentTestBlocks` scopes the run, this scopes the data,
and only the data is what "one read of the test set" is counted over. `attrs` is pandas'
own metadata channel and it travels with `copy()`, slicing and `concat`, so a rebuilt copy
of the same table carries the receipt it earned."""


def _read_once(blocked: pd.DataFrame, table: pd.DataFrame, reads: SpentTestBlocks) -> None:
    """Spend this block's single read, or refuse a second one over the same rows.

    The key is the **content** of the blocked rows, never the identity of the frame they
    arrived in. That distinction is the whole guard: `table.copy()` is a different object
    with a different `id` holding exactly the same rows, so an identity key lets a second
    read through by construction — and copying a frame is precisely what an agent reaching
    for the test twice would do (ADR-0020: "el agente no puede modificar ... el dataset de
    test ni la compuerta"). Rows are put in a canonical order before they are hashed, so
    presenting the same block reordered is the same read too.

    The spend is written twice on purpose — into the run's ledger and into the table — so
    that neither a re-minted ledger nor a re-minted frame is a way through. docs/08
    §Reglas de gobierno, "Test intocable", allows one read of the test block; a caller who
    constructs another `SpentTestBlocks` for the same table has built another object, not
    bought another answer.

    Called before `_design` builds anything, so a repeat call is refused while the rows are
    still just rows: no design matrix is built and no scorer is asked anything.
    """
    digest = _block_digest(blocked)
    spent: frozenset[str] = frozenset(table.attrs.get(_SPENT_READS, ()))
    if digest in spent or digest in reads.digests:
        raise ValueError(
            "the blocked test block has already been read, and docs/08 §Reglas de gobierno, "
            '"Test intocable", allows one read per final candidate; a second answer over the '
            "same rows is iterating against the test set"
        )
    reads.digests.add(digest)
    table.attrs[_SPENT_READS] = spent | {digest}


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
    _read_once(blocked, table, reads)
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
    # of the whole label window rather than the fullest one, because the last month of a
    # window is incomplete by construction and comparing against the maximum would refuse
    # legitimate datasets. The window is the reference because it is what the dataset itself
    # says a month holds, so nothing about the shape of the data has to be maintained here.
    counts = months.value_counts()
    typical = int(counts.mode().iloc[0])
    thin = sorted(str(month) for month in expected if int(counts.get(month, 0)) < typical)
    if thin:
        raise ValueError(
            f"the complete labelled year {TEST_FIRST} to {TEST_LAST} is thin in {len(thin)} "
            f"of its {len(expected)} months ({', '.join(thin[:3])}), each holding fewer than "
            f"the {typical} rows a typical month of the label window holds; a thinned month is "
            "a truncated block wearing a complete one's shape"
        )

    # A row count is manufactured by copying a row: duplicate the one municipality-month a
    # thinned month kept and its count is back at the typical one, with the municipalities
    # still missing. docs/08 §M2 "Unidad" is one row per municipality and month, so a month
    # that holds one twice is not the same block wearing a bigger hat — it is a different
    # dataset, and the gate has no authority over what to make of it.
    repeated = blocked.duplicated(subset=["code", "horizon_start"], keep=False)
    if repeated.any():
        offenders = blocked.loc[repeated, ["code", "horizon_start"]].drop_duplicates()
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
