"""The train climatology of M2, and the anomaly of every municipality-month (ADR-0020).

`flood_m2.parquet` holds `precip_anomaly_1m/3m/6m` as null columns, because T4 built the
table without a climatology: the years the anomalies are measured against are the **split's**
(data card §Contrato de columnas), and the harness is what names them —
`split.train_climatology_years()` is 2019–2022 and a year the gate decides on never appears
in it. This module is the experiment step that finally derives them, from the weather
source and those years only.

Two properties are worth the reader's attention:

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
from typing import Any

import pandas as pd
from techcamp.risk.domain.features import (
    ANOMALY_WINDOWS_MONTHS,
    DailySeries,
    monthly_climatology,
    precip_anomaly,
)

ANOMALY_WINDOWS = ANOMALY_WINDOWS_MONTHS
"""The anomaly windows of docs/08 §M2 "Features", read from the shared module rather than
written here: a second list would be a second answer to a question parity already answers."""

ANOMALY_COLUMNS: tuple[str, ...] = tuple(f"precip_anomaly_{months}m" for months in ANOMALY_WINDOWS)
"""The column names of the shared feature contract, in its order."""

KEY_COLUMNS = ("code", "horizon_start")
"""What a municipality-month is: the same unit docs/08 §M2 "Unidad" names, read off the
dataset's own columns rather than rebuilt from the year and the month."""

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
