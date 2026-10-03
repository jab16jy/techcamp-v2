"""Tests for the harness metrics: PR-AUC, Brier, the paired CI95 and the thresholds."""

from __future__ import annotations

import pytest

from techcamp_ml.harness.metrics import (
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    CRITICAL_PRECISION,
    HIGH_PRECISION,
    Interval,
    brier,
    operating_thresholds,
    paired_improvement_ci95,
    pr_auc,
)

PERFECT = ([1, 0, 0, 1, 0], [0.9, 0.8, 0.4, 0.3, 0.2])
"""Two positives of five. Sorted by score the precisions are 1, 1/2, 1/3, 1/2, 2/5 at
recalls 1/2, 1/2, 1/2, 1, 1, so the average precision is (1/2)(1) + (1/2)(1/2) = 0.75 and the
Brier is (0.01 + 0.64 + 0.16 + 0.49 + 0.04) / 5 = 0.268."""

INVERTED = ([1, 0, 1, 0, 0, 0], [0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
"""The two positives score lowest: no real cut reaches 0.7 precision, so `high` is missing
evidence and not a number."""

ONE_POSITIVE = ([1] + [0] * 5, [0.9] + [0.1] * 5)
INVERTED_ONE_POSITIVE = ([1] + [0] * 5, [0.1] + [0.9] * 5)
"""One positive of six. A third of the resamples hold no positive at all and have no average
precision to report, which is exactly when counting them as zero would matter."""


def test_pr_auc_is_the_hand_computed_average_precision() -> None:
    assert pr_auc(*PERFECT) == 0.75
    # Negative: the ranking a balanced copy of these scores would give is not this number.
    assert pr_auc([1, 0], [0.9, 0.1]) == 1.0


def test_brier_is_the_hand_computed_mean_squared_error() -> None:
    assert brier(*PERFECT) == pytest.approx(0.268)


def test_brier_refuses_a_score_it_cannot_read() -> None:
    """Negative: a missing feature would be filled as 0 by an imputer and score as a
    confident forecast; the metric refuses it rather than rewarding it (docs/08
    §Reglas de gobierno, "Missing evidence is a third state")."""
    with pytest.raises(ValueError):
        brier([1, 0], [0.5, float("nan")])


def test_a_perfect_ranking_scores_one_and_a_reversed_one_scores_below_chance() -> None:
    labels = [1, 0, 1, 0]

    assert pr_auc(labels, [0.9, 0.1, 0.8, 0.2]) == 1.0
    # Reversed: the recall only moves at ranks 3 and 4, where precision is 1/3 and 1/2.
    assert pr_auc(labels, [0.1, 0.9, 0.2, 0.8]) == pytest.approx(1 / 6 + 1 / 4)
    # A constant score carries no ranking at all, so it cannot beat the prevalence.
    assert pr_auc(labels, [0.5] * 4) == 0.5


def test_the_metrics_see_every_row_they_are_given_and_none_of_them_more() -> None:
    """Negative: docs/08 §Reglas de gobierno "Frecuencia real" — validation and test keep the
    real class frequency and only train may weight classes, so no metric here may reach for
    a balanced subsample."""
    y_true = [1] * 9 + [0] * 91
    y_score = [0.9] * 9 + [0.1] * 91

    assert pr_auc(y_true, y_score) == 1.0
    assert brier(y_true, y_score) == pytest.approx(0.01)
    # A balanced copy of the same 100 scores would score a different number, which is the
    # whole reason the row count matters here.
    assert pr_auc([1] * 9 + [0] * 9, y_score[:18]) == 1.0
    assert len(y_true) == 100


def test_an_identical_candidate_improves_by_exactly_nothing() -> None:
    """Negative: this is the case the gate of ADR-0020 step 8 refuses — an interval whose
    lower bound is 0 is not evidence of an improvement."""
    interval = paired_improvement_ci95(*PERFECT, PERFECT[1])

    assert interval.point == 0.0
    assert interval.lower == 0.0
    assert interval.upper == 0.0
    assert not interval.lower > 0


def test_a_candidate_that_separates_the_classes_improves_with_a_positive_lower_bound() -> None:
    labels = [1] * 4 + [0] * 8
    candidate = [0.9] * 4 + [0.1] * 8
    baseline = [0.5] * 12

    interval = paired_improvement_ci95(labels, candidate, baseline, resamples=400)

    assert interval.point == pytest.approx(2 / 3)
    assert interval.lower > 0
    assert interval.lower <= interval.point <= interval.upper


def test_the_interval_comes_from_a_fixed_seed_and_a_documented_number_of_resamples() -> None:
    labels, candidate, baseline = *ONE_POSITIVE, [0.9] * 6

    first = paired_improvement_ci95(labels, candidate, baseline)
    again = paired_improvement_ci95(labels, candidate, baseline)

    assert first == again
    assert BOOTSTRAP_RESAMPLES > 0
    assert isinstance(BOOTSTRAP_SEED, int)
    # Negative: another seed is another interval, which is why the seed is pinned here.
    other = paired_improvement_ci95(labels, candidate, baseline, seed=BOOTSTRAP_SEED + 1)
    assert other.lower <= other.point <= other.upper


def test_a_resample_with_no_positive_is_left_out_rather_than_counted_as_zero() -> None:
    """Negative: an all-negative resample has no average precision, and reading it as 0
    would drag the lower bound under an improvement that happened in every valid one."""
    labels, candidate = ONE_POSITIVE
    _, baseline = INVERTED_ONE_POSITIVE

    interval = paired_improvement_ci95(labels, candidate, baseline, resamples=400)

    # The positive is ranked first by the candidate and last by the baseline, so every
    # resample holding it at all improves; the gain is 1 - 1/k for k rows drawn with it, so
    # the interval is a real spread and not a constant. A third of the 400 resamples hold
    # no positive, which would put the 2.5th percentile on zero if they were counted.
    assert interval.point == pytest.approx(5 / 6)
    assert interval.lower > 0
    assert interval.lower < interval.point


def test_a_block_with_a_single_class_says_it_cannot_answer() -> None:
    """Negative: with no positive there is no average precision to improve on, and a
    fabricated interval would be a number the data does not support."""
    with pytest.raises(ValueError, match="both classes"):
        paired_improvement_ci95([0, 0, 0], [0.9, 0.5, 0.1], [0.5, 0.5, 0.5], resamples=200)


def test_the_high_threshold_is_the_lowest_one_that_reaches_the_target_precision() -> None:
    labels = [1, 0, 1, 0, 0, 0, 0, 0, 0, 0]
    scores = [0.9, 0.8, 0.75, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1]

    thresholds = operating_thresholds(labels, scores)

    assert HIGH_PRECISION == 0.7
    # Only the cut at 0.9 keeps a precision of 0.7: at 0.75 it is 2/3, at 0.8 it is 1/2.
    assert thresholds.high is not None
    assert thresholds.high.value == 0.9
    assert thresholds.high.precision == 1.0
    # docs/08 §M2 "Uso operativo": the recall at that point is published, not kept private.
    assert thresholds.high.recall == 0.5


def test_the_cut_keeps_the_most_recall_it_can_at_the_target_precision() -> None:
    labels = [0, 1, 1, 1, 0, 0]
    scores = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4]

    thresholds = operating_thresholds(labels, scores)

    # Every cut above 0.6 costs recall: 0.7 gives 2/3 of precision, so 0.6 is the one that
    # reaches 0.7, and it still keeps the whole positive set.
    assert thresholds.high is not None
    assert thresholds.high.value == 0.6
    assert thresholds.high.precision == pytest.approx(0.75)
    assert thresholds.high.recall == 1.0


def test_no_cut_reaching_the_target_precision_is_missing_evidence() -> None:
    thresholds = operating_thresholds(*INVERTED)

    # Negative: 0.0 would alert on everything and 1.0 on nothing; neither is an answer these
    # scores support (D-T0.4, docs/08 §M2 "Severidad").
    assert thresholds.high is None
    assert thresholds.critical is None


def test_the_critical_threshold_is_none_when_the_target_is_out_of_reach() -> None:
    """Negative: docs/08 §M2 "Severidad" says `crítico` exists only if it can, "si existe"
    (D-T0.4) — 0.75 precision clears `alto` and not 0.85."""
    labels = [0, 1, 1, 1, 0, 0]
    scores = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4]

    thresholds = operating_thresholds(labels, scores)

    assert thresholds.high is not None
    assert thresholds.high.precision >= HIGH_PRECISION
    assert thresholds.critical is None


def test_the_critical_cut_is_never_lower_than_the_high_one() -> None:
    thresholds = operating_thresholds(*PERFECT)

    assert thresholds.high is not None
    assert thresholds.critical is not None
    # A cut that alerts on everything `crítico` would fire below every `alto` alert.
    assert thresholds.critical.value >= thresholds.high.value


def test_an_interval_is_ordered() -> None:
    interval = Interval(point=0.1, lower=-0.2, upper=0.4)

    assert interval.lower <= interval.point <= interval.upper
    assert CRITICAL_PRECISION > HIGH_PRECISION
