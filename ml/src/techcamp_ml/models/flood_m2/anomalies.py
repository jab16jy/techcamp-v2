"""The train climatology of M2, the anomaly of every municipality-month, and the derived
table that carries them (ADR-0020).

`flood_m2.parquet` holds `precip_anomaly_1m/3m/6m` as null columns, because T4 built the
table without a climatology: the years the anomalies are measured against are the **split's**
(data card §Contrato de columnas), and the harness is what names them —
`split.train_climatology_years()` is 2019–2022 and a year the gate decides on never appears
in it. This module is the experiment step that finally derives them, from the weather
source and those years only, and owns the parquet they land in.

Three properties are worth the reader's attention:

* **The manifest is not touched.** The anomalies land in their own parquet under
  `ml/data/flood_m2/derived/`, out of git beside the dataset, because the dataset's hash is
  published and a column added to it would be a dataset the data card no longer describes
  (owner, 2026-10-05).
* **No label and no test boundary enters the derivation.** An anomaly is a difference of two
  rainfall accumulations and the train climatology; it reads the weather archive, which
  covers every month equally, and never the `label` column. The rows of the blocked test
  months are derived here so the gate can score them, exactly as the dataset carries them.
* **The shared module does the arithmetic.** `monthly_climatology` and `precip_anomaly` are
  `techcamp.risk.domain.features`, the module the serving job builds its vector from, so an
  anomaly trained on and an anomaly served are the same number (docs/08 §Reglas de gobierno,
  "Paridad de features").
"""

from __future__ import annotations

from collections.abc import Container, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from techcamp.risk.domain.features import (
    ANOMALY_WINDOWS_MONTHS,
    DailySeries,
    monthly_climatology,
    precip_anomaly,
)

from techcamp_ml.datasets.flood_m2 import DATASET_DIRNAME, PARQUET_NAME
from techcamp_ml.harness.split import train_climatology_years
from techcamp_ml.sources.layout import Layout

ANOMALY_WINDOWS = ANOMALY_WINDOWS_MONTHS
"""The anomaly windows of docs/08 §M2 "Features", read from the shared module rather than
written here: a second list would be a second answer to a question parity already answers."""

ANOMALY_COLUMNS: tuple[str, ...] = tuple(f"precip_anomaly_{months}m" for months in ANOMALY_WINDOWS)
"""The column names of the shared feature contract, in its order."""

KEY_COLUMNS = ("code", "horizon_start")
"""What a municipality-month is: the same unit docs/08 §M2 "Unidad" names, read off the
dataset's own columns rather than rebuilt from the year and the month."""

WEATHER_NAME = "weather"
DERIVED_DIRNAME = "derived"
DERIVED_NAME = "anomalies.parquet"

Climatologies = Mapping[str, Mapping[int, float]]
SeriesByCode = Mapping[str, DailySeries]


def climatologies(weather: pd.DataFrame, *, years: Container[int]) -> dict[str, dict[int, float]]:
    """`code -> calendar month -> mean monthly total` over `years` only.

    A day outside `years` cannot move the answer, which is what keeps a validation or test
    anomaly from carrying its own season's weather (docs/08 §M2 "Features"). A municipality
    the archive never answered is absent from the mapping rather than present with zeroes.
    """
    return {
        code: dict(monthly_climatology(days, years=years))
        for code, days in precipitation_by_code(weather).items()
    }


def precipitation_by_code(weather: pd.DataFrame) -> dict[str, DailySeries]:
    """Each municipality's daily rainfall, keyed by day, NaN days kept as they are.

    The shared module reads a NaN as the missing day it is, and turning it into 0 mm would
    claim the weather was dry.

    Two rows for the same municipality and day are refused rather than resolved, the same
    rule `datasets.flood_m2.build_table` applies to the same parquet: a mapping keeps
    whichever came last, so the value a window reads would be row order instead of a rule.
    """
    duplicated = weather[weather.duplicated(subset=["code", "date"], keep=False)]
    if not duplicated.empty:
        repeated = duplicated[["code", "date"]].drop_duplicates().head(3)
        pairs = ", ".join(
            f"{code} {pd.Timestamp(day).date()}" for code, day in repeated.itertuples(index=False)
        )
        raise ValueError(
            f"the weather parquet holds {len(duplicated)} rows for {len(repeated)}+ repeated "
            f"(code, date) pairs, e.g. {pairs}; which one an anomaly would read is row order, "
            "not a rule, so parse the source again and derive the anomalies from that"
        )
    series: dict[str, DailySeries] = {}
    for code, frame in weather.groupby("code", sort=False):
        days = [pd.Timestamp(day).date() for day in frame["date"]]
        series[str(code)] = dict(zip(days, frame["precipitation_sum"].tolist(), strict=True))
    return series


def anomaly_features(
    table: pd.DataFrame,
    series: SeriesByCode,
    climatology: Climatologies,
) -> pd.DataFrame:
    """One derived row per dataset row, in the dataset's own order.

    A municipality with no series or no climatology keeps `None` in all three columns: its
    evidence is missing, and zero would be a claim that the rain matched the average.
    """
    columns: list[Any] = []
    for months in ANOMALY_WINDOWS:
        columns.append(
            pd.Series(
                [
                    precip_anomaly(
                        series.get(str(row.code), {}),
                        issue_month=date(int(row.year), int(row.month), 1),
                        months=months,
                        climatology=climatology.get(str(row.code), {}),
                    )
                    for row in table[["code", "year", "month"]].itertuples(index=False)
                ],
                name=f"precip_anomaly_{months}m",
                dtype="float64",
            )
        )
    keys = table[list(KEY_COLUMNS)].reset_index(drop=True)
    return pd.concat([keys, *columns], axis=1)


def attach_anomalies(table: pd.DataFrame, derived: pd.DataFrame) -> pd.DataFrame:
    """The dataset with the derived anomalies in its own three null columns.

    Full coverage is required and refused otherwise. A missing row cannot be told from a
    missing value once the columns are joined — both are `None` — so a derived table that
    does not cover every municipality-month would quietly leave a block of the model
    without its features, which is the failure a null-filled join would hide.
    """
    _require_columns(derived, ANOMALY_COLUMNS)
    repeated = derived[derived.duplicated(subset=list(KEY_COLUMNS), keep=False)]
    if not repeated.empty:
        offenders = repeated[list(KEY_COLUMNS)].drop_duplicates()
        raise ValueError(
            f"the derived anomaly table holds {len(offenders)} municipality-months more "
            "than once; a repeated key joins one dataset row to two anomaly rows, and which "
            "of them wins is row order"
        )
    uncovered = table[list(KEY_COLUMNS)].merge(
        derived[list(KEY_COLUMNS)], on=list(KEY_COLUMNS), how="left", indicator=True
    )
    missing = uncovered[uncovered["_merge"] == "left_only"]
    if not missing.empty:
        first = missing.iloc[0]
        raise ValueError(
            f"the derived anomaly table covers {len(table) - len(missing)} of the {len(table)} "
            f"municipality-months of the dataset and misses {len(missing)}, e.g. "
            f"{first['code']} {pd.Timestamp(first['horizon_start']).date()}; a row with no "
            "derived anomaly is a row with no evidence, not a row with a zero one"
        )
    joined = table.drop(columns=list(ANOMALY_COLUMNS), errors="ignore").merge(
        derived, on=list(KEY_COLUMNS), how="left", validate="one_to_one", sort=False
    )
    # The dataset's own order first, so a frame that already carried the null anomaly
    # columns keeps them where it had them, and a column the dataset did not carry is
    # appended rather than dropped.
    kept = [column for column in table.columns if column in joined.columns]
    return joined[[*kept, *[column for column in joined.columns if column not in kept]]]


def derived_path(layout: Layout) -> Path:
    """`ml/data/flood_m2/derived/anomalies.parquet`: a build product, out of git, beside the
    dataset whose hash it does not change."""
    return layout.data.parent / DERIVED_DIRNAME / DERIVED_NAME


def dataset_path(layout: Layout) -> Path:
    """`ml/data/flood_m2/dataset/flood_m2.parquet`, the hashed dataset T4 built."""
    return layout.data.parent / DATASET_DIRNAME / PARQUET_NAME


def build_anomalies(layout: Layout, *, years: Container[int] | None = None) -> Path:
    """Derive the anomalies of every dataset row and write them to `derived_path(layout)`."""
    weather = pd.read_parquet(layout.data / f"{WEATHER_NAME}.parquet")
    table = pd.read_parquet(dataset_path(layout))
    wanted = _years(years)
    derived = anomaly_features(
        table,
        precipitation_by_code(weather),
        climatologies(weather, years=wanted),
    )
    path = derived_path(layout)
    path.parent.mkdir(parents=True, exist_ok=True)
    derived.to_parquet(path, index=False)
    return path


def load_features(layout: Layout, *, years: Container[int] | None = None) -> pd.DataFrame:
    """The dataset with its anomalies attached, deriving and writing them when missing.

    The years default to the split's and never to a constant of this module's own: an
    anomaly measured against a climatology another experiment chose would be a different
    feature, and the gate scores the same columns the ladder trained on.
    """
    table = pd.read_parquet(dataset_path(layout))
    path = derived_path(layout)
    if not path.is_file():
        build_anomalies(layout, years=years)
    return attach_anomalies(table, pd.read_parquet(path))


def _years(years: Container[int] | None) -> Container[int]:
    """`train_climatology_years()` when the caller names none."""
    return train_climatology_years() if years is None else years


def _require_columns(frame: pd.DataFrame, columns: tuple[str, ...]) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(
            f"the derived anomaly table is missing {missing}; it has to carry the shared "
            "contract of techcamp.risk.domain.features.FEATURE_NAMES for the gate to score"
        )
