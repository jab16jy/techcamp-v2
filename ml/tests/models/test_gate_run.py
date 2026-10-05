"""The single read of the blocked test block (ADR-0020 step 8).

The fixture is a table over the real label window with four municipalities, so the harness's
own checks — the complete 2025 year, no thin month, no repeated municipality-month — are
the real ones. `run_gate` is called **three times over three different tables** and never
twice over the same one: `harness.promotion._SPENT_READS` is process-level and refuses the
second read of the same rows, which is what makes the order of these tests irrelevant under
`pytest-randomly`.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from techcamp.risk.domain.features import FEATURE_NAMES, seasonality

from techcamp_ml.harness.metrics import Interval
from techcamp_ml.harness.promotion import (
    CI_LOWER_BOUND_ABOVE_ZERO,
    GateReport,
    PromotionDecision,
    ScoreReport,
    SpentTestBlocks,
)
from techcamp_ml.harness.split import split
from techcamp_ml.models.flood_m2 import gate_run
from techcamp_ml.models.flood_m2.experiments import BASELINE, CANDIDATE, DISCARDED
from techcamp_ml.sources.layout import Layout

MUNICIPALITIES = (
    ("08001", "08", "ATLANTICO"),
    ("08002", "08", "ATLANTICO"),
    ("13001", "13", "BOLIVAR"),
    ("13002", "13", "BOLIVAR"),
)
MONTHS = [
    (f"{year}-{month:02d}", year, month) for year in range(2019, 2026) for month in range(1, 13)
]
FLOODED = tuple(index for index in range(0, len(MONTHS), 3))
"""Every third month of the window floods, in all four municipalities at once: 14 positives
in train and 6 in validation, which is what a bootstrap needs to resample a block that holds
both classes."""
ROWS = 336
TEST_ROWS = 48
TEST_POSITIVES = 16
"""Four flooded months over four municipalities: 33 % of the block, four times the real
frequency and six times the harness fixture's. Nothing here reads a threshold off it — what
it has to be is a block the harness's own checks accept."""


def _refusal() -> PromotionDecision:
    """A gate answer that refused, built by hand so a test can record a receipt without
    spending a second read of the same rows."""
    score = ScoreReport(pr_auc=0.08, brier=0.05)
    return PromotionDecision(
        promote=False,
        reasons=(CI_LOWER_BOUND_ABOVE_ZERO,),
        report=GateReport(
            test_rows=TEST_ROWS,
            test_positives=TEST_POSITIVES,
            candidate=score,
            baseline=score,
            improvement=Interval(point=-0.01, lower=-0.05, upper=0.02),
        ),
    )


def flood_table(*, offset: int = 0, noise: float = 0.0) -> pd.DataFrame:
    """The municipality × month table of docs/08 §M2, one flooded month every third.

    `offset` and `noise` make two tables differ in content, so a second `run_gate` over one
    of them is a second read of *different* rows and the harness guard does not answer for
    it. The floods ride on a wetter month, which is the signal the ladder needs to rank
    anything at all.
    """
    generator = np.random.default_rng(offset)
    rows = []
    for index, (code, department_code, department_name) in enumerate(MUNICIPALITIES):
        for position, (month, year, number) in enumerate(MONTHS):
            flooded = (position + offset + index) % len(MONTHS) in FLOODED
            rain = 400.0 + 300.0 * flooded + noise * float(generator.normal())
            row: dict[str, object] = {column: float(generator.normal()) for column in FEATURE_NAMES}
            row.update(
                {
                    "code": code,
                    "department_code": department_code,
                    "department_name": department_name,
                    "year": year,
                    "month": number,
                    "horizon_start": pd.Timestamp(month + "-01"),
                    "precip_sum_1m": rain,
                    "precip_sum_2m": rain * 1.5,
                    "precip_sum_3m": rain * 2.0,
                    "precip_sum_4m": rain * 2.5,
                    "precip_sum_5m": rain * 3.0,
                    "precip_sum_6m": rain * 3.5,
                    "precip_anomaly_1m": rain - 300.0,
                    "precip_anomaly_3m": rain * 2.0 - 600.0,
                    "precip_anomaly_6m": rain * 3.5 - 900.0,
                    "soil_moisture_mean_1m": 0.2 + rain / 5000.0,
                    "elevation_m": 30.0 + index,
                    "slope_deg": 1.5 + index,
                    "month_sin": seasonality(number)[0],
                    "month_cos": seasonality(number)[1],
                    "label": int(flooded),
                }
            )
            rows.append(row)
    return pd.DataFrame(rows)


def _run(table: pd.DataFrame, register: Path | None = None) -> gate_run.GateRun:
    return gate_run.run_gate(
        table,
        split(table),
        reads=SpentTestBlocks(),
        register=register,
        trials=2,
        resamples=64,
        today="2026-10-05",
    )


def test_the_gate_is_called_once_and_answers_with_a_typed_decision() -> None:
    run = _run(flood_table())

    assert run.decision.report.test_rows == TEST_ROWS
    assert run.decision.report.test_positives == TEST_POSITIVES
    assert set(run.decision.reasons) <= {
        "improvement_ic95_lower_bound_not_above_zero",
        "brier_worse_than_the_best_baseline",
    }
    assert run.decision.promote is (not run.decision.reasons)


def test_the_candidate_and_the_baseline_the_gate_were_given_are_different_rungs() -> None:
    run = _run(flood_table(noise=0.5))

    assert run.candidate.kind == "model"
    assert run.baseline.kind == BASELINE
    assert run.candidate.name != run.baseline.name


def test_what_production_serves_is_the_baseline_unless_the_gate_promoted() -> None:
    """docs/08 §M2 "Línea base servida", D-T0.5: the best validation baseline is served when
    nothing passes. That is an answer, not a failure of the run."""
    run = _run(flood_table(noise=1.0))

    assert run.served == (run.candidate.name if run.promote else run.baseline.name)
    if not run.promote:
        assert run.served in {rung.name for rung in run.ladder if rung.kind == BASELINE}


def test_a_second_read_of_the_same_block_is_refused_by_the_harness() -> None:
    table = flood_table(noise=2.0)
    _run(table)

    with pytest.raises(ValueError, match="already been read"):
        _run(table)


def test_the_register_is_written_with_a_row_per_rung_and_no_test_column(
    tmp_path: Path,
) -> None:
    register = tmp_path / "experiments" / "log.csv"
    run = _run(flood_table(noise=3.0, offset=1), register=register)

    assert register.is_file()
    assert len(run.register) == 4
    assert {entry.decision for entry in run.register} == {CANDIDATE, BASELINE, DISCARDED}
    assert all(entry.hypothesis and entry.change for entry in run.register)


def test_a_run_without_a_register_path_writes_nothing() -> None:
    run = _run(flood_table(noise=4.0, offset=2))

    assert len(run.register) == 4
    assert [entry.id for entry in run.register] == [0, 0, 0, 0]


def test_the_report_names_the_ladder_the_cuts_the_gate_and_the_served_version() -> None:
    run = _run(flood_table(noise=5.0, offset=3))
    report = run.report()

    assert "== ladder (validation only) ==" in report
    assert "== operating cuts (validation) ==" in report
    assert "== gate (blocked test block, one read) ==" in report
    assert f"candidate {run.candidate.name}" in report
    assert f"served: {run.served}" in report
    assert "== robustness (train and validation only) ==" in report
    assert str(run.decision.report.test_positives) in report


def test_the_receipt_refuses_a_second_run_and_only_records_after_a_decision(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    run = _run(flood_table(noise=6.0, offset=4))

    assert not gate_run.already_spent(layout)
    written = gate_run.record_spend(run.decision, run.candidate.name, run.baseline.name, layout)

    assert written == gate_run.receipt_path(layout)
    assert gate_run.already_spent(layout)
    receipt = json.loads(written.read_text(encoding="utf-8"))
    assert receipt["promote"] == run.decision.promote
    assert receipt["test_rows"] == TEST_ROWS
    assert receipt["candidate"] == run.candidate.name


def test_the_receipt_lives_beside_the_derived_table_and_outside_the_hashed_dataset(
    tmp_path: Path,
) -> None:
    from techcamp_ml.models.flood_m2 import anomalies

    layout = Layout(tmp_path)

    assert gate_run.receipt_path(layout).parent == anomalies.derived_path(layout).parent
    assert gate_run.receipt_path(layout) != anomalies.dataset_path(layout)


def _layout(tmp_path: Path, table: pd.DataFrame) -> Layout:
    """A layout whose dataset and derived anomaly table are the fixture, so `main` never
    reads the real cache or the real dataset."""
    from techcamp_ml.models.flood_m2 import anomalies

    layout = Layout(tmp_path)
    layout.data.mkdir(parents=True, exist_ok=True)
    anomalies.dataset_path(layout).parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(anomalies.dataset_path(layout), index=False)
    path = anomalies.derived_path(layout)
    path.parent.mkdir(parents=True, exist_ok=True)
    table[["code", "horizon_start", *anomalies.ANOMALY_COLUMNS]].to_parquet(path, index=False)
    return layout


def test_a_dry_run_ranks_the_ladder_and_never_reaches_the_gate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    layout = _layout(tmp_path, flood_table(noise=7.0, offset=5))

    assert gate_run.main(["--dry-run", "--trials", "2"], layout=layout) == 0
    printed = capsys.readouterr().out

    assert "PR-AUC" in printed
    assert "candidate" in printed
    assert not gate_run.already_spent(layout)


def test_a_second_run_is_refused_before_it_reads_anything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    layout = _layout(tmp_path, flood_table(noise=8.0, offset=6))
    gate_run.record_spend(_refusal(), "candidate", "baseline", layout)

    assert gate_run.main([], layout=layout) == 2
    assert "already been read" in capsys.readouterr().err


def test_the_fixture_is_the_shape_the_harness_gate_accepts() -> None:
    """The gate refuses a thin year, a missing month and a repeated municipality-month, so
    the fixture has to be none of those for any of this file to mean anything."""
    table = flood_table()
    months = pd.PeriodIndex(table["horizon_start"], freq="M")
    test_months = months[(months >= pd.Period("2025-01", freq="M"))]

    assert len(table) == ROWS
    assert len(test_months.unique()) == 12
    assert table.duplicated(subset=["code", "horizon_start"]).sum() == 0
    assert test_months.value_counts().min() == test_months.value_counts().mode().iloc[0]


def test_the_register_ladder_is_the_whole_run_before_the_gate_and_writes_it(
    tmp_path: Path,
) -> None:
    """It reads train and validation only, so the register can be written — or rewritten,
    after a run that crashed past it — without spending the block's single read."""
    register = tmp_path / "log.csv"
    development = split(flood_table(noise=9.0, offset=7))

    ladder, candidate, baseline, rows = gate_run.register_ladder(
        development, register=register, trials=2, resamples=64, today="2026-10-05"
    )

    assert len(ladder) == 4
    assert (candidate.kind, baseline.kind) == ("model", BASELINE)
    assert len(rows) == 4
    assert len(register.read_text(encoding="utf-8").splitlines()) == 5
    assert all(entry.id > 0 for entry in rows)
