"""The temporal split of M2, its gap and the department hold-out (docs/08 §M2
"Partición", ADR-0020 step 3).

Three blocks over the label window docs/08 §Fuentes de datos de M2 closes (2019-01 →
2025-12, D-T3.2), with **six whole months dropped between each pair**. Six is the longest
feature window (`PRECIPITATION_WINDOWS_MONTHS`, techcamp.risk.domain.features), so the
window of the first month of a block reads months that belong to no block: no validation
row can see a month train was labelled on, and no test row can see one validation was tuned
on. The rows in the gaps are discarded, not reassigned — a gap month kept on either side is
a month whose label the other side could have learned from.

The boundaries are written down here rather than measured, and they belong to the harness:
docs/08 fixes the gap, not the months, and an agent may not move them (ADR-0020: "el agente
no puede modificar el harness, el dataset de test ni la compuerta"). 2022 is the flood year
of the window (663 of the 1 508 reports of the data card), so it stays in train, where the
climatology is built; the test is the most recent complete labelled year.

The test block is **not** reachable from here. `split` returns train and validation and
nothing else, and this module offers no function that takes the test boundaries and gives
rows back: the only reader of those rows is `harness.promotion.decide_promotion`, the gate
of ADR-0020 step 8.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pandas as pd

GAP_MONTHS = 6
"""docs/08 §M2 "Partición": "brecha ≥ 6 meses (la ventana más larga de las features)"."""

TRAIN_FIRST = "2019-01"
TRAIN_LAST = "2022-06"
VAL_FIRST = "2023-01"
VAL_LAST = "2024-06"
TEST_FIRST = "2025-01"
TEST_LAST = "2025-12"
"""The boundaries, as the first day of the first and last month of each block. `TEST_FIRST`
and `TEST_LAST` are the gate's, not this module's: nothing here reads the rows they name."""

TRAIN_YEARS: tuple[int, ...] = tuple(range(int(TRAIN_FIRST[:4]), int(TRAIN_LAST[:4]) + 1))
"""The years the anomalies are calibrated against (docs/08 §M2 "Features": "anomalías contra
la climatología de train"). D-T6b.1 leaves the served job without a climatology, so
`precip_anomaly_*` is null there and the years have to live where the experiment step can
read them; this is that place. A year the gate decides on never appears here."""


@dataclass(frozen=True, slots=True)
class DevelopmentSplit:
    """What an experiment may see: the rows it trains on and the rows it is judged on.

    There is no `test` field, on purpose (see the module docstring).
    """

    train: pd.DataFrame
    validation: pd.DataFrame


@dataclass(frozen=True, slots=True)
class HeldOut:
    """One department left out of training, for the robustness report of ADR-0020 step 9."""

    department_code: str
    department_name: str
    train: pd.DataFrame
    evaluation: pd.DataFrame


def train_climatology_years() -> tuple[int, ...]:
    """The years to average rainfall over, for the experiment step that fills
    `precip_anomaly_*` (D-T6b.1). A copy of `TRAIN_YEARS`, named for what it is for."""
    return TRAIN_YEARS


def split(table: pd.DataFrame) -> DevelopmentSplit:
    """The two blocks an experiment may use, after checking the gaps they leave.

    The validation boundary is `VAL_FIRST` and nothing else. It used to be a keyword an
    experiment could pass, and that was a way to keep the six-month gap while quietly
    discarding a year of validation: `val_first="2024-01"` leaves the gap intact and drops
    every 2023 row, so the split passes every check it has while judging less. The
    boundaries belong to the harness (ADR-0020: "el agente no puede modificar el harness, el
    dataset de test ni la compuerta"), so `split` takes the table and nothing else; a test
    that needs to prove the gap is enforced asks `_split_at` for a different boundary.
    """
    return _split_at(table, VAL_FIRST)


def _split_at(table: pd.DataFrame, val_first: str) -> DevelopmentSplit:
    """The split at a boundary this call chose.

    `val_first` is a parameter here and nowhere else: a boundary that only holds while
    nobody moves a constant was never checked, and the gap has to be provable without
    opening that back up to callers.
    """
    _assert_gap(TRAIN_LAST, val_first)
    _assert_gap(VAL_LAST, TEST_FIRST)
    months = _months(table)
    return DevelopmentSplit(
        train=table[months <= _period(TRAIN_LAST)].reset_index(drop=True),
        validation=table[
            (months >= _period(val_first)) & (months <= _period(VAL_LAST))
        ].reset_index(drop=True),
    )


def department_holdouts(development: DevelopmentSplit) -> Iterator[HeldOut]:
    """One `HeldOut` per department of `development`, in `department_code` order.

    docs/08 §M2 "Partición" asks for "reporte adicional dejando fuera un departamento a la
    vez". Only the department moves: every month stays where it is, so this is a department
    axis on top of the temporal split and not a second split of its own. It takes the
    development blocks and not the whole table, because a hold-out that read the blocked
    test months would be a second reader of a block the gate owns (ADR-0020 step 9 runs
    after step 8, never instead of it).
    """
    development_rows = pd.concat([development.train, development.validation], ignore_index=True)
    for department_code, department_name in _departments(development_rows):
        outside = development_rows["department_code"] != department_code
        yield HeldOut(
            department_code=department_code,
            department_name=department_name,
            train=development_rows[outside].reset_index(drop=True),
            evaluation=development_rows[~outside].reset_index(drop=True),
        )


def _assert_gap(earlier: str, later: str) -> None:
    """Refuse two boundaries that leave fewer than `GAP_MONTHS` whole months between them."""
    between = (_period(later) - _period(earlier)).n - 1
    if between < GAP_MONTHS:
        raise ValueError(
            f"the gap between {earlier} and {later} holds {between} whole months; docs/08 "
            f'§M2 "Partición" asks for at least {GAP_MONTHS}, the longest feature window'
        )


def _departments(table: pd.DataFrame) -> list[tuple[str, str]]:
    seen = table[["department_code", "department_name"]].drop_duplicates()
    pairs = zip(seen["department_code"], seen["department_name"], strict=True)
    return sorted((str(code), str(name)) for code, name in pairs)


def _months(table: pd.DataFrame) -> pd.PeriodIndex:
    return pd.PeriodIndex(table["horizon_start"], freq="M")


def _period(month: str) -> pd.Period:
    return pd.Period(month, freq="M")
