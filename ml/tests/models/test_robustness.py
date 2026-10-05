"""The robustness report of M2 (ADR-0020 step 9).

Three departments and two seasons of twelve months, small enough that every count in an
assertion can be read off the fixture. What is pinned is the *shape* of the report: which
rows land in which slice, that a department's fold holds out that department and only that
department, and that a slice with one class reports `None` rather than a number.

Nothing here asserts a threshold, and nothing here reads the blocked test block: the
development blocks are the whole world these functions see.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from techcamp.risk.domain.features import FEATURE_NAMES, seasonality

from techcamp_ml.harness.split import DevelopmentSplit, department_holdouts
from techcamp_ml.models.flood_m2.baselines import ClimatologyBaseline
from techcamp_ml.models.flood_m2.experiments import design
from techcamp_ml.models.flood_m2.robustness import (
    AXIS_ALL,
    AXIS_DEPARTMENT,
    CALENDAR_SEASONS,
    SINGLE_CLASS,
    robustness,
)

DEPARTMENTS = (("08", "ATLANTICO"), ("13", "BOLIVAR"), ("23", "CORDOBA"))
MONTHS = 12
SILENT = "23"
"""The department the one-class test silences: docs/08 §M2 "Región" has seven departments
and three is enough to show that a fold holds out its own and keeps the other two."""

FIXTURE_SEED = 5


def _frame(period: str, seed: int, *, silent: bool) -> pd.DataFrame:
    """`period`'s twelve months of every department, with floods drawn from the rainfall.

    `silent` zeroes one department's labels, which is the slice the report has to answer
    `None` for.
    """
    generator = np.random.default_rng(seed)
    start = pd.Period(f"{period}-01", freq="M")
    records = []
    for index, (code, name) in enumerate(DEPARTMENTS):
        for step in range(MONTHS):
            month = start + step
            rain = generator.uniform(0.0, 900.0) + index * 30.0
            wet = generator.random() < 0.02 + 0.5 * (rain / 1000.0)
            row: dict[str, object] = {column: float(generator.normal()) for column in FEATURE_NAMES}
            row.update(
                {
                    "code": f"{code}{index + 1:03d}",
                    "department_code": code,
                    "department_name": name,
                    "year": month.year,
                    "month": month.month,
                    "horizon_start": month.to_timestamp(),
                    "month_sin": seasonality(month.month)[0],
                    "month_cos": seasonality(month.month)[1],
                    "label": int(wet) and not (silent and code == SILENT),
                }
            )
            records.append(row)
    return pd.DataFrame(records)


def development_frame(*, silent: bool = False) -> DevelopmentSplit:
    """A development split of the same shape `harness.split.split` returns: twelve months of
    train and twelve of validation, over the three departments."""

    return DevelopmentSplit(
        train=_frame("2021-01", FIXTURE_SEED, silent=silent),
        validation=_frame("2022-01", FIXTURE_SEED + 1, silent=silent),
    )


def _fit(train: pd.DataFrame) -> ClimatologyBaseline:
    """A deterministic stand-in for the candidate: the climatology of the rows it is given.
    What these tests measure is the slicing, not a model's skill."""

    return ClimatologyBaseline.fit(train)


def test_the_report_holds_the_whole_block_the_departments_and_the_seasons() -> None:
    development = development_frame()

    report = robustness(_fit, development)

    assert report.whole.axis == AXIS_ALL
    assert [part.axis for part in report.departments] == [AXIS_DEPARTMENT] * len(DEPARTMENTS)
    assert [part.name for part in report.seasons] == list(CALENDAR_SEASONS)
    assert report.whole.rows == len(development.validation)


def test_every_department_is_held_out_once_and_the_rows_are_counted() -> None:
    development = development_frame()

    report = robustness(_fit, development)

    assert [part.name for part in report.departments] == [name for _, name in DEPARTMENTS]
    assert sum(part.rows for part in report.departments) == len(development.train) + len(
        development.validation
    )


def test_a_departments_fold_holds_out_that_department_and_only_that_one() -> None:
    development = development_frame()
    codes = [code for code, _ in DEPARTMENTS]

    for fold, code in zip(department_holdouts(development), codes, strict=True):
        assert set(fold.evaluation["department_code"]) == {code}
        assert set(fold.train["department_code"]) == set(codes) - {code}
        assert len(fold.evaluation) == MONTHS * 2


def test_a_season_slice_is_every_row_of_its_calendar_months() -> None:
    development = development_frame()
    months = development.validation["horizon_start"].dt.month

    for part in robustness(_fit, development).seasons:
        of_season = months.isin(CALENDAR_SEASONS[part.name])
        assert part.rows == int(of_season.sum())
        assert part.positives == int(development.validation["label"][of_season].sum())


def test_the_four_seasons_partition_the_validation_block() -> None:
    development = development_frame()
    report = robustness(_fit, development)

    assert sum(part.rows for part in report.seasons) == len(development.validation)
    assert sum(part.positives for part in report.seasons) == int(
        development.validation["label"].sum()
    )


def test_a_department_that_never_floods_reports_none_and_not_zero() -> None:
    """Zero is a real average precision — "ranks the floods last" — and it is not the truth
    of a slice with nothing to rank."""
    report = robustness(_fit, development_frame(silent=True))
    silent = next(part for part in report.departments if part.name == dict(DEPARTMENTS)[SILENT])

    assert silent.positives == 0
    assert silent.pr_auc is None
    assert silent.brier is None
    assert silent.note == SINGLE_CLASS
    assert "n/a" in report.report()


def test_a_silent_department_does_not_silence_the_others() -> None:
    report = robustness(_fit, development_frame(silent=True))
    speaking = [part for part in report.departments if part.name != dict(DEPARTMENTS)[SILENT]]

    assert all(part.pr_auc is not None for part in speaking)
    assert all(part.positives > 0 for part in speaking)


def test_the_frame_carries_one_row_per_slice_with_its_counts() -> None:
    report = robustness(_fit, development_frame())

    frame = report.frame()

    assert len(frame) == len(report.slices)
    assert list(frame.columns) == [
        "axis",
        "slice",
        "rows",
        "positives",
        "prevalence",
        "pr_auc",
        "brier",
        "note",
    ]
    assert frame["prevalence"].between(0.0, 1.0).all()
    assert frame["axis"].iloc[0] == AXIS_ALL


def test_the_report_never_reads_a_block_it_was_not_given() -> None:
    """`DevelopmentSplit` has no `test` field, so a hold-out that wanted the blocked months
    would have to build them itself."""

    assert not hasattr(DevelopmentSplit, "test")
    with pytest.raises(TypeError):
        DevelopmentSplit(train=pd.DataFrame(), validation=pd.DataFrame(), test=pd.DataFrame())


def test_a_slice_is_measured_on_the_design_matrix_the_gate_also_builds() -> None:
    development = development_frame()
    whole = robustness(_fit, development).whole

    matrix = _fit(development.train).predict_proba(design(development.validation))

    assert whole.rows == len(development.validation)
    assert matrix.shape == (whole.rows, 2)
