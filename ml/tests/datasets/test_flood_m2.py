"""The M2 table: one row per municipality and month, through the shared features."""

import math
from datetime import date

import pandas as pd
import pytest

from techcamp_ml.datasets.flood_m2 import COLUMNS, build_table, label_coverage

MUNICIPALITIES = 195
"""docs/08 §M2 "Región". The fixture is the real region, so the guard is the real one."""
MONTHS = 84
"""2019-01 to 2025-12, the label window of docs/08 §Fuentes de datos de M2."""
ROWS = MUNICIPALITIES * MONTHS

MARCH_PRECIP_13001 = {
    "precip_sum_1m": 2.0 * 28,
    "precip_sum_2m": 2.0 * 59,
    "precip_sum_3m": 2.0 * 90,
    "precip_sum_6m": 2.0 * 181,
}
"""The windows of 2019-03 all end on 2019-02-28: 28 days back to February, 59 to January,
90 to December 2018, 181 to September 2018 — every one of them at the 2 mm a day the
fixture gives, and none of them reading a day of March."""
MARCH_OWN_RAIN = 100.0 * 31
"""What March itself received, which none of its own windows may read."""

SLOPE_13001 = math.degrees(math.atan((110.0 - 10.0) / (2 * 1000.0)))
"""A 100 m rise to the east over 2 km, read by the shared module's central difference."""


def _build(
    municipalities: pd.DataFrame,
    weather: pd.DataFrame,
    elevation: pd.DataFrame,
    labels: pd.DataFrame,
) -> pd.DataFrame:
    return build_table(municipalities, weather, elevation, labels)


def _row(table: pd.DataFrame, code: str, year: int, month: int) -> pd.Series:
    rows = table[(table["code"] == code) & (table["year"] == year) & (table["month"] == month)]
    assert len(rows) == 1, f"expected one row for {code} {year}-{month:02d}, got {len(rows)}"
    return rows.iloc[0]


def test_the_table_is_one_row_per_municipality_and_month(
    region_codes: list[str],
    municipalities_frame: pd.DataFrame,
    weather_frame: pd.DataFrame,
    elevation_frame: pd.DataFrame,
    labels_frame: pd.DataFrame,
) -> None:
    table = _build(municipalities_frame, weather_frame, elevation_frame, labels_frame)

    assert list(table.columns) == list(COLUMNS)
    months = list(zip(table["year"], table["month"], strict=True))
    assert len(table) == ROWS
    assert sorted(set(table["code"])) == region_codes
    assert len(set(months)) == MONTHS
    assert months[0] == (2019, 1)
    assert months[-1] == (2025, 12)
    # Negative assertion: 2026 has weather in the fixture and no label in the window, and
    # an unknown month is not a negative (data card, sesgo 3), so it is not a row.
    assert (2026, 1) not in months


def test_the_horizon_columns_name_the_predicted_month(
    municipalities_frame: pd.DataFrame,
    weather_frame: pd.DataFrame,
    elevation_frame: pd.DataFrame,
    labels_frame: pd.DataFrame,
) -> None:
    table = _build(municipalities_frame, weather_frame, elevation_frame, labels_frame)

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
    table = _build(municipalities_frame, weather_frame, elevation_frame, labels_frame)

    march = _row(table, "13001", 2019, 3)
    for column, expected in MARCH_PRECIP_13001.items():
        assert march[column] == expected, column
        # Negative assertion: the 100 mm a day March itself received is in no window.
        assert march[column] != expected + MARCH_OWN_RAIN, column


def test_a_missing_day_leaves_its_window_missing_and_not_zero(
    municipalities_frame: pd.DataFrame,
    weather_frame: pd.DataFrame,
    elevation_frame: pd.DataFrame,
    labels_frame: pd.DataFrame,
) -> None:
    table = _build(municipalities_frame, weather_frame, elevation_frame, labels_frame)

    february = _row(table, "08001", 2019, 2)
    for column in ("precip_sum_1m", "precip_sum_6m", "soil_moisture_mean_1m"):
        assert math.isnan(february[column]), f"{column} reads a day ERA5 never reported"
        assert february[column] != 0, f"{column} claims zero rain out of missing evidence"
    # The month before the gap is whole: December 2018, 31 days at 2 mm.
    assert _row(table, "08001", 2019, 1)["precip_sum_1m"] == 2.0 * 31
    # Negative assertion: the gap does not leak forward either — June 2019 is whole again
    # once the missing January day is behind its window.
    assert _row(table, "08001", 2019, 7)["precip_sum_1m"] == 2.0 * 30


def test_a_municipality_the_archive_never_answered_keeps_its_row_with_null_features(
    region_codes: list[str],
    municipalities_frame: pd.DataFrame,
    weather_frame: pd.DataFrame,
    elevation_frame: pd.DataFrame,
    labels_frame: pd.DataFrame,
) -> None:
    table = _build(municipalities_frame, weather_frame, elevation_frame, labels_frame)

    unanswered = table[table["code"] == region_codes[2]].reset_index(drop=True)
    assert len(unanswered) == MONTHS, "the month is known even when its evidence is not"
    assert unanswered["precip_sum_1m"].isna().all()
    assert unanswered["soil_moisture_mean_1m"].isna().all()
    # Negative assertion: what the archive did answer is still there, and the month is not
    # dropped from the negatives.
    assert unanswered["elevation_m"].notna().all()
    assert set(unanswered["label"]) == {0}


def test_the_terrain_arrives_from_the_elevation_table(
    municipalities_frame: pd.DataFrame,
    weather_frame: pd.DataFrame,
    elevation_frame: pd.DataFrame,
    labels_frame: pd.DataFrame,
) -> None:
    table = _build(municipalities_frame, weather_frame, elevation_frame, labels_frame)

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
    table = _build(municipalities_frame, weather_frame, elevation_frame, labels_frame)

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
    table = _build(municipalities_frame, weather_frame, elevation_frame, labels_frame)

    assert _row(table, "08001", 2019, 3)["label"] == 1
    # Negative assertion: the second report of the same month adds no second row.
    assert len(table[table["label"] == 1]) == 1
    # The other municipality of that same month stays a negative.
    assert _row(table, "13001", 2019, 3)["label"] == 0


def test_every_month_without_an_event_stays_a_negative(
    municipalities_frame: pd.DataFrame,
    weather_frame: pd.DataFrame,
    elevation_frame: pd.DataFrame,
    labels_frame: pd.DataFrame,
) -> None:
    table = _build(municipalities_frame, weather_frame, elevation_frame, labels_frame)

    assert int(table["label"].sum()) == 1
    # Negative assertion: all of them, no subsampling (docs/08 §M2 "Negativos").
    assert len(table[table["label"] == 0]) == ROWS - 1


def test_a_region_that_is_not_the_caribbean_is_refused(
    municipalities_frame: pd.DataFrame,
    weather_frame: pd.DataFrame,
    elevation_frame: pd.DataFrame,
    labels_frame: pd.DataFrame,
) -> None:
    # The parse owns the region, but a dataset built over another one would train M2 on
    # municipalities docs/08 §M2 "Región" never named (docs/08:68).
    with pytest.raises(ValueError, match="195"):
        _build(municipalities_frame.head(2), weather_frame, elevation_frame, labels_frame)


def test_a_municipality_with_no_elevation_row_is_refused(
    region_codes: list[str],
    municipalities_frame: pd.DataFrame,
    weather_frame: pd.DataFrame,
    elevation_frame: pd.DataFrame,
    labels_frame: pd.DataFrame,
) -> None:
    absent = region_codes[2]
    short = elevation_frame[elevation_frame["code"] != absent]

    with pytest.raises(ValueError, match=absent):
        _build(municipalities_frame, weather_frame, short, labels_frame)


def test_the_label_coverage_is_the_window_the_docs_name(labels_frame: pd.DataFrame) -> None:
    first, last = label_coverage(labels_frame)

    assert first == date(2019, 1, 1), "the window docs/08 declares opens"
    assert last == date(2025, 12, 1)
    # Negative assertion: not the newest report, which here is March 2019. A window that
    # moved with the data would shrink the dataset every time a download did.
    assert last != date(2019, 3, 1)


def test_an_empty_label_table_is_refused(
    municipalities_frame: pd.DataFrame,
    weather_frame: pd.DataFrame,
    elevation_frame: pd.DataFrame,
) -> None:
    with pytest.raises(ValueError, match="parse"):
        _build(municipalities_frame, weather_frame, elevation_frame, pd.DataFrame())
