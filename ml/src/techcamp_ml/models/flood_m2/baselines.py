"""The M2 baseline ladder's first two rungs (ADR-0020 step 4, docs/08 §M2 "Escalera").

`climatology` -> `heuristic` -> `linear model` -> `LightGBM`, in that order, because a model
that cannot beat "floods follow the rains" must not reach production (docs/08 §Reglas de
gobierno, "Compuerta estadística"). This module holds the first two, and `experiments.py`
holds the two above them.

**Both are pure functions of the shared feature contract, and that is a gate requirement,
not a preference.** `harness.promotion._design` scores the blocked block with exactly
`FEATURE_NAMES` and nothing else, so a baseline keyed on anything outside that contract —
the municipality code, the department — cannot be the one the gate compares against: it
would answer on validation with its fine key and on test with a coarser one, and the
improvement the gate measures would be the difference between two different baselines
instead of the difference between two models. The calendar month *is* inside the contract,
as `month_sin` and `month_cos`, which is why the climatology here is the frequency of the
label by calendar month and nothing finer.

Both answer `predict_proba(features) -> (n, 2)`, the `harness.promotion.Scored` protocol:
a two-column matrix whose columns sum to one, because the Brier rule of the gate reads a
probability and a hard label cannot answer it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt
import pandas as pd
from sklearn.impute import SimpleImputer
from techcamp.risk.domain.features import FEATURE_NAMES, seasonality

CALENDAR_MONTHS: tuple[int, ...] = tuple(range(1, 13))
"""The twelve levels a climatology can be estimated for."""

RAINFALL_COLUMN = "precip_sum_6m"
"""The feature the domain rule reads: the accumulated rainfall of the longest window of
docs/08 §M2 "Features", the one the model card §1 names as "la línea base simple de lluvia
acumulada"."""

RAINFALL_BINS = 5
"""Equal-count bins of train, so every bin has enough rows to estimate a frequency and the
rule stays a table a field technician can read."""

MONTH_SIN, MONTH_COS = "month_sin", "month_cos"


def calendar_month(features: pd.DataFrame) -> npt.NDArray[np.int64]:
    """The calendar month of every row, read off `month_sin`/`month_cos`.

    The nearest of the twelve pairs `seasonality` produces, so the month is recovered with
    the shared function's own numbers instead of a second trigonometry: December sits next
    to January on the circle, and an `arctan` of two rounded features would put a boundary
    in the wrong place.
    """
    _require(features, (MONTH_SIN, MONTH_COS))
    pairs = np.array([seasonality(month) for month in CALENDAR_MONTHS], dtype=np.float64)
    angles = np.asarray(
        np.column_stack([features[MONTH_SIN], features[MONTH_COS]]), dtype=np.float64
    )
    distances = np.square(angles[:, None, :] - pairs[None, :, :]).sum(axis=2)
    return np.asarray(CALENDAR_MONTHS, dtype=np.int64)[distances.argmin(axis=1)]


def probabilities(scores: Sequence[float] | npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """`scores` as the two-column probability matrix the harness protocol asks for."""
    positive = np.asarray(scores, dtype=np.float64)
    return np.column_stack([1.0 - positive, positive])


@dataclass(frozen=True, slots=True)
class ClimatologyBaseline:
    """The frequency of the label per calendar month in train (docs/08 §M2 "Escalera",
    first rung).

    A month train never saw falls back to the prevalence of the whole train block, the one
    number every part of train agrees on. `0.0` would be "never floods" and `1.0` "always
    floods"; both are claims about a month nobody observed.
    """

    prevalence: float
    by_month: Mapping[int, float] = field(default_factory=dict)

    @classmethod
    def fit(cls, train: pd.DataFrame) -> ClimatologyBaseline:
        """Estimate from train only; validation and test keep their real frequency
        (docs/08 §Reglas de gobierno, "Frecuencia real")."""
        labels = train["label"].to_numpy(dtype=np.int64)
        if labels.size == 0:
            raise ValueError("the climatology baseline needs at least one train row")
        months = calendar_month(train)
        counts = np.bincount(months, minlength=13)[1:]
        floods = np.bincount(months, weights=labels, minlength=13)[1:]
        prevalence = float(labels.mean())
        return cls(
            prevalence=prevalence,
            by_month={
                int(month): float(flood) / float(count)
                for month, flood, count in zip(CALENDAR_MONTHS, floods, counts, strict=True)
                if count > 0
            },
        )

    def score(self, features: pd.DataFrame) -> npt.NDArray[np.float64]:
        """One probability per row, read from the calendar month alone."""
        return np.array(
            [self.by_month.get(int(month), self.prevalence) for month in calendar_month(features)],
            dtype=np.float64,
        )

    def predict_proba(self, features: pd.DataFrame) -> npt.NDArray[np.float64]:
        return probabilities(self.score(features))


@dataclass(frozen=True, slots=True)
class RainfallBaseline:
    """The domain rule: the flood frequency of train's accumulated-rainfall bins, forced to
    never fall as the rain rises (docs/08 §M2 "Escalera", second rung).

    The monotonicity is the domain assumption — "more rain, never less risk" — imposed on
    the empirical bins rather than assumed of them: train's bins are noisy, and a rule that
    answered 0.40 at 300 mm and 0.39 at 310 mm would be a table nobody could act on.
    `top_factors` of this baseline is its own bin (docs/08 §M2 "Factores").
    """

    edges: npt.NDArray[np.float64]
    frequencies: npt.NDArray[np.float64]
    imputer: SimpleImputer = field(compare=False, repr=False)

    @classmethod
    def fit(cls, train: pd.DataFrame) -> RainfallBaseline:
        """Estimate the bin edges and their frequencies from train only."""
        _require(train, (RAINFALL_COLUMN, "label"))
        rainfall = train[RAINFALL_COLUMN].to_numpy(dtype=np.float64)
        if not np.isfinite(rainfall).any():
            raise ValueError(
                f"every {RAINFALL_COLUMN} of train is missing, so there is no bin to "
                "estimate; a rule with no train evidence is not a baseline, it is a guess"
            )
        edges = np.unique(
            np.quantile(rainfall[~np.isnan(rainfall)], np.linspace(0, 1, RAINFALL_BINS + 1)[1:-1])
        )
        imputer = SimpleImputer(strategy="median").fit(train[[RAINFALL_COLUMN]])
        bins = np.digitize(
            np.asarray(imputer.transform(train[[RAINFALL_COLUMN]])).reshape(-1), edges
        )
        # `edges.size + 1` reachable bins and not RAINFALL_BINS + 1: quantile edges collapse
        # when train holds fewer distinct accumulations than bins, and a phantom empty bin at
        # the end would make the refusal below fire on every run.
        reachable = int(edges.size) + 1
        counts = np.bincount(bins, minlength=reachable)
        floods = np.bincount(
            bins, weights=train["label"].to_numpy(dtype=np.float64), minlength=reachable
        )
        empty = [int(index) for index, count in enumerate(counts) if count == 0]
        if empty:
            raise ValueError(
                f"the {reachable} rainfall bins of train left {len(empty)} of them with no "
                f"row, the first being bin {empty[0]}; an empty bin has no frequency and the "
                "rule would answer 0.0 — 'this rain never floods' — for rain nobody has seen"
            )
        return cls(
            edges=edges,
            frequencies=np.maximum.accumulate(floods / counts),
            imputer=imputer,
        )

    def score(self, features: pd.DataFrame) -> npt.NDArray[np.float64]:
        """One probability per row, read from the rainfall bin alone."""
        _require(features, (RAINFALL_COLUMN,))
        rainfall = np.asarray(self.imputer.transform(features[[RAINFALL_COLUMN]])).reshape(-1)
        return self.frequencies[np.digitize(rainfall, self.edges)]

    def predict_proba(self, features: pd.DataFrame) -> npt.NDArray[np.float64]:
        return probabilities(self.score(features))


def _require(features: pd.DataFrame, columns: tuple[str, ...]) -> None:
    missing = [column for column in columns if column not in features.columns]
    if missing:
        raise ValueError(
            f"the feature frame is missing {missing}; a baseline reads the shared contract "
            f"of techcamp.risk.domain.features.FEATURE_NAMES ({len(FEATURE_NAMES)} columns), "
            "the same one harness.promotion._design hands to the gate"
        )
