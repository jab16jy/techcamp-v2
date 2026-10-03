"""Tests for the harness split: the temporal gap, the blocked test and the hold-out."""

from __future__ import annotations

import pandas as pd
import pytest
from conftest import MONTHS, flood_table

from techcamp_ml.harness.split import (
    GAP_MONTHS,
    TEST_FIRST,
    TRAIN_LAST,
    TRAIN_YEARS,
    VAL_FIRST,
    VAL_LAST,
    department_holdouts,
    split,
    train_climatology_years,
)


def months_of(frame: pd.DataFrame) -> set[pd.Period]:
    """The months the rows of `frame` cover."""
    return set(pd.PeriodIndex(frame["horizon_start"], freq="M"))


EVERY_MONTH = set(pd.PeriodIndex([month for month, _, _ in MONTHS], freq="M"))
GAPS = set(pd.PeriodIndex([f"2022-{month:02d}" for month in range(7, 13)], freq="M")) | set(
    pd.PeriodIndex([f"2024-{month:02d}" for month in range(7, 13)], freq="M")
)
"""The twelve months the two gaps drop, spelled out here on purpose: a test that recomputes
them from the same constants it is testing proves nothing."""


def test_train_and_validation_cover_every_month_outside_the_two_gaps() -> None:
    development = split(flood_table())
    covered = months_of(development.train) | months_of(development.validation)
    blocked = {month for month in EVERY_MONTH if month >= pd.Period(TEST_FIRST, freq="M")}

    assert covered | GAPS == EVERY_MONTH - blocked
    # A gap month that reached train would put a train label inside the six-month window of
    # a validation month (docs/08 §M2 "Partición").
    assert not GAPS & covered
    assert len(GAPS) == GAP_MONTHS * 2


def test_the_blocks_end_where_the_harness_constants_say() -> None:
    development = split(flood_table())

    assert development.train["horizon_start"].max() == pd.Timestamp(TRAIN_LAST)
    assert development.validation["horizon_start"].min() == pd.Timestamp(VAL_FIRST)
    assert development.validation["horizon_start"].max() == pd.Timestamp(VAL_LAST)


def test_no_month_of_the_blocked_test_reaches_a_development_block() -> None:
    """Negative: a test month in validation is a month the experiment was already tuned on,
    so the gate of step 8 would decide on something it has seen (docs/08 §Reglas de
    gobierno, "Test intocable")."""
    development = split(flood_table())
    blocked = {month for month in EVERY_MONTH if month >= pd.Period(TEST_FIRST, freq="M")}

    assert not blocked & (months_of(development.train) | months_of(development.validation))


def test_a_three_month_gap_is_refused() -> None:
    """Negative: docs/08 §M2 "Partición" wants six months, the longest feature window."""
    with pytest.raises(ValueError, match="gap"):
        split(flood_table(), val_first="2022-10")


def test_validation_starting_right_after_train_is_refused() -> None:
    """Negative: with no gap at all, 2022-07's window reads a month train was labelled on."""
    with pytest.raises(ValueError, match="gap"):
        split(flood_table(), val_first="2022-07")


def test_a_validation_block_that_starts_before_train_ends_is_refused() -> None:
    """Negative: overlapping blocks are not a temporal split at all."""
    with pytest.raises(ValueError, match="gap"):
        split(flood_table(), val_first="2022-01")


def test_the_development_split_never_names_the_blocked_test() -> None:
    """Negative: the test is blocked, so no attribute of it can be read here."""
    development = split(flood_table())

    assert not hasattr(development, "test")
    assert set(development.__slots__) == {"train", "validation"}


def test_the_climatology_years_are_the_train_years_and_nothing_later() -> None:
    """D-T6b.1: the anomalies compare against the train climatology, and the experiment step
    builds it from these years (docs/08 §M2 "Features")."""
    assert train_climatology_years() == TRAIN_YEARS == (2019, 2020, 2021, 2022)
    # Negative: a year the gate decides on must never calibrate the anomalies.
    assert not {2023, 2024, 2025} & set(TRAIN_YEARS)


def test_every_department_gets_its_own_hold_out() -> None:
    development = split(flood_table())
    hold_outs = list(department_holdouts(development))

    assert [held_out.department_code for held_out in hold_outs] == ["08", "13"]
    assert [held_out.department_name for held_out in hold_outs] == ["Atlántico", "Bolívar"]
    for held_out in hold_outs:
        assert held_out.department_code not in set(held_out.train["department_code"])
        assert len(held_out.train) + len(held_out.evaluation) == len(development.train) + len(
            development.validation
        )


def test_a_hold_out_never_reads_the_blocked_test() -> None:
    """Negative: step 9 reports on what the gate already used, so a hold-out reaching the
    test months would be a second reader of a block the gate owns."""
    development = split(flood_table())
    blocked = {month for month in EVERY_MONTH if month >= pd.Period(TEST_FIRST, freq="M")}

    for held_out in department_holdouts(development):
        held = months_of(held_out.train) | months_of(held_out.evaluation)
        assert not held & blocked


def test_a_hold_out_keeps_every_month_the_development_split_kept() -> None:
    """Negative: the robustness report is a department axis on top of the temporal split, not
    a second split, so it may not drop a month of its own (docs/08 §M2 "Partición")."""
    development = split(flood_table())

    for held_out in department_holdouts(development):
        held = months_of(held_out.train) | months_of(held_out.evaluation)
        assert held == months_of(development.train) | months_of(development.validation)
