"""The quasi-random search over the ladder's noise hyperparameters (ADR-0020 step 6).

The tables are small and the signal is loud on purpose: the floods sit in the wettest
accumulations, so a model that reads the rainfall at all beats one that does not. What these
tests pin is the **shape** of the search — what is fitted on what, what the budget does, what
is reproducible — never a threshold, because a threshold read off a fixture's frequency is a
number the real table never had.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from techcamp.risk.domain.features import FEATURE_NAMES

from techcamp_ml.harness.metrics import BOOTSTRAP_RESAMPLES, BOOTSTRAP_SEED
from techcamp_ml.models.flood_m2 import experiments
from techcamp_ml.models.flood_m2.experiments import (
    BASELINE,
    CANDIDATE,
    LOG_COLUMNS,
    LOG_PATH,
    MODEL,
    TUNING_SEED,
    Search,
    append_register,
    entry_for,
    lightgbm_search,
    logistic_search,
    pr_auc_ci95,
    run_ladder,
    select,
)

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


def test_the_ladder_holds_the_four_rungs_of_the_escalera() -> None:
    ladder = run_ladder(train_frame(), validation_frame(), resamples=32)

    assert sorted(rung.name for rung in ladder) == [
        "climatology_month",
        "lightgbm",
        "logistic_regression",
        "rainfall_6m",
    ]
    assert sorted(rung.kind for rung in ladder) == [BASELINE, BASELINE, MODEL, MODEL]


def test_the_ladder_comes_back_best_first() -> None:
    ladder = run_ladder(train_frame(), validation_frame(), resamples=32)

    assert [(-rung.pr_auc, rung.brier) for rung in ladder] == sorted(
        (-rung.pr_auc, rung.brier) for rung in ladder
    )


def test_a_model_that_reads_the_rain_beats_a_baseline_that_does_not() -> None:
    ladder = run_ladder(train_frame(), validation_frame(), resamples=32)

    assert select(ladder, MODEL).pr_auc > select(ladder, BASELINE).pr_auc


def test_the_gate_gets_one_baseline_and_one_model_and_never_the_same_rung() -> None:
    ladder = run_ladder(train_frame(), validation_frame(), resamples=32)
    candidate, baseline = select(ladder, MODEL), select(ladder, BASELINE)

    assert candidate.name != baseline.name
    assert (candidate.kind, baseline.kind) == (MODEL, BASELINE)


def test_a_ladder_with_no_rung_of_a_kind_is_refused_not_scored_zero() -> None:
    ladder = run_ladder(train_frame(), validation_frame(), resamples=32)
    models = [rung for rung in ladder if rung.kind == MODEL]

    with pytest.raises(ValueError, match="no ensemble"):
        select(models, "ensemble")


def test_equal_pr_auc_is_broken_by_the_brier_and_not_by_the_order_of_the_ladder() -> None:
    ladder = run_ladder(train_frame(), validation_frame(), resamples=32)
    well = Search("well", MODEL, ladder[0].scorer, 0.5, ladder[0].interval, 0.1)
    poorly = Search("poorly", MODEL, ladder[0].scorer, 0.5, ladder[0].interval, 0.4)

    assert select([poorly, well], MODEL).name == "well"
    assert select([well, poorly], MODEL).name == "well"


def test_the_validation_interval_brackets_the_score_it_was_read_from() -> None:
    ladder = run_ladder(train_frame(), validation_frame(), resamples=64)
    rung = ladder[0]

    assert rung.interval.lower <= rung.pr_auc <= rung.interval.upper
    assert rung.interval.point == pytest.approx(rung.pr_auc)


def test_the_register_interval_uses_the_harness_seed_and_resample_count() -> None:
    """The gate's interval and the register's are resamples of one rule: the harness is
    locked and holds no bootstrap of an absolute PR-AUC."""
    assert experiments.BOOTSTRAP_SEED is BOOTSTRAP_SEED
    assert experiments.BOOTSTRAP_RESAMPLES is BOOTSTRAP_RESAMPLES
    labels = np.array([0, 1] * 20, dtype=np.int64)
    scores = np.linspace(0.0, 1.0, labels.size)

    assert pr_auc_ci95(labels, scores, resamples=200) == pr_auc_ci95(labels, scores, resamples=200)


def test_a_block_with_no_positives_has_no_interval() -> None:
    labels = np.zeros(40, dtype=np.int64)
    scores = np.linspace(0.0, 1.0, 40)

    with pytest.raises(ValueError, match="too small"):
        pr_auc_ci95(labels, scores, resamples=8)


def test_every_rung_answers_the_two_column_matrix_the_gate_reads() -> None:
    ladder = run_ladder(train_frame(), validation_frame(), resamples=16)
    gate_frame = experiments.design(validation_frame())

    for rung in ladder:
        matrix = np.asarray(rung.scorer.predict_proba(gate_frame))
        assert matrix.shape == (80, 2)
        assert np.isfinite(matrix).all()
        assert ((matrix >= 0.0) & (matrix <= 1.0)).all()
        assert np.allclose(matrix.sum(axis=1), 1.0)
        assert np.array_equal(matrix[:, 1], rung.score(validation_frame()))


def _rung(name: str, kind: str, pr_auc: float, brier: float) -> Search:
    ladder = run_ladder(train_frame(), validation_frame(), resamples=16)
    template = ladder[0]
    return Search(name, kind, template.scorer, pr_auc, template.interval, brier)


def test_a_register_row_carries_the_hypothesis_the_score_and_what_the_run_did() -> None:
    rung = _rung("rainfall_6m", BASELINE, 0.21, 0.04)
    entry = entry_for(
        rung,
        date="2026-10-05",
        hypothesis="accumulated rainfall alone orders the risk",
        change="five bins of precip_sum_6m fitted on train",
        decision=BASELINE,
    )

    assert entry.row() == {
        "id": "0",
        "date": "2026-10-05",
        "hypothesis": "accumulated rainfall alone orders the risk",
        "change": "five bins of precip_sum_6m fitted on train",
        "model": "rainfall_6m",
        "val_pr_auc": "0.210000",
        "val_pr_auc_ci_low": f"{rung.interval.lower:.6f}",
        "val_pr_auc_ci_high": f"{rung.interval.upper:.6f}",
        "val_brier": "0.040000",
        "decision": BASELINE,
        "note": rung.note,
    }


def test_a_row_that_says_nothing_about_its_decision_is_refused() -> None:
    rung = _rung("lightgbm", MODEL, 0.3, 0.04)
    entry = entry_for(rung, date="2026-10-05", hypothesis="h", change="c", decision="probably")

    with pytest.raises(ValueError, match="not one of"):
        entry.row()


def test_the_register_is_created_with_the_fixed_header_and_ids_that_never_repeat(
    tmp_path: Path,
) -> None:
    register = tmp_path / "experiments" / "log.csv"
    first = _rung("rainfall_6m", BASELINE, 0.2, 0.04)
    second = _rung("lightgbm", MODEL, 0.3, 0.03)

    one = append_register(
        register,
        entry_for(first, date="2026-10-05", hypothesis="h1", change="c", decision=BASELINE),
    )
    two = append_register(
        register,
        entry_for(second, date="2026-10-05", hypothesis="h2", change="c", decision=CANDIDATE),
    )

    assert (one.id, two.id) == (1, 2)
    assert register.read_text().splitlines()[0] == ",".join(LOG_COLUMNS)
    assert len(register.read_text().splitlines()) == 3


def test_a_register_whose_header_is_not_the_fixed_one_is_refused(tmp_path: Path) -> None:
    register = tmp_path / "log.csv"
    register.write_text("id,date,model,test_pr_auc\n", encoding="utf-8")
    rung = _rung("rainfall_6m", BASELINE, 0.2, 0.04)

    with pytest.raises(ValueError, match="the register is read with"):
        append_register(
            register,
            entry_for(rung, date="2026-10-05", hypothesis="h", change="c", decision=BASELINE),
        )


def test_a_register_row_holds_no_test_number(tmp_path: Path) -> None:
    """There is no column for it, and there will not be one: the header is fixed and a test
    column is where leakage accumulates."""
    register = tmp_path / "log.csv"
    rung = _rung("lightgbm", MODEL, 0.3, 0.03)
    append_register(
        register, entry_for(rung, date="2026-10-05", hypothesis="h", change="c", decision=CANDIDATE)
    )

    header, row = register.read_text().splitlines()
    assert header == ",".join(LOG_COLUMNS)
    assert len(row.split(",")) == len(LOG_COLUMNS)
    assert not any("test" in column for column in LOG_COLUMNS)


def test_the_register_of_the_repository_is_the_one_this_module_writes() -> None:
    assert LOG_PATH.name == "log.csv"
    assert LOG_PATH.parent.name == "experiments"
    assert LOG_PATH.read_text(encoding="utf-8").splitlines()[0] == ",".join(LOG_COLUMNS)
