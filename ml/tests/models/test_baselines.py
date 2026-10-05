"""The climatology and the rainfall rule, the first two rungs of the M2 ladder.

The tables are small on purpose: every fraction below is a whole number of rows over a round
denominator, so a reader can check it without a calculator. A baseline's whole claim *is*
that number.

The prevalence here is 7 positives over 100 rows — nothing like the real 5,12 % and nothing
like the harness fixture's 1,19 %. This file tests *what the rule answers*, never a
threshold: a threshold read off a fixture frequency is a number the real table never had.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from techcamp.risk.domain.features import FEATURE_NAMES, seasonality

from techcamp_ml.harness.promotion import Scored
from techcamp_ml.models.flood_m2.baselines import (
    RAINFALL_COLUMN,
    ClimatologyBaseline,
    RainfallBaseline,
    calendar_month,
)

TRAIN_ROWS = 100
RAINFALL_STEP = 10.0
"""Ten millimetres apart, so the quintile edges of train are 198, 396, 594 and 792 mm and
bin `k` holds the twenty rows whose rank is `20k .. 20k+19`."""

FLOOD_INDICES = (50, 62, 70, 74, 80, 88, 94)
"""Seven floods, chosen so each rainfall bin has its own frequency:

* index `i` belongs to calendar month `i % 4 + 1`, so months 2 and 4 hold no flood at all
  and months 1 and 3 hold 2 and 5 of their 25 rows;
* the quintile edges fall at 198, 396, 594 and 792 mm, so the floods land in bin 2 (one),
  bin 3 (three) and bin 4 (three), and bins 0 and 1 hold none.
"""

MONTHLY_FLOODS = {1: 2, 3: 5}
"""What each flooded month holds out of its 25 train rows: 2/25 and 5/25."""

BIN_FREQUENCIES = {0: 0.0, 1: 0.0, 2: 1 / 20, 3: 3 / 20, 4: 3 / 20}
"""The empirical frequency of each bin. The rule answers the running maximum of these, which
here is itself, because the bins already rise."""


def _features(months: list[int], rainfall: list[float], labels: list[int]) -> pd.DataFrame:
    rows = []
    for number, rain, label in zip(months, rainfall, labels, strict=True):
        sin, cos = seasonality(number)
        row: dict[str, object] = {name: 0.0 for name in FEATURE_NAMES}
        row.update({RAINFALL_COLUMN: rain, "month_sin": sin, "month_cos": cos, "label": label})
        rows.append(row)
    return pd.DataFrame(rows)


def train_frame() -> pd.DataFrame:
    """100 train rows: the calendar month cycles 1..4, the rain rises with the row, and the
    floods are the seven of `FLOOD_INDICES`."""
    months = [(index % 4) + 1 for index in range(TRAIN_ROWS)]
    rainfall = [index * RAINFALL_STEP for index in range(TRAIN_ROWS)]
    labels = [int(index in FLOOD_INDICES) for index in range(TRAIN_ROWS)]
    return _features(months, rainfall, labels)


def validation_frame() -> pd.DataFrame:
    """Five validation rows: one per calendar month, rain from nothing to a flood."""
    return _features([1, 3, 6, 12, 12], [0.0, 900.0, 400.0, 100.0, 300.0], [0, 1, 0, 0, 0])


def test_the_climatology_answers_the_frequency_of_its_calendar_month() -> None:
    baseline = ClimatologyBaseline.fit(train_frame())

    assert baseline.by_month[1] == pytest.approx(MONTHLY_FLOODS[1] / 25)
    assert baseline.by_month[3] == pytest.approx(MONTHLY_FLOODS[3] / 25)
    assert baseline.prevalence == pytest.approx(len(FLOOD_INDICES) / TRAIN_ROWS)


def test_a_month_train_never_flooded_answers_zero_and_not_the_prevalence() -> None:
    """February and April hold 25 train rows and no flood: their frequency really is 0.0,
    and the fallback is not what a month with evidence gets."""
    baseline = ClimatologyBaseline.fit(train_frame())
    scores = baseline.score(validation_frame())

    assert baseline.by_month[2] == 0.0
    assert baseline.by_month[4] == 0.0
    assert baseline.prevalence > 0.0
    assert scores[0] == pytest.approx(MONTHLY_FLOODS[1] / 25)


def test_a_validation_row_takes_the_frequency_of_its_own_month() -> None:
    baseline = ClimatologyBaseline.fit(train_frame())

    scores = baseline.score(validation_frame())

    assert scores[0] == pytest.approx(MONTHLY_FLOODS[1] / 25)
    assert scores[1] == pytest.approx(MONTHLY_FLOODS[3] / 25)


def test_a_month_train_never_saw_falls_back_to_the_prevalence_not_to_zero() -> None:
    """June and December are not in train: the answer is the block's prevalence, never 0.0
    ("never floods") and never 1.0 ("always floods")."""
    baseline = ClimatologyBaseline.fit(train_frame())

    scores = baseline.score(validation_frame())

    assert scores[2] == pytest.approx(len(FLOOD_INDICES) / TRAIN_ROWS)
    assert scores[3] == pytest.approx(len(FLOOD_INDICES) / TRAIN_ROWS)
    assert 0.0 < scores[3] < 1.0


def test_the_calendar_month_is_recovered_from_the_rounded_pair() -> None:
    """The harness fixture rounds the pair to six decimals, so the month has to survive it."""
    rounded = pd.DataFrame(
        [
            {"month_sin": round(sin, 6), "month_cos": round(cos, 6)}
            for sin, cos in (seasonality(month) for month in range(1, 13))
        ]
    )

    assert calendar_month(rounded).tolist() == list(range(1, 13))


def test_december_is_read_as_december_and_not_as_january() -> None:
    """The negative of the nearest-pair rule: the pair an angle inversion confuses most."""

    assert calendar_month(validation_frame()).tolist() == [1, 3, 6, 12, 12]


def test_the_rainfall_rule_answers_the_frequency_of_its_bin() -> None:
    baseline = RainfallBaseline.fit(train_frame())
    frame = _features([5] * 5, [0.0, 250.0, 500.0, 700.0, 800.0], [0] * 5)

    scores = baseline.score(frame)

    assert [pytest.approx(frequency) for frequency in BIN_FREQUENCIES.values()] == list(scores)


def test_the_rainfall_rule_never_answers_less_as_the_rain_rises() -> None:
    """The negative of the rule's own assumption: a wetter month can never be safer."""
    baseline = RainfallBaseline.fit(train_frame())
    frame = _features([5] * 6, [0.0, 199.0, 200.0, 999.0, 5000.0, 6000.0], [0] * 6)

    scores = baseline.score(frame)

    assert list(scores) == sorted(scores)
    assert scores[0] == 0.0
    assert scores[3] > scores[0]


def test_rain_beyond_every_train_bin_takes_the_wettest_bin_not_one() -> None:
    baseline = RainfallBaseline.fit(train_frame())
    frame = _features([5, 5], [9000.0, 0.0], [0, 0])

    scores = baseline.score(frame)

    assert scores[0] == pytest.approx(float(baseline.frequencies[-1]))
    assert scores[0] < 1.0
    assert scores[0] > scores[1]


def test_a_row_with_no_rainfall_is_imputed_with_the_train_median_not_dropped() -> None:
    """Owner, 2026-10-05: the imputer's median is fitted on train only. A month the archive
    never answered is still a municipality-month the model has to answer for."""
    baseline = RainfallBaseline.fit(train_frame())
    median = (TRAIN_ROWS - 1) * RAINFALL_STEP / 2
    frame = _features([5, 5], [float("nan"), median], [0, 0])

    scores = baseline.score(frame)

    assert scores.shape == (2,)
    assert scores[0] == pytest.approx(scores[1])
    assert np.isfinite(scores).all()


def test_a_baseline_answers_the_two_column_matrix_the_gate_scores() -> None:
    """`harness.promotion._positive_class` refuses anything but finite values in [0, 1] whose
    rows sum to one, so a baseline that answered a hard label would be refused by the gate
    rather than judged."""
    train, validation = train_frame(), validation_frame()
    scorers = [ClimatologyBaseline.fit(train), RainfallBaseline.fit(train)]

    for scorer in scorers:
        assert isinstance(scorer, Scored)
        matrix = scorer.predict_proba(validation)
        assert matrix.shape == (len(validation), 2)
        assert np.isfinite(matrix).all()
        assert ((matrix >= 0.0) & (matrix <= 1.0)).all()
        assert np.allclose(matrix.sum(axis=1), 1.0)
        assert np.array_equal(matrix[:, 1], scorer.score(validation))


def test_a_frame_without_the_shared_contract_is_refused() -> None:
    baseline = ClimatologyBaseline.fit(train_frame())

    with pytest.raises(ValueError, match="missing"):
        baseline.score(pd.DataFrame({"month_sin": [0.0]}))


def test_a_train_block_with_no_rainfall_at_all_is_refused() -> None:
    """A rule with no train evidence is a guess, and the guess it would make is 0.0."""
    frame = train_frame()
    frame[RAINFALL_COLUMN] = float("nan")

    with pytest.raises(ValueError, match="no bin to"):
        RainfallBaseline.fit(frame)
