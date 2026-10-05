"""The train climatology of M2 and the anomaly of every municipality-month (ADR-0020 steps 4-5).

The anomalies are `None` in `flood_m2.parquet` on purpose: T4 built the table without a
climatology because the years are the split's (data card §Contrato de columnas), and the
harness names them (`split.train_climatology_years`). This module is the experiment step
that finally derives them, from the weather source and those years only.

Everything here is hand-computed on two municipalities over four years, so the numbers in
the assertions are the ones a reader can check with a calendar.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from techcamp.risk.domain.features import ANOMALY_WINDOWS_MONTHS, monthly_climatology

from techcamp_ml.harness.split import train_climatology_years
from techcamp_ml.models.flood_m2 import anomalies
from techcamp_ml.models.flood_m2.anomalies import anomaly_features, climatologies
from techcamp_ml.sources.layout import Layout

TRAIN_YEARS = (2019, 2020)
"""Two train years, so a third one can be the control: the 2021 rain must not move a
climatology built from 2019 and 2020 (docs/08 §M2 "Features": anomalies against the *train*
climatology)."""

WEATHER_FIRST = date(2018, 7, 1)
WEATHER_LAST = date(2021, 3, 31)
"""Six months before the first issue month, as the real archive starts (docs/08 §M2
"Features"), and a whole month past 2021 so a 2021 window has all of its days."""

DAILY_MM = {
    "08001": {2018: 1.0, 2019: 2.0, 2020: 2.0, 2021: 2.0},
    "08002": {2018: 1.0, 2019: 3.0, 2020: 5.0, 2021: 100.0},
}
"""`08001` rains the same every train year, so its 1-month anomaly is the leap day and
nothing else; `08002` rains harder in 2020 and a hundred times harder in 2021, which is the
rain that must stay out of the climatology. 2018 is the archive year before the window: it
completes the six-month windows of 2019 and is not a train year either."""

ISSUE_MONTHS = ("2019-03", "2020-03", "2021-03")
"""Three issue months: one per year, the last one outside the train years."""

UNANSWERED = "08003"
"""A municipality the archive never answered: its evidence is missing, not zero."""


def weather_frame() -> pd.DataFrame:
    """One row per municipality and day, the shape `sources/weather.parquet` has."""
    records = []
    for code, by_year in DAILY_MM.items():
        day = WEATHER_FIRST
        while day <= WEATHER_LAST:
            records.append(
                {
                    "code": code,
                    "date": pd.Timestamp(day),
                    "precipitation_sum": by_year[day.year],
                    "soil_moisture_0_to_7cm_mean": 0.3,
                }
            )
            day += timedelta(days=1)
    return pd.DataFrame(records)


def table_frame() -> pd.DataFrame:
    """The dataset rows the anomalies are derived for, one per municipality and month."""
    rows = []
    for code in (*DAILY_MM, UNANSWERED):
        for month in ISSUE_MONTHS:
            year, number = int(month[:4]), int(month[5:])
            rows.append(
                {
                    "code": code,
                    "year": year,
                    "month": number,
                    "horizon_start": pd.Timestamp(date(year, number, 1)),
                    "label": 0,
                }
            )
    return pd.DataFrame(rows)


def derived_frame() -> pd.DataFrame:
    series = anomalies.precipitation_by_code(weather_frame())
    return anomaly_features(
        table_frame(),
        series,
        {code: monthly_climatology(days, years=TRAIN_YEARS) for code, days in series.items()},
    )


def test_the_climatology_averages_the_train_years_and_no_other() -> None:
    series = anomalies.precipitation_by_code(weather_frame())
    climatology = climatologies(weather_frame(), years=TRAIN_YEARS)
    with_the_leaked_year = climatologies(weather_frame(), years=(*TRAIN_YEARS, 2021))

    # 08002 rains 3 mm a day in 2019 and 5 in 2020: February is 28 days in one and 29 in
    # the other, so the mean of the two yearly totals is (84 + 145) / 2 and not 4 * 28.5.
    assert climatology["08002"][2] == pytest.approx(114.5)
    assert climatology["08001"][2] == pytest.approx(57.0)
    assert series["08002"][date(2019, 2, 1)] == 3.0
    # 2021 rains a hundred times harder, and it is not a train year.
    assert with_the_leaked_year["08002"][2] != pytest.approx(114.5)


def test_an_anomaly_is_the_accumulation_minus_that_climatology() -> None:
    derived = derived_frame()
    february_2020 = derived[
        (derived["code"] == "08002") & (derived["horizon_start"] == pd.Timestamp("2020-03-01"))
    ].iloc[0]

    # The one-month window of a March issue month is February: 29 days of 5 mm in the leap
    # year 2020, against a February climatology of (28x3 + 29x5) / 2.
    assert february_2020["precip_anomaly_1m"] == pytest.approx(145.0 - 114.5)


def test_an_issue_month_outside_the_train_years_uses_the_train_climatology() -> None:
    derived = derived_frame()
    march_2021 = derived[
        (derived["code"] == "08002") & (derived["horizon_start"] == pd.Timestamp("2021-03-01"))
    ].iloc[0]

    # 28 days of 100 mm minus a climatology that never saw 2021. Reading the row's own year
    # would answer 0.0, which is the leakage the train climatology exists to prevent.
    assert march_2021["precip_anomaly_1m"] == pytest.approx(2800.0 - 114.5)


def test_a_municipality_the_archive_never_answered_keeps_its_anomalies_null() -> None:
    """Missing evidence is `None`, never `0` (docs/08 §M2 "Features")."""
    derived = derived_frame()
    unanswered = derived[derived["code"] == UNANSWERED]

    assert len(unanswered) == len(ISSUE_MONTHS)
    assert unanswered[list(anomalies.ANOMALY_COLUMNS)].isna().all().all()


def test_the_derived_table_holds_one_row_per_dataset_row_and_the_shared_contract() -> None:
    table = table_frame()
    derived = derived_frame()

    assert list(derived["code"]) == list(table["code"])
    assert list(derived["horizon_start"]) == list(table["horizon_start"])
    assert anomalies.ANOMALY_COLUMNS == tuple(
        f"precip_anomaly_{months}m" for months in ANOMALY_WINDOWS_MONTHS
    )
    assert list(derived.columns) == ["code", "horizon_start", *anomalies.ANOMALY_COLUMNS]


def test_a_repeated_municipality_day_is_refused_rather_than_resolved() -> None:
    weather = pd.concat([weather_frame(), weather_frame().head(1)], ignore_index=True)

    with pytest.raises(ValueError, match="repeated"):
        anomalies.precipitation_by_code(weather)


def _with_null_anomalies() -> pd.DataFrame:
    """The dataset as T4 wrote it: the three anomaly columns present and null."""
    table = table_frame()
    for column in anomalies.ANOMALY_COLUMNS:
        table[column] = None
    return table


def test_attaching_the_anomalies_fills_the_null_columns_and_keeps_the_rest() -> None:
    table = _with_null_anomalies()
    table["precip_sum_1m"] = 12.0

    joined = anomalies.attach_anomalies(table, derived_frame())

    assert list(joined.columns) == list(table.columns)
    assert joined["precip_sum_1m"].tolist() == [12.0] * len(table)
    assert joined["label"].tolist() == table["label"].tolist()
    february_2020 = joined[
        (joined["code"] == "08002") & (joined["horizon_start"] == pd.Timestamp("2020-03-01"))
    ].iloc[0]
    assert february_2020["precip_anomaly_1m"] == pytest.approx(30.5)


def test_a_derived_table_that_misses_a_row_is_refused_not_zero_filled() -> None:
    with pytest.raises(ValueError, match="covers 8 of the 9"):
        anomalies.attach_anomalies(_with_null_anomalies(), derived_frame().iloc[:-1])


def test_a_derived_table_that_repeats_a_key_is_refused() -> None:
    doubled = pd.concat([derived_frame(), derived_frame().head(1)], ignore_index=True)

    with pytest.raises(ValueError, match="more than once"):
        anomalies.attach_anomalies(_with_null_anomalies(), doubled)


def test_the_written_table_reloads_the_same_features(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    layout.data.mkdir(parents=True, exist_ok=True)
    weather_frame().to_parquet(layout.data / "weather.parquet", index=False)
    anomalies.dataset_path(layout).parent.mkdir(parents=True, exist_ok=True)
    _with_null_anomalies().to_parquet(anomalies.dataset_path(layout), index=False)

    written = anomalies.build_anomalies(layout, years=TRAIN_YEARS)
    loaded = anomalies.load_features(layout, years=TRAIN_YEARS)

    assert written.is_file()
    assert list(loaded.columns) == list(_with_null_anomalies().columns)
    february_2020 = loaded[
        (loaded["code"] == "08002") & (loaded["horizon_start"] == pd.Timestamp("2020-03-01"))
    ].iloc[0]
    assert february_2020["precip_anomaly_1m"] == pytest.approx(30.5)


def test_the_derived_table_is_a_build_product_outside_the_hashed_dataset() -> None:
    """The manifest hashes `flood_m2.parquet` and its sha256 is not touched (owner,
    2026-10-05): the anomalies go to their own parquet, out of git."""
    layout = Layout(Path("/repo"))

    assert anomalies.derived_path(layout) == Path("/repo/data/flood_m2/derived/anomalies.parquet")
    assert anomalies.derived_path(layout) != anomalies.dataset_path(layout)
    assert anomalies.derived_path(layout).parent.parent != anomalies.dataset_path(layout).parent


def test_the_years_the_prepass_reads_are_the_split_ones() -> None:
    assert train_climatology_years() == (2019, 2020, 2021, 2022)
