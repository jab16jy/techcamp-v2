"""Flood-risk (M2) shared feature tests.

Pure calculations for the one feature module both sides import
(`techcamp.risk.domain.features`): the dataset builder in `ml/` and the daily
serving job. Parity is a governance rule (docs/08-ml.md §M2 "Features" and
§Reglas de gobierno "Paridad de features"; docs/06-diseno-detallado.md §8), so
the window, the climatology and the missing-evidence rules are all pinned here.

Expected values are hand-derived and the arithmetic is stated in each test.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

import pytest

from techcamp.risk.domain.features import (
    FEATURE_NAMES,
    Neighbours,
    build_features,
    monthly_climatology,
    precip_anomaly,
    precip_sum,
    seasonality,
    slope_degrees,
    soil_moisture_mean,
)

ISSUE_MONTH = date(2024, 11, 1)
"""Issue month M = November 2024, given as its first day. Every window ends on
2024-10-31, the last day of M-1 (docs/08 §M2 "Horizonte")."""

# Rain per calendar month of the 6-month window (May..October 2024), each day of a
# month carrying the same value so the monthly totals are exact:
#   May 31 d x 1.0 = 31   Jun 30 x 2.0 = 60   Jul 31 x 3.0 = 93
#   Aug 31 x 4.0 = 124   Sep 30 x 5.0 = 150  Oct 31 x 6.0 = 186
MONTHLY_RAIN_MM = {5: 1.0, 6: 2.0, 7: 3.0, 8: 4.0, 9: 5.0, 10: 6.0}
MONTH_TOTALS_MM = {5: 31.0, 6: 60.0, 7: 93.0, 8: 124.0, 9: 150.0, 10: 186.0}
# Expected window sums, accumulating from October backwards:
#   1m = 186          2m = 150 + 186 = 336      3m = 124 + 336 = 460
#   4m = 93 + 460 = 553   5m = 60 + 553 = 613   6m = 31 + 613 = 644
EXPECTED_PRECIP_SUMS_MM = {
    1: 186.0,
    2: 336.0,
    3: 460.0,
    4: 553.0,
    5: 613.0,
    6: 644.0,
}
# Train climatology (mean monthly total per calendar month, in mm):
#   1m: 186 - 150 = 36
#   3m: 460 - (100 + 120 + 150) = 460 - 370 = 90
#   6m: 644 - (20 + 50 + 80 + 100 + 120 + 150) = 644 - 520 = 124
CLIMATOLOGY_MM = {5: 20.0, 6: 50.0, 7: 80.0, 8: 100.0, 9: 120.0, 10: 150.0}
EXPECTED_ANOMALIES_MM = {1: 36.0, 3: 90.0, 6: 124.0}

# Soil moisture over M-1 (October 2024, 31 days): 15 days at 0.20 and 16 at 0.30,
# so the mean is (15 x 0.20 + 16 x 0.30) / 31 = 7.8 / 31.
SOIL_DRY_DAYS = 15
SOIL_DRY_M3 = 0.20
SOIL_WET_M3 = 0.30
EXPECTED_SOIL_MEAN_M3 = (SOIL_DRY_DAYS * SOIL_DRY_M3 + 16 * SOIL_WET_M3) / 31

ELEVATION_M = 12.5
# 4 neighbours 1 km apart: dz/dx = (25 - 0) / (2 x 1000) = 0.0125 and
# dz/dy = (25 - 0) / (2 x 1000) = 0.0125, so
# slope = degrees(atan(hypot(0.0125, 0.0125))) = degrees(atan(0.0176776695)).
NEIGHBOURS = Neighbours(east=25.0, west=0.0, north=25.0, south=0.0, spacing_m=1000.0)
EXPECTED_SLOPE_DEG = 1.0127503696363709
# Seasonality of M: angle = 2 x pi x (11 - 1) / 12 = 5 pi / 3 = 300 degrees,
# sin = -0.8660254037844386 and cos = 0.5.
EXPECTED_MONTH_SIN = -0.8660254037844386
EXPECTED_MONTH_COS = 0.5


def _series(
    start: date,
    end: date,
    value_for: Callable[[date], float],
) -> dict[date, float]:
    return {
        start + timedelta(days=offset): value_for(start + timedelta(days=offset))
        for offset in range((end - start).days + 1)
    }


def _precip_series() -> dict[date, float]:
    """Rain over the 6-month window, 2024-05-01..2024-10-31."""
    return _series(date(2024, 5, 1), date(2024, 10, 31), lambda day: MONTHLY_RAIN_MM[day.month])


def _soil_series() -> dict[date, float]:
    """Soil moisture over M-1 only, 2024-10-01..2024-10-31."""
    return _series(
        date(2024, 10, 1),
        date(2024, 10, 31),
        lambda day: SOIL_DRY_M3 if day.day <= SOIL_DRY_DAYS else SOIL_WET_M3,
    )


def test_feature_names_are_the_documented_contract() -> None:
    """The ordered tuple is the public contract both sides build their column
    order from (docs/08 §M2 "Features")."""
    assert FEATURE_NAMES == (
        "precip_sum_1m",
        "precip_sum_2m",
        "precip_sum_3m",
        "precip_sum_4m",
        "precip_sum_5m",
        "precip_sum_6m",
        "precip_anomaly_1m",
        "precip_anomaly_3m",
        "precip_anomaly_6m",
        "soil_moisture_mean_1m",
        "elevation_m",
        "slope_deg",
        "month_sin",
        "month_cos",
    )
    assert len(set(FEATURE_NAMES)) == len(FEATURE_NAMES)


def test_build_features_matches_the_hand_computed_arithmetic() -> None:
    features = build_features(
        issue_month=ISSUE_MONTH,
        precipitation=_precip_series(),
        soil_moisture=_soil_series(),
        elevation_m=ELEVATION_M,
        neighbours=NEIGHBOURS,
        climatology=CLIMATOLOGY_MM,
    )

    assert list(features) == list(FEATURE_NAMES)
    for months, expected in EXPECTED_PRECIP_SUMS_MM.items():
        assert features[f"precip_sum_{months}m"] == pytest.approx(expected)
    for months, expected in EXPECTED_ANOMALIES_MM.items():
        assert features[f"precip_anomaly_{months}m"] == pytest.approx(expected)
    assert features["soil_moisture_mean_1m"] == pytest.approx(EXPECTED_SOIL_MEAN_M3)
    assert features["elevation_m"] == ELEVATION_M
    assert features["slope_deg"] == pytest.approx(EXPECTED_SLOPE_DEG)
    assert features["month_sin"] == pytest.approx(EXPECTED_MONTH_SIN)
    assert features["month_cos"] == pytest.approx(EXPECTED_MONTH_COS, abs=1e-12)


def test_build_features_ignores_every_day_on_or_after_the_issue_month() -> None:
    """The prediction for M is issued with data through the last day of M-1
    (docs/08 §M2 "Horizonte"): a day of M itself may not move any feature."""
    before = build_features(
        issue_month=ISSUE_MONTH,
        precipitation=_precip_series(),
        soil_moisture=_soil_series(),
        elevation_m=ELEVATION_M,
        neighbours=NEIGHBOURS,
        climatology=CLIMATOLOGY_MM,
    )
    leaked_precip = _precip_series() | _series(
        date(2024, 11, 1), date(2024, 11, 30), lambda day: 999.0
    )
    leaked_soil = _soil_series() | {date(2024, 11, 5): 0.99, date(2024, 12, 1): 0.99}

    after = build_features(
        issue_month=ISSUE_MONTH,
        precipitation=leaked_precip,
        soil_moisture=leaked_soil,
        elevation_m=ELEVATION_M,
        neighbours=NEIGHBOURS,
        climatology=CLIMATOLOGY_MM,
    )

    assert after == before
    assert after["precip_sum_1m"] == pytest.approx(186.0)


def test_build_features_ignores_days_before_the_longest_window() -> None:
    """Six months is the longest window, so the split gap needs it (docs/08 §M2
    "Particion"); anything older than its first day is out of the features."""
    before = build_features(
        issue_month=ISSUE_MONTH,
        precipitation=_precip_series(),
        soil_moisture=_soil_series(),
        elevation_m=ELEVATION_M,
        neighbours=NEIGHBOURS,
        climatology=CLIMATOLOGY_MM,
    )
    older = _series(date(2024, 1, 1), date(2024, 4, 30), lambda day: 500.0)

    after = build_features(
        issue_month=ISSUE_MONTH,
        precipitation=_precip_series() | older,
        soil_moisture=_soil_series(),
        elevation_m=ELEVATION_M,
        neighbours=NEIGHBOURS,
        climatology=CLIMATOLOGY_MM,
    )

    assert after == before
    assert after["precip_sum_6m"] == pytest.approx(644.0)


def test_precip_sum_is_none_when_a_day_of_the_window_is_missing() -> None:
    """Missing evidence is a third state: `None`, never 0 (a 0 mm sum would read
    as a dry month and train the model on a fiction)."""
    series = _precip_series()
    del series[date(2024, 9, 10)]

    # The 1m window (October) is untouched: 31 d x 6.0 still sums to 186.
    assert precip_sum(series, issue_month=ISSUE_MONTH, months=1) == pytest.approx(186.0)
    # The 2m window (September + October) and every longer one read the gap.
    assert precip_sum(series, issue_month=ISSUE_MONTH, months=2) is None
    assert precip_sum(series, issue_month=ISSUE_MONTH, months=6) is None
    # And a day the source reported as `None` is missing just the same.
    assert (
        precip_sum(series | {date(2024, 10, 15): None}, issue_month=ISSUE_MONTH, months=1) is None
    )


def test_precip_sum_is_none_for_a_window_the_series_does_not_cover() -> None:
    assert precip_sum(
        _series(date(2024, 10, 1), date(2024, 10, 31), lambda day: 1.0),
        issue_month=ISSUE_MONTH,
        months=1,
    ) == pytest.approx(31.0)
    assert (
        precip_sum(
            _series(date(2024, 10, 1), date(2024, 10, 31), lambda day: 1.0),
            issue_month=ISSUE_MONTH,
            months=2,
        )
        is None
    )


def test_soil_moisture_mean_is_none_when_a_day_of_the_previous_month_is_missing() -> None:
    series = _soil_series()
    assert soil_moisture_mean(series, issue_month=ISSUE_MONTH) == pytest.approx(
        EXPECTED_SOIL_MEAN_M3
    )

    del series[date(2024, 10, 31)]

    assert soil_moisture_mean(series, issue_month=ISSUE_MONTH) is None
    assert soil_moisture_mean(_soil_series(), issue_month=date(2024, 12, 1)) is None


def test_climatology_is_built_only_from_the_given_years() -> None:
    """Anomalies are taken against the climatology of train (docs/08 §M2
    "Features"); a year outside that split must not move it."""
    series = _series(date(2022, 10, 1), date(2022, 10, 31), lambda day: 100.0 / 31)
    series |= _series(date(2023, 10, 1), date(2023, 10, 31), lambda day: 140.0 / 31)
    series |= _series(date(2024, 10, 1), date(2024, 10, 31), lambda day: 999.0 / 31)

    train_only = monthly_climatology(series, years={2022, 2023})

    assert train_only == {10: pytest.approx(120.0)}
    assert monthly_climatology(series, years={2023}) == {10: pytest.approx(140.0)}
    # October of a year with no rain at all is not a 0 mm month either.
    assert monthly_climatology({}, years={2022}) == {}


def test_climatology_averages_the_years_with_a_complete_month() -> None:
    series = _series(date(2022, 10, 1), date(2022, 10, 31), lambda day: 100.0 / 31)
    series |= _series(date(2023, 10, 1), date(2023, 10, 31), lambda day: 140.0 / 31)
    # 2024 October is incomplete: the 15th is absent, so that year contributes no
    # total and the mean stays (100 + 140) / 2 = 120.
    partial = _series(date(2024, 10, 1), date(2024, 10, 31), lambda day: 900.0 / 31)
    del partial[date(2024, 10, 15)]

    climatology = monthly_climatology(series | partial, years={2022, 2023, 2024})

    assert climatology[10] == pytest.approx(120.0)


def test_monthly_climatology_keeps_every_calendar_month_separate() -> None:
    """May 2022: 31 d x 1.0 = 31. June 2022: 30 d x 1.0 = 30. One calendar month
    is the mean of its own yearly totals, never of daily values."""
    series = _series(date(2022, 5, 1), date(2022, 5, 31), lambda day: 1.0)
    series |= _series(date(2022, 6, 1), date(2022, 6, 30), lambda day: 1.0)
    series |= _series(date(2023, 5, 1), date(2023, 5, 31), lambda day: 3.0)

    climatology = monthly_climatology(series, years={2022, 2023})

    assert climatology[5] == pytest.approx((31.0 + 93.0) / 2)
    assert climatology[6] == pytest.approx(30.0)


def test_anomaly_is_none_when_the_climatology_has_no_matching_month() -> None:
    series = _precip_series()
    # Only October is in the climatology, so the 3m and 6m windows need months the
    # train split never covered: their anomalies are unknown, not zero.
    anomalies = build_features(
        issue_month=ISSUE_MONTH,
        precipitation=series,
        soil_moisture=_soil_series(),
        elevation_m=ELEVATION_M,
        neighbours=NEIGHBOURS,
        climatology={10: 150.0},
    )

    assert anomalies["precip_anomaly_1m"] == pytest.approx(36.0)
    assert anomalies["precip_anomaly_3m"] is None
    assert anomalies["precip_anomaly_6m"] is None


def test_missing_climatology_or_terrain_leaves_those_features_none() -> None:
    """Serving has no climatology for a fresh cell and the dataset builder has no
    elevation until the source answers: those features stay `None` (a third
    state), never 0, and the rest of the vector still computes."""
    features = build_features(
        issue_month=ISSUE_MONTH,
        precipitation=_precip_series(),
        soil_moisture=_soil_series(),
    )

    assert list(features) == list(FEATURE_NAMES)
    assert features["precip_anomaly_1m"] is None
    assert features["precip_anomaly_3m"] is None
    assert features["precip_anomaly_6m"] is None
    assert features["elevation_m"] is None
    assert features["slope_deg"] is None
    assert features["precip_sum_1m"] == pytest.approx(186.0)
    assert features["soil_moisture_mean_1m"] == pytest.approx(EXPECTED_SOIL_MEAN_M3)
    assert features["month_sin"] == pytest.approx(EXPECTED_MONTH_SIN)


def test_slope_degrees_is_zero_on_flat_terrain() -> None:
    flat = Neighbours(east=10.0, west=10.0, north=10.0, south=10.0, spacing_m=1000.0)

    assert slope_degrees(flat) == 0.0
    # A single axis tilted: dz/dx = (25 - 0) / (2 x 1000) = 0.0125, dz/dy = 0, so
    # slope = degrees(atan(0.0125)).
    assert slope_degrees(
        Neighbours(east=25.0, west=0.0, north=10.0, south=10.0, spacing_m=1000.0)
    ) == pytest.approx(0.7161599454704085)


def test_slope_degrees_rejects_a_non_positive_spacing() -> None:
    with pytest.raises(ValueError, match="spacing_m"):
        slope_degrees(Neighbours(east=1.0, west=0.0, north=0.0, south=0.0, spacing_m=0.0))
    with pytest.raises(ValueError, match="spacing_m"):
        slope_degrees(Neighbours(east=1.0, west=0.0, north=0.0, south=0.0, spacing_m=-500.0))


def test_seasonality_puts_january_at_the_phase_origin() -> None:
    assert seasonality(1) == (0.0, 1.0)
    # July is half a turn: angle = 2 x pi x 6 / 12 = pi.
    assert seasonality(7) == (pytest.approx(0.0, abs=1e-12), pytest.approx(-1.0))


def test_issue_month_is_read_as_its_calendar_month() -> None:
    """The job knows the day it runs; only its calendar month identifies M."""
    from_first = build_features(
        issue_month=date(2024, 11, 1),
        precipitation=_precip_series(),
        soil_moisture=_soil_series(),
        elevation_m=ELEVATION_M,
        neighbours=NEIGHBOURS,
        climatology=CLIMATOLOGY_MM,
    )

    from_mid_month = build_features(
        issue_month=date(2024, 11, 17),
        precipitation=_precip_series(),
        soil_moisture=_soil_series(),
        elevation_m=ELEVATION_M,
        neighbours=NEIGHBOURS,
        climatology=CLIMATOLOGY_MM,
    )

    assert from_mid_month == from_first


def test_a_mid_month_issue_date_never_reads_days_of_the_issue_month() -> None:
    """The job runs on whatever day it runs, so the public helpers get a date
    inside M, not its first day. Read as given, the window would end inside M and
    train or predict on days of the month being predicted, which docs/08 §M2
    "Horizonte" forbids (#237 R3-004).

    November 2024 carries 999.0 mm/day from its 1st to its 16th: a leaked 1m
    window from 2024-11-17 would sum 186 + 16 x 999 = 16170 mm, while the correct
    window ends on 2024-10-31 and sums October's 31 d x 6.0 = 186 mm.
    """
    mid_month = date(2024, 11, 17)
    leaked_days = _series(date(2024, 11, 1), date(2024, 11, 16), lambda day: 999.0)
    leaked_soil = {date(2024, 11, 5): 0.99}

    assert precip_sum(_precip_series() | leaked_days, issue_month=mid_month, months=1) == (
        pytest.approx(186.0)
    )
    assert precip_anomaly(
        _precip_series() | leaked_days,
        issue_month=mid_month,
        months=1,
        climatology=CLIMATOLOGY_MM,
    ) == pytest.approx(36.0)
    assert soil_moisture_mean(_soil_series() | leaked_soil, issue_month=mid_month) == pytest.approx(
        EXPECTED_SOIL_MEAN_M3
    )
    # The negative: a leaked window would have moved all three by the November days.
    assert precip_sum(_precip_series() | leaked_days, issue_month=mid_month, months=1) != (
        pytest.approx(186.0 + 16 * 999.0)
    )
    assert build_features(
        issue_month=mid_month,
        precipitation=_precip_series() | leaked_days,
        soil_moisture=_soil_series() | leaked_soil,
        elevation_m=ELEVATION_M,
        neighbours=NEIGHBOURS,
        climatology=CLIMATOLOGY_MM,
    ) == build_features(
        issue_month=ISSUE_MONTH,
        precipitation=_precip_series(),
        soil_moisture=_soil_series(),
        elevation_m=ELEVATION_M,
        neighbours=NEIGHBOURS,
        climatology=CLIMATOLOGY_MM,
    )


@pytest.mark.parametrize("months", [0, -1])
def test_a_window_of_less_than_one_month_is_rejected_not_a_zero(months: int) -> None:
    # R3-001 (#238): an empty or inverted window is a caller bug, never a 0 mm claim.
    with pytest.raises(ValueError):
        precip_sum(_precip_series(), issue_month=ISSUE_MONTH, months=months)
    with pytest.raises(ValueError):
        precip_anomaly(_precip_series(), issue_month=ISSUE_MONTH, months=months, climatology={})


def test_a_nan_day_is_missing_evidence_like_none() -> None:
    # R3-002 (#238): NaN is pandas' missing marker in `ml/`; it must read as None here,
    # or training gets NaN where serving gets None (docs/08 "Paridad de features").
    series: dict[date, float] = _precip_series()
    series[date(2024, 10, 15)] = float("nan")
    assert precip_sum(series, issue_month=ISSUE_MONTH, months=1) is None
    assert precip_sum(series, issue_month=ISSUE_MONTH, months=2) is None
    # Negative: the same NaN outside the 1-month window of a September issue is unread.
    assert precip_sum(series, issue_month=date(2024, 10, 1), months=1) == 150.0
    climatology = monthly_climatology(series, years={2024})
    assert 10 not in climatology
    assert climatology[9] == 150.0


def test_a_window_crossing_the_year_boundary() -> None:
    # R3-003 (#238): issue month January 2025 -> the 6-month window is Jul..Dec 2024.
    # Rain: Jul 31x3=93, Aug 31x4=124, Sep 30x5=150, Oct 31x6=186, Nov 30x7=210,
    # Dec 31x8=248; total 1011. Climatology 10 mm per month -> anomaly 1011-60 = 951.
    rain = {7: 3.0, 8: 4.0, 9: 5.0, 10: 6.0, 11: 7.0, 12: 8.0}
    series = _series(date(2024, 7, 1), date(2024, 12, 31), lambda day: rain[day.month])
    january = date(2025, 1, 1)
    assert precip_sum(series, issue_month=january, months=6) == 1011.0
    assert precip_sum(series, issue_month=january, months=1) == 248.0
    climatology = {month: 10.0 for month in range(7, 13)}
    assert precip_anomaly(series, issue_month=january, months=6, climatology=climatology) == 951.0
    # Negative: without December in the climatology the January anomaly is unknown.
    del climatology[12]
    assert precip_anomaly(series, issue_month=january, months=1, climatology=climatology) is None
