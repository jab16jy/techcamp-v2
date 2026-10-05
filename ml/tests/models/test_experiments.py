"""The quasi-random search over the ladder's noise hyperparameters (ADR-0020 step 6).

The tables are small and the signal is loud on purpose: the floods sit in the wettest
accumulations, so a model that reads the rainfall at all beats one that does not. What these
tests pin is the **shape** of the search — what is fitted on what, what the budget does, what
is reproducible — never a threshold, because a threshold read off a fixture's frequency is a
number the real table never had.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from techcamp.risk.domain.features import FEATURE_NAMES

from techcamp_ml.models.flood_m2 import experiments
from techcamp_ml.models.flood_m2.experiments import TUNING_SEED, lightgbm_search, logistic_search

TRAIN_ROWS = 240
FIXTURE_SEED = 11


def _frame(rows: int, *, floods: list[int], seed: int) -> pd.DataFrame:
    """`rows` municipality-months whose floods are the listed indices.

    The rainfall rises with the row and the flooded rows are the wettest ones, which is the
    signal the model has to find; the rest of the columns are noise around it.
    """
    generator = np.random.default_rng(seed)
    records = []
    for index in range(rows):
        wet = index in floods
        rainfall = 800.0 + index if wet else 100.0 + (index % 37) * 5.0
        row: dict[str, object] = {name: float(generator.normal()) for name in FEATURE_NAMES}
        row["precip_sum_1m"] = rainfall
        row["precip_sum_6m"] = rainfall * 3.0
        row["precip_anomaly_1m"] = rainfall - 200.0
        row["precip_anomaly_3m"] = rainfall * 2.0 - 400.0
        row["precip_anomaly_6m"] = rainfall * 3.0 - 600.0
        row["soil_moisture_mean_1m"] = 0.2 + (rainfall / 1000.0)
        row["label"] = int(wet)
        records.append(row)
    return pd.DataFrame(records)


def train_frame() -> pd.DataFrame:
    """Every fifth row of the first two thirds floods."""

    return _frame(TRAIN_ROWS, floods=list(range(0, 160, 5)), seed=FIXTURE_SEED)


def validation_frame() -> pd.DataFrame:
    """Every twenty-fourth row floods, 7 of 80: a different rate from train's 32 of 160, so
    a model that merely learned train's prevalence answers worse than one that read the
    rain."""

    return _frame(TRAIN_ROWS // 3, floods=list(range(0, TRAIN_ROWS, 24)), seed=FIXTURE_SEED + 1)


def test_the_design_matrix_is_the_shared_contract_and_not_the_label() -> None:
    """The gate hands `predict_proba` exactly `FEATURE_NAMES`; an estimator fitted on the
    whole training frame could not answer it."""
    frame = train_frame()

    assert list(experiments.design(frame).columns) == list(FEATURE_NAMES)
    assert "label" not in experiments.design(frame).columns
    with pytest.raises(ValueError, match="missing"):
        experiments.design(frame.drop(columns=["slope_deg"]))


def test_the_search_draws_a_fixed_budget_and_records_the_winning_draw() -> None:
    _, trial = logistic_search(train_frame(), validation_frame(), trials=5, seed=TUNING_SEED)

    assert 1 <= trial.number <= 5
    assert set(trial.params) == {"C"}
    assert 1e-3 <= trial.params["C"] <= 1e2
    assert trial.rounds is None


def test_the_search_is_reproducible_from_its_seed_and_only_from_it() -> None:
    first = logistic_search(train_frame(), validation_frame(), trials=4, seed=TUNING_SEED)
    second = logistic_search(train_frame(), validation_frame(), trials=4, seed=TUNING_SEED)
    other = logistic_search(train_frame(), validation_frame(), trials=4, seed=TUNING_SEED + 1)

    assert first[1] == second[1]
    assert other[1].params != first[1].params or other[1].pr_auc != first[1].pr_auc


def test_lightgbm_draws_its_noise_hyperparameters_inside_their_ranges() -> None:
    _, trial = lightgbm_search(train_frame(), validation_frame(), trials=3)

    assert set(trial.params) == set(experiments.LIGHTGBM_RANGES)
    for name, (low, high) in experiments.LIGHTGBM_RANGES.items():
        assert low <= trial.params[name] <= high
    assert isinstance(trial.params["num_leaves"], int)
    assert isinstance(trial.params["min_child_samples"], int)


def test_lightgbm_early_stops_on_validation_and_reports_the_rounds_it_kept() -> None:
    _, trial = lightgbm_search(train_frame(), validation_frame(), trials=2)

    assert trial.rounds is not None
    assert 0 < trial.rounds <= experiments.TREE_BUDGET
    assert "early stopping" in trial.note


def test_the_logistic_imputes_with_a_median_fitted_on_train_alone() -> None:
    """Owner, 2026-10-05: the imputer's median comes from train, and the class weighting is
    fixed rather than drawn."""
    train, validation = train_frame(), validation_frame()
    pipeline, _ = logistic_search(train, validation, trials=2)
    imputer = pipeline.named_steps["impute"]

    assert pipeline.named_steps["model"].class_weight == "balanced"
    assert list(imputer.statistics_) == [
        float(np.nanmedian(train[column].to_numpy(dtype=np.float64))) for column in FEATURE_NAMES
    ]


def test_a_search_with_no_budget_is_refused_rather_than_returning_an_unfitted_model() -> None:
    with pytest.raises(ValueError, match="budget is never zero"):
        logistic_search(train_frame(), validation_frame(), trials=0)
    with pytest.raises(ValueError, match="budget is never zero"):
        lightgbm_search(train_frame(), validation_frame(), trials=0)


def test_both_searches_answer_the_two_column_matrix_the_gate_reads() -> None:
    train, validation = train_frame(), validation_frame()
    gate_frame = experiments.design(validation)
    scorers = [
        logistic_search(train, validation, trials=2)[0],
        lightgbm_search(train, validation, trials=2)[0],
    ]

    for scorer in scorers:
        matrix = np.asarray(scorer.predict_proba(gate_frame))
        assert matrix.shape == (80, 2)
        assert np.isfinite(matrix).all()
        assert ((matrix >= 0.0) & (matrix <= 1.0)).all()
        assert np.allclose(matrix.sum(axis=1), 1.0)
