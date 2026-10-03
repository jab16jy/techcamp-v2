"""Tests for the promotion gate of ADR-0020 step 8 and its single read of the test block."""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pytest
from conftest import flood_table

from techcamp_ml.harness.promotion import (
    BRIER_NOT_WORSE,
    CI_LOWER_BOUND_ABOVE_ZERO,
    PromotionDecision,
    Scored,
    decide_promotion,
)
from techcamp_ml.harness.split import TEST_FIRST, TRAIN_LAST, VAL_LAST


@dataclass
class Fixed:
    """A scored model that answers the same probability for every row.

    It records what it was asked and how often, because the gate's single pass over the
    test block is the whole point of `docs/08` §Reglas de gobierno, "Test intocable".
    """

    probability: float
    calls: list[int] = field(default_factory=list)

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        self.calls.append(len(features))
        return np.column_stack(
            [np.full(len(features), 1 - self.probability), np.full(len(features), self.probability)]
        )


@dataclass
class HeavyRain:
    """A scorer that alerts when the last month was wet, the way the heuristic baseline of
    docs/08 §M2 "Escalera" would.

    `confidence` is how loudly it says so: the ranking it gives is perfect on this fixture at
    any confidence, so the knob moves only the calibration, which is what the Brier rule of
    the gate reads. It reads only `precip_sum_1m`, which proves the design matrix the gate
    builds is the shared feature contract and nothing more: a `label` column handed over
    would be leakage.
    """

    confidence: float = 0.9
    calls: list[int] = field(default_factory=list)

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        self.calls.append(len(features))
        high = np.where(
            (features["precip_sum_1m"] > 20.0).to_numpy(), self.confidence, 1.0 - self.confidence
        )
        return np.column_stack([1.0 - high, high])


PRECEDENCE = 1 / 12
"""The climatology of the test block: one flooded month of the twelve of 2025. It is the
first step of the ladder of docs/08 §M2 "Escalera" and, answered as a constant probability,
the best baseline a season-blind model can be — a Brier of about 0.076 against 0.25 for a
coin flip, because the real frequency is 8.3% and docs/08 §Reglas de gobierno keeps it."""


def test_a_candidate_that_beats_the_baseline_on_both_rules_is_promoted() -> None:
    table = flood_table()
    candidate = HeavyRain()
    baseline = Fixed(probability=PRECEDENCE)

    decision = decide_promotion(table, candidate=candidate, baseline=baseline, resamples=400)

    assert isinstance(decision, PromotionDecision)
    assert decision.promote
    assert decision.reasons == ()
    assert decision.report.improvement.lower > 0
    assert decision.report.candidate.brier <= decision.report.baseline.brier
    assert decision.report.candidate.pr_auc > decision.report.baseline.pr_auc


def test_a_candidate_that_only_ties_the_baseline_is_not_promoted() -> None:
    """Negative: docs/08 §Reglas de gobierno wants the lower bound **above** 0, and a tie
    leaves it at 0. Two identical scorers improve by exactly nothing."""
    identical = Fixed(probability=0.5)

    decision = decide_promotion(
        flood_table(), candidate=identical, baseline=identical, resamples=200
    )

    assert not decision.promote
    assert decision.reasons == (CI_LOWER_BOUND_ABOVE_ZERO,)
    assert decision.report.improvement.lower == 0.0


def test_a_candidate_that_ranks_better_but_calibrates_worse_is_not_promoted() -> None:
    """Negative: HeavyRain still ranks every flooded month first, so its PR-AUC wins and
    its interval clears zero. At 0.6 it says 0.4 on the months it calls dry, which is
    badly calibrated against a climatology baseline, and "el Brier no puede empeorar"
    (docs/08 §Reglas de gobierno) refuses it all the same."""
    table = flood_table()
    candidate = HeavyRain(confidence=0.6)
    baseline = Fixed(probability=PRECEDENCE)

    decision = decide_promotion(table, candidate=candidate, baseline=baseline, resamples=400)

    assert decision.report.candidate.pr_auc == 1.0
    assert decision.report.improvement.lower > 0
    assert decision.report.candidate.brier > decision.report.baseline.brier
    assert not decision.promote
    assert decision.reasons == (BRIER_NOT_WORSE,)


def test_a_candidate_with_neither_a_lower_bound_nor_a_brier_is_not_promoted() -> None:
    """Negative: both rules fail at once, and the report names both."""
    decision = decide_promotion(
        flood_table(),
        candidate=Fixed(probability=0.95),
        baseline=Fixed(probability=0.02),
        resamples=200,
    )

    assert not decision.promote
    assert decision.reasons == (CI_LOWER_BOUND_ABOVE_ZERO, BRIER_NOT_WORSE)


def test_the_gate_scores_only_the_blocked_test_and_scores_it_once() -> None:
    """The one run ADR-0020 step 8 allows. The table holds every month of the window; the
    gate may only touch the twelve of the test block, and only in a single pass."""
    table = flood_table()
    candidate = HeavyRain()

    decision = decide_promotion(table, candidate=candidate, baseline=Fixed(0.5), resamples=200)
    expected = int((table["year"] == 2025).sum())

    assert candidate.calls == [expected]
    assert decision.report.test_rows == expected
    assert decision.report.test_rows < len(table)
    assert decision.report.test_positives == 4


def test_the_gate_reads_no_month_the_experiment_already_used() -> None:
    """Negative: the test block starts after the validation block, so a gate that read one
    month earlier would be deciding on something the experiment was tuned on
    (docs/08 §Reglas de gobierno, "Test intocable")."""
    table = flood_table()

    decision = decide_promotion(table, candidate=HeavyRain(), baseline=Fixed(0.5), resamples=200)

    assert decision.report.test_rows == int(
        (table["horizon_start"] >= pd.Timestamp(TEST_FIRST)).sum()
    )
    assert int((table["horizon_start"] <= pd.Timestamp(VAL_LAST)).sum()) > decision.report.test_rows
    assert int((table["horizon_start"] <= pd.Timestamp(TRAIN_LAST)).sum()) > 0


def test_the_gate_has_no_way_to_be_told_which_rows_to_score() -> None:
    """Negative: an agent may not choose the block it is judged on (ADR-0020, "el agente no
    puede modificar el harness, el dataset de test ni la compuerta")."""
    parameters = set(inspect.signature(decide_promotion).parameters)

    assert not parameters & {"rows", "months", "test_first", "test_last", "block"}


def test_there_is_no_force_promote_anywhere_in_the_gate() -> None:
    """docs/08 §Reglas de gobierno: "Sin force_promote — no existe la opción".

    Read off the tree, not off the text: this module's docstrings name the option on purpose,
    so a substring search would only prove that the documentation exists. What has to be true
    is that no name, parameter or keyword anywhere in the code can carry it.
    """
    tree = ast.parse(inspect.getsource(inspect.getmodule(decide_promotion)))
    named = (
        {
            node.id if isinstance(node, ast.Name) else node.arg
            for node in ast.walk(tree)
            if isinstance(node, (ast.Name, ast.arg))
        }
        | {
            keyword.arg
            for node in ast.walk(tree)
            if isinstance(node, ast.keyword)
            for keyword in [node]
        }
        | {
            attribute
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            for attribute in [node.attr]
        }
    )

    assert not named & {"force_promote", "force"}
    assert set(inspect.signature(decide_promotion).parameters) == {
        "table",
        "candidate",
        "baseline",
        "seed",
        "resamples",
    }


def test_the_gate_says_which_feature_columns_are_missing() -> None:
    """Negative: a table without the shared feature contract has no vectors to score, and
    naming the missing columns is what tells the caller to rebuild the dataset."""
    table = flood_table().drop(columns=["slope_deg", "month_cos"])

    with pytest.raises(ValueError, match="month_cos.*slope_deg|slope_deg.*month_cos"):
        decide_promotion(table, candidate=HeavyRain(), baseline=Fixed(0.5), resamples=200)


def test_a_table_without_the_label_column_is_refused() -> None:
    """Negative: `label` is the contract of T4 (docs/08 §M2 "Etiqueta"); without it the gate
    has nothing to score against."""
    table = flood_table().drop(columns=["label"])

    with pytest.raises(ValueError, match="label"):
        decide_promotion(table, candidate=HeavyRain(), baseline=Fixed(0.5), resamples=200)


def test_a_scorer_that_answered_hard_labels_is_refused() -> None:
    """Negative: the Brier rule reads a probability. A model that answers one column, or the
    wrong number of rows, would make the comparison meaningless rather than merely wrong."""

    class HardLabels:
        def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
            return np.zeros((len(features), 1))

    with pytest.raises(ValueError, match="two-column"):
        decide_promotion(flood_table(), candidate=HardLabels(), baseline=Fixed(0.5), resamples=200)


def test_a_scorer_that_skipped_rows_is_refused() -> None:
    """Negative: the paired interval is only defined when both scorers answered every row."""

    class Short:
        def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
            return np.column_stack([np.ones(1), np.zeros(1)])

    with pytest.raises(ValueError, match="one row per block row"):
        decide_promotion(flood_table(), candidate=Short(), baseline=Fixed(0.5), resamples=200)


def test_a_dataset_with_no_test_block_is_refused() -> None:
    """Negative: a gate that scored zero rows would answer a perfect PR-AUC on nothing."""
    table = flood_table()
    early = table[table["horizon_start"] < pd.Timestamp(TEST_FIRST)]

    with pytest.raises(ValueError, match="nothing to decide on"):
        decide_promotion(early, candidate=HeavyRain(), baseline=Fixed(0.5), resamples=200)


def test_the_gate_accepts_anything_that_answers_predict_proba() -> None:
    """The port exists because there are two real implementations of one call: an estimator
    of the ladder of docs/08 §M2 "Escalera", and the heuristic baseline the same row names
    (AGENTS.md: "A port exists only with two real implementations")."""
    assert isinstance(HeavyRain(), Scored)
    assert isinstance(Fixed(0.5), Scored)

    class NoProbabilities:
        def predict(self, features: pd.DataFrame) -> np.ndarray:
            return np.zeros(len(features))

    # Negative: a model that answers only hard labels cannot be compared by Brier, so it is
    # not a `Scored` and the gate would refuse it at the call.
    assert not isinstance(NoProbabilities(), Scored)


def test_the_report_carries_the_numbers_the_gate_decided_on() -> None:
    decision = decide_promotion(
        flood_table(), candidate=HeavyRain(), baseline=Fixed(0.5), resamples=200
    )

    report = decision.report
    assert report.test_positives > 0
    assert report.test_positives <= report.test_rows
    assert 0.0 <= report.candidate.pr_auc <= 1.0
    assert 0.0 <= report.baseline.brier <= 1.0
    assert report.improvement.lower <= report.improvement.point
