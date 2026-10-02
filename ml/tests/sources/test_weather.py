"""Open-Meteo archive: the query the serving client must repeat, and its parser."""

from collections.abc import Callable
from datetime import date

import pandas as pd
import pytest

from techcamp_ml.sources.fetching import chunks
from techcamp_ml.sources.municipalities import EXPECTED_MUNICIPALITIES
from techcamp_ml.sources.weather import (
    ARCHIVE_PARAMS,
    COORDINATE_BATCH,
    DAILY_VARIABLES,
    MAX_COORDINATES_PER_REQUEST,
    WEATHER_START,
    WINDOW_DAYS,
    archive_params,
    concat_windows,
    last_complete_month,
    parse_archive,
    weather_windows,
)


def test_archive_params_pin_era5_the_bogota_day_and_the_two_variables() -> None:
    assert ARCHIVE_PARAMS["models"] == "era5", "best_match/seamless change with the catalogue"
    assert ARCHIVE_PARAMS["daily"] == "precipitation_sum,soil_moisture_0_to_7cm_mean"
    assert ARCHIVE_PARAMS["timezone"] == "America/Bogota"
    assert ARCHIVE_PARAMS["daily"].split(",") == list(DAILY_VARIABLES)


def test_archive_params_start_one_day_before_the_range() -> None:
    params = archive_params(date(2018, 7, 1), date(2018, 12, 31))

    # The first day of an ERA5 window can come back null, so the window opens a day early.
    assert params["start_date"] == "2018-06-30"
    assert params["end_date"] == "2018-12-31"
    assert params["models"] == "era5"
    assert "best_match" not in str(params)


def test_last_complete_month_is_the_end_of_the_previous_month() -> None:
    assert last_complete_month(date(2026, 10, 8)) == date(2026, 9, 30)
    assert last_complete_month(date(2026, 3, 31)) == date(2026, 2, 28)


def test_parse_archive_builds_one_row_per_code_and_day(
    fixture: Callable[[str], bytes],
) -> None:
    frame = parse_archive(fixture("open_meteo_archive_era5.json"), ["08001", "20045"])

    assert list(frame.columns) == ["code", "date", *DAILY_VARIABLES]
    assert len(frame) == 12, "6 days x 2 municipalities, one row each"
    assert not frame.duplicated(subset=["code", "date"]).any()
    first = frame[(frame["code"] == "08001") & (frame["date"] == pd.Timestamp("2018-06-30"))].iloc[
        0
    ]
    assert first["precipitation_sum"] == pytest.approx(0.3)
    # Missing evidence is a third state: the null day stays missing, never 0.
    assert pd.isna(first["soil_moisture_0_to_7cm_mean"])
    later = frame[(frame["code"] == "20045") & (frame["date"] == pd.Timestamp("2018-07-05"))].iloc[
        0
    ]
    assert later["precipitation_sum"] == pytest.approx(0.5)
    assert later["soil_moisture_0_to_7cm_mean"] == pytest.approx(0.413)
    assert frame["date"].min() == pd.Timestamp(date(2018, 6, 30))


def test_weather_windows_cover_the_range_with_no_gap() -> None:
    windows = weather_windows(date(2018, 7, 1), date(2026, 9, 30))

    assert windows[0] == (date(2018, 7, 1), windows[0][1])
    assert windows[-1][1] == date(2026, 9, 30)
    for (_, end), (start, _) in zip(windows, windows[1:], strict=False):
        assert (start - end).days == 1, "a day between windows would leave a hole in the series"
    # Each window stays under the free tier's hourly weight (2 variables x its days).
    assert all((end - start).days + 1 <= WINDOW_DAYS for start, end in windows)


def test_concatenating_windows_drops_the_day_they_overlap_on(
    fixture: Callable[[str], bytes],
) -> None:
    payload = fixture("open_meteo_archive_era5.json")
    window = parse_archive(payload, ["08001", "20045"])

    joined = concat_windows([window, window])

    assert len(joined) == 12, "the extra day of the second window repeats the first one's last day"
    assert not joined.duplicated(subset=["code", "date"]).any()


def test_batching_bounds_a_request_without_hiding_any_municipality() -> None:
    """The weight counts locations, so batching must not skip or duplicate work.

    Open-Meteo weighs a query by variables, locations and domains, with a cap on the
    locations one request may carry. Both hold here: no request over the cap, and the
    batches cover every municipality exactly once.
    """
    windows = weather_windows(WEATHER_START, last_complete_month(date(2026, 10, 2)))
    assert len(windows) == 3, "8.2 years of range, four years per window"

    for _window in windows:
        batches = list(chunks(range(EXPECTED_MUNICIPALITIES), COORDINATE_BATCH))
        assert all(len(batch) <= MAX_COORDINATES_PER_REQUEST for batch in batches)
        assert sorted(index for batch in batches for index in batch) == list(
            range(EXPECTED_MUNICIPALITIES)
        )


def test_parse_archive_reads_a_single_location_body(fixture: Callable[[str], bytes]) -> None:
    """Open-Meteo answers one location with an object, not a list."""
    import json

    one = json.dumps(json.loads(fixture("open_meteo_archive_era5.json"))[0]).encode()

    frame = parse_archive(one, ["08001"])

    assert len(frame) == 6
    assert set(frame["code"]) == {"08001"}
    assert frame["precipitation_sum"].iloc[1] == pytest.approx(4.8)


def test_the_last_complete_month_waits_for_the_era5_delay() -> None:
    # ERA5 arrives with about five days of lag (docs/08 §Fuentes de datos de M2), so
    # 2026-09 is not a complete month on 2026-10-02: it still has days in flight.
    assert last_complete_month(date(2026, 10, 2)) == date(2026, 8, 31)
    assert last_complete_month(date(2026, 10, 8)) == date(2026, 9, 30)
    # Early in a month the lag reaches back into the one before it.
    assert last_complete_month(date(2026, 1, 1)) == date(2025, 11, 30)
