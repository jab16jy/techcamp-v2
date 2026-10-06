"""Calibration and the operating cut of M2 (ADR-0020 step 7, docs/08 §M2 "Severidad").

The fixture's floods are the top of the ranking, so a cut exists and can be read; what is
pinned here is the *shape* — the recall is published, a cut the block cannot reach is
`None` and produces no `critical`, and calibration never reorders the ranking. No threshold
value is asserted: a threshold read off this fixture's frequency is a number the real table
never had.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pytest
from techcamp.risk.domain.features import FEATURE_NAMES, seasonality

from techcamp_ml.models.flood_m2 import experiments
from techcamp_ml.models.flood_m2.thresholds import (
    CRITICAL,
    HIGH,
    LOW,
    CalibratedCandidate,
    read_cuts,
)

ROWS = 600
FIXTURE_SEED = 3
SIGNAL = 4
"""The rainfall's exponent in the flood probability: the wettest months flood and the dry
ones do not, but no cut of this block is perfect, which is the case `crítico` and the
published recall exist for."""


def _frame(rows: int, *, seed: int) -> pd.DataFrame:
    """`rows` municipality-months whose flood probability rises with the rainfall.

    A flood is *drawn* at that probability rather than placed by hand, so the block carries
    false positives at both ends: a perfectly separable fixture would answer precision 1.0
    at every cut and there would be nothing to distinguish `alto` from `crítico`.
    """
    generator = np.random.default_rng(seed)
    records = []
    for index in range(rows):
        rain = generator.uniform(0.0, 950.0)
        wet = generator.random() < 0.01 + 0.95 * (rain / 950.0) ** SIGNAL
        row: dict[str, object] = {name: float(generator.normal()) for name in FEATURE_NAMES}
        row["precip_sum_1m"] = rain
        row["precip_sum_6m"] = rain * 2.0
        row["month_sin"], row["month_cos"] = seasonality((index % 12) + 1)
        row["label"] = int(wet)
        records.append(row)
    return pd.DataFrame(records)


def candidate_frame() -> pd.DataFrame:
    """The validation block: 600 municipality-months at the signal's own frequency."""

    return _frame(ROWS, seed=FIXTURE_SEED)


def train_frame() -> pd.DataFrame:
    """A train of its own, from another draw of the same process."""

    return _frame(ROWS, seed=FIXTURE_SEED + 1)


def _pipeline(frame: pd.DataFrame) -> Any:
    return experiments.logistic_search(train_frame(), frame, trials=3)[0]


def _fitted(frame: pd.DataFrame) -> CalibratedCandidate:
    return CalibratedCandidate.fit(_pipeline(frame), frame)


def test_calibration_answers_the_two_column_matrix_the_gate_reads() -> None:
    frame = candidate_frame()
    calibrated = _fitted(frame)

    matrix = calibrated.predict_proba(experiments.design(frame))

    assert matrix.shape == (ROWS, 2)
    assert np.isfinite(matrix).all()
    assert ((matrix >= 0.0) & (matrix <= 1.0)).all()
    assert np.allclose(matrix.sum(axis=1), 1.0)


def test_calibration_never_reorders_the_ranking() -> None:
    """A calibration that reorders would change the candidate's PR-AUC behind the ladder's
    back: the rung that won on validation would not be the rung the gate scores."""
    frame = candidate_frame()
    calibrated = _fitted(frame)
    design = experiments.design(frame)

    base = calibrated.base.predict_proba(design)[:, 1]
    after = calibrated.score(frame)
    # A calibration may merge tied scores, never invert a strict one: `strict` holds where the
    # base says row `i` ranks below row `j`, and `inverted` where the calibrated says the
    # opposite.
    strict = base[:, None] < base[None, :]
    inverted = after[:, None] > after[None, :]

    assert not (strict & inverted).any()


def test_a_score_of_exactly_zero_or_one_is_still_calibrated() -> None:
    """A score of 0.0 is a model's rounding, and `log(0)` is not a number a calibrator can
    be fitted on."""
    frame = candidate_frame()
    calibrated = _fitted(frame)
    saturated = frame.copy()
    saturated["precip_sum_6m"] = 0.0
    saturated["precip_sum_1m"] = 0.0

    scores = calibrated.score(saturated)

    assert np.isfinite(scores).all()


def test_a_validation_block_with_one_class_is_refused_rather_than_calibrated_to_a_constant() -> (
    None
):
    frame = candidate_frame()
    pipeline = _pipeline(frame)
    frame["label"] = 0

    with pytest.raises(ValueError, match="one class"):
        CalibratedCandidate.fit(pipeline, frame)


def test_the_high_cut_reaches_precision_and_publishes_its_recall() -> None:
    frame = candidate_frame()
    cuts = read_cuts(_fitted(frame), frame)

    assert cuts.high is not None
    assert cuts.high.precision >= 0.7
    assert 0.0 < cuts.recall_at_high() <= 1.0
    assert "recall" in cuts.report()


def test_a_critical_cut_that_validation_cannot_reach_is_none_and_never_a_score() -> None:
    """`crítico` exists only if a threshold reaches 0.85 (docs/08 §M2 "Severidad", D-T0.4). A
    block with no separable positives cannot, so there is no cut and no `critical`."""
    flat = candidate_frame()
    flat["precip_sum_1m"] = 1.0
    flat["precip_sum_6m"] = 2.0
    pipeline = _pipeline(flat)

    cuts = read_cuts(CalibratedCandidate.fit(pipeline, flat), flat)

    if cuts.critical is None:
        assert CRITICAL not in list(cuts.severity([0.0, 0.5, 1.0]))
        assert "no cut reached" in cuts.report()
    else:
        assert cuts.critical.precision >= 0.85


def test_a_score_at_a_cut_gets_that_severity_and_not_the_one_below() -> None:
    cuts = read_cuts(_fitted(candidate_frame()), candidate_frame())
    scores = [cut.value for cut in (cuts.high, cuts.critical) if cut is not None]
    ranks = {"low": 0, "high": 1, "critical": 2}

    codes = [ranks[code] for code in cuts.severity(sorted(scores))]

    assert codes == sorted(codes)
    if cuts.critical is not None and cuts.high is not None:
        assert codes == [ranks[HIGH], ranks[CRITICAL]]
        assert cuts.high.value < cuts.critical.value
    assert list(cuts.severity([0.0])) == [LOW]


def test_a_score_below_the_first_cut_is_low_and_never_a_severity() -> None:
    cuts = read_cuts(_fitted(candidate_frame()), candidate_frame())
    first = min(cut.value for cut in (cuts.high, cuts.critical) if cut is not None)

    assert list(cuts.severity([0.0, first / 2])) == [LOW, LOW]
