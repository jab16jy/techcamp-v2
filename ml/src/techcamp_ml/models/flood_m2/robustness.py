"""The robustness report of M2: one department out at a time, and the four seasons
(ADR-0020 step 9, docs/08 §M2 "Partición").

The report reads **train and validation only**. `harness.split.department_holdouts` takes
the development blocks and not the whole table on purpose: a hold-out that read the blocked
test months would be a second reader of a block the gate owns, and step 9 runs after step 8,
never instead of it. Nothing here can reach the test: the block is not in the arguments of
any function in this module, and `DevelopmentSplit` has no `test` field.

Three readings of the step are worth naming, because docs/08 asks for them in one line and
each could have gone another way:

* **The department is the subgroup.** docs/08 §M2 "Partición" names the department axis for
  this report ("reporte adicional dejando fuera un departamento a la vez") and the feature
  contract carries no other categorical attribute — `harness.promotion._design` scores
  `FEATURE_NAMES` and nothing else, so the municipality code is not even available to group
  by. Seven departments is the whole of the region's administration (docs/08 §M2 "Región").
* **The season is the calendar one.** docs/08 §M2 asks for "desempeño por temporada" and
  never says which: there is no hydrological season defined for M2 anywhere in `docs/`, and
  inventing a wet/dry split here would be a decision the owner has not made. The four
  calendar seasons of `horizon_start` are read instead, and the report says which one it
  read.
* **The hold-out refits the chosen configuration, it does not re-tune.** Each fold re-fits
  the candidate's own hyperparameters on that fold's train rows. Re-running the whole search
  per fold would tune on validation-period rows seven times over and measure the search as
  much as the department; step 9 asks how the candidate behaves across the region, and the
  candidate is the one the ladder chose.

A slice with no positives, or none at all, reports `None` for its PR-AUC and its Brier and
says why in `note`. Zero is a number here — "ranks the floods last" is a real average
precision — and it is not the truth of a slice with nothing to rank.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from techcamp_ml.harness.promotion import Scored
from techcamp_ml.harness.split import DevelopmentSplit, department_holdouts
from techcamp_ml.models.flood_m2.experiments import brier, design, positive_class, pr_auc

CALENDAR_SEASONS: dict[str, tuple[int, int, int]] = {
    "Q1": (1, 2, 3),
    "Q2": (4, 5, 6),
    "Q3": (7, 8, 9),
    "Q4": (10, 11, 12),
}
"""The four calendar seasons, as the months of `horizon_start` each one holds."""

AXIS_ALL, AXIS_DEPARTMENT, AXIS_SEASON = "all", "department", "season"

SINGLE_CLASS = "the slice holds one class only, so its PR-AUC and its Brier are not defined"


@dataclass(frozen=True, slots=True)
class Slice:
    """One row of the report: how many rows, how many floods, and what the candidate did
    with them."""

    axis: str
    name: str
    rows: int
    positives: int
    pr_auc: float | None
    brier: float | None
    note: str = ""

    def row(self) -> dict[str, object]:
        return {
            "axis": self.axis,
            "slice": self.name,
            "rows": self.rows,
            "positives": self.positives,
            "prevalence": self.positives / self.rows if self.rows else None,
            "pr_auc": self.pr_auc,
            "brier": self.brier,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class RobustnessReport:
    """The whole block, the departments one at a time, and the four seasons."""

    whole: Slice
    departments: tuple[Slice, ...]
    seasons: tuple[Slice, ...]

    @property
    def slices(self) -> tuple[Slice, ...]:
        return (self.whole, *self.departments, *self.seasons)

    def frame(self) -> pd.DataFrame:
        """The report as a frame, one row per slice, in the order it was read."""
        return pd.DataFrame([part.row() for part in self.slices])

    def report(self) -> str:
        """One line per slice, for the model card §9 the numbers can be copied from."""
        return "\n".join(
            f"{part.axis:>10} {part.name:<12} rows {part.rows:>6} "
            f"positives {part.positives:>4} "
            f"PR-AUC {'n/a' if part.pr_auc is None else format(part.pr_auc, '.4f')} "
            f"Brier {'n/a' if part.brier is None else format(part.brier, '.5f')} {part.note}"
            for part in self.slices
        )


def robustness(
    fit: Callable[[pd.DataFrame], Scored],
    development: DevelopmentSplit,
) -> RobustnessReport:
    """The whole of ADR-0020 step 9 for one candidate.

    `fit` re-fits the chosen configuration on the rows it is given and answers the
    `harness.promotion.Scored` protocol, which is what lets the same candidate be measured
    seven times without the report owning a model.
    """
    scored = fit(development.train)
    whole = _slice(AXIS_ALL, "train -> validation", scored, development.validation)
    departments = tuple(
        _slice(
            AXIS_DEPARTMENT,
            held_out.department_name,
            fit(held_out.train),
            held_out.evaluation,
        )
        for held_out in department_holdouts(development)
    )
    seasons = tuple(
        _slice(AXIS_SEASON, name, scored, _season(development.validation, name))
        for name in CALENDAR_SEASONS
    )
    return RobustnessReport(whole=whole, departments=departments, seasons=seasons)


def _slice(axis: str, name: str, scorer: Scored, rows: pd.DataFrame) -> Slice:
    """One slice measured, with the two ways of having nothing to measure."""
    labels = rows["label"].to_numpy(dtype=np.int64)
    positives = int(labels.sum())
    single_class = positives == 0 or positives == labels.size
    if single_class:
        return Slice(axis, name, len(labels), positives, None, None, SINGLE_CLASS)
    scores = positive_class(scorer.predict_proba(design(rows)))
    return Slice(
        axis=axis,
        name=name,
        rows=len(labels),
        positives=positives,
        pr_auc=pr_auc(labels, scores),
        brier=brier(labels, scores),
    )


def _season(validation: pd.DataFrame, name: str) -> pd.DataFrame:
    """The rows of one calendar season of `horizon_start`."""
    months = pd.PeriodIndex(validation["horizon_start"], freq="M").month
    return validation[months.isin(CALENDAR_SEASONS[name])]
