"""The M2 table: one row per municipality and month, through the shared features."""

import math

import pandas as pd
import pytest

from techcamp_ml.datasets.flood_m2 import COLUMNS, build_table, label_coverage

MARCH_RAIN_13001 = 2.0 * 28
"""February 2019 has 28 days at 2 mm, and March's own 100 mm a day stays out of it."""
JANUARY_TO_FEBRUARY_13001 = 2.0 * 59
"""59 days from 2019-01-01 to 2019-02-28, the two-month window of March."""
SLOPE_13001 = math.degrees(math.atan((110.0 - 10.0) / (2 * 1000.0)))
"""A 100 m rise to the east over 2 km, read by the shared module's central difference."""


def _row(table: pd.DataFrame, code: str, year: int, month: int) -> pd.Series:
    rows = table[(table["code"] == code) & (table["year"] == year) & (table["month"] == month)]
    assert len(rows) == 1, f"expected one row for {code} {year}-{month:02d}, got {len(rows)}"
    return rows.iloc[0]


def test_the_table_is_one_row_per_municipality_and_month(
    municipalities_frame: pd.DataFrame,
    weather_frame: pd.DataFrame,
    elevation_frame: pd.DataFrame,
    labels_frame: pd.DataFrame,
) -> None:
    table = build_table(municipalities_frame, weather_frame, elevation_frame, labels_frame)

    assert list(table.columns) == list(COLUMNS)
    assert [
        (code, year, month)
        for code, year, month in zip(table["code"], table["year"], table["month"], strict=True)
    ] == [
        ("08001", 2019, 1),
        ("08001", 2019, 2),
        ("08001", 2019, 3),
        ("13001", 2019, 1),
        ("13001", 2019, 2),
        ("13001", 2019, 3),
    ]
    # Negative assertion: April has weather but no label, and an unknown month is not a
    # negative (data card, sesgo 3), so it is not a row either.
    assert not (table["month"] == 4).any()


def test_the_horizon_columns_name_the_predicted_month(
    municipalities_frame: pd.DataFrame,
    weather_frame: pd.DataFrame,
    elevation_frame: pd.DataFrame,
    labels_frame: pd.DataFrame,
) -> None:
    table = build_table(municipalities_frame, weather_frame, elevation_frame, labels_frame)

    march = _row(table, "13001", 2019, 3)
    assert march["horizon_start"].date() == pd.Timestamp("2019-03-01").date()
    assert march["horizon_days"] == 31
    assert _row(table, "13001", 2019, 2)["horizon_days"] == 28


def test_the_features_of_a_month_read_only_the_months_before_it(
    municipalities_frame: pd.DataFrame,
    weather_frame: pd.DataFrame,
    elevation_frame: pd.DataFrame,
    labels_frame: pd.DataFrame,
) -> None:
    table = build_table(municipalities_frame, weather_frame, elevation_frame, labels_frame)

    march = _row(table, "13001", 2019, 3)
    assert march["precip_sum_1m"] == MARCH_RAIN_13001
    assert march["precip_sum_2m"] == JANUARY_TO_FEBRUARY_13001
    # Negative assertion: the 100 mm a day March itself received is not in its features.
    assert march["precip_sum_1m"] != 2.0 * 28 + 100.0 * 31


def test_a_missing_day_leaves_its_window_missing_and_not_zero(
    municipalities_frame: pd.DataFrame,
    weather_frame: pd.DataFrame,
    elevation_frame: pd.DataFrame,
    labels_frame: pd.DataFrame,
) -> None:
    table = build_table(municipalities_frame, weather_frame, elevation_frame, labels_frame)

    february = _row(table, "08001", 2019, 2)
    for column in ("precip_sum_1m", "precip_sum_6m", "soil_moisture_mean_1m"):
        assert math.isnan(february[column]), f"{column} reads a day ERA5 never reported"
        assert february[column] != 0, f"{column} claims zero rain out of missing evidence"
    # The month before the gap is whole: December 2018, 31 days at 2 mm.
    assert _row(table, "08001", 2019, 1)["precip_sum_1m"] == 2.0 * 31


def test_the_terrain_arrives_from_the_elevation_table(
    municipalities_frame: pd.DataFrame,
    weather_frame: pd.DataFrame,
    elevation_frame: pd.DataFrame,
    labels_frame: pd.DataFrame,
) -> None:
    table = build_table(municipalities_frame, weather_frame, elevation_frame, labels_frame)

    january = _row(table, "08001", 2019, 1)
    assert january["elevation_m"] == 10.0
    assert january["slope_deg"] == 0.0
    cartagena = _row(table, "13001", 2019, 3)
    assert cartagena["elevation_m"] == 20.0
    assert cartagena["slope_deg"] == pytest.approx(SLOPE_13001)


def test_the_anomaly_columns_stay_null_for_the_split_to_fill(
    municipalities_frame: pd.DataFrame,
    weather_frame: pd.DataFrame,
    elevation_frame: pd.DataFrame,
    labels_frame: pd.DataFrame,
) -> None:
    table = build_table(municipalities_frame, weather_frame, elevation_frame, labels_frame)

    # The climatology belongs to the train split (T5), so the table stores no anomaly:
    # missing evidence, never a zero anomaly.
    assert table["precip_anomaly_1m"].isna().all()
    assert table["precip_anomaly_3m"].isna().all()
    assert table["precip_anomaly_6m"].isna().all()
    # Negative assertion: the features that need no climatology are computed.
    assert table["month_sin"].notna().all()
    assert table["month_cos"].notna().all()


def test_a_flood_event_labels_its_municipality_month(
    municipalities_frame: pd.DataFrame,
    weather_frame: pd.DataFrame,
    elevation_frame: pd.DataFrame,
    labels_frame: pd.DataFrame,
) -> None:
    table = build_table(municipalities_frame, weather_frame, elevation_frame, labels_frame)

    assert _row(table, "08001", 2019, 3)["label"] == 1
    # Negative assertion: the second report of the same month adds no second row.
    assert len(table) == 6


def test_every_month_without_an_event_stays_a_negative(
    municipalities_frame: pd.DataFrame,
    weather_frame: pd.DataFrame,
    elevation_frame: pd.DataFrame,
    labels_frame: pd.DataFrame,
) -> None:
    table = build_table(municipalities_frame, weather_frame, elevation_frame, labels_frame)

    assert int(table["label"].sum()) == 1
    # Negative assertion: all of them, no subsampling (docs/08 §M2 "Negativos").
    assert sorted(table["label"]) == [0, 0, 0, 0, 0, 1]


def test_a_municipality_with_no_elevation_row_is_refused(
    municipalities_frame: pd.DataFrame,
    weather_frame: pd.DataFrame,
    elevation_frame: pd.DataFrame,
    labels_frame: pd.DataFrame,
) -> None:
    short = elevation_frame[elevation_frame["code"] != "08001"]

    with pytest.raises(ValueError, match="08001"):
        build_table(municipalities_frame, weather_frame, short, labels_frame)


def test_the_label_coverage_runs_from_the_first_declared_window_to_the_newest_report(
    labels_frame: pd.DataFrame,
) -> None:
    first, last = label_coverage(labels_frame)

    assert (first.year, first.month) == (2019, 1), "January of the earliest label window"
    assert (last.year, last.month) == (2019, 3), "the month of the newest report"


def test_an_empty_label_table_is_refused(
    municipalities_frame: pd.DataFrame,
    weather_frame: pd.DataFrame,
    elevation_frame: pd.DataFrame,
) -> None:
    with pytest.raises(ValueError, match="parse"):
        build_table(municipalities_frame, weather_frame, elevation_frame, pd.DataFrame())
