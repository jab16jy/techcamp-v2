"""Two municipalities, ten months of daily weather and one flood month.

Small enough to hand-compute a window by hand and to hold the whole table in a test: the
real region is 195 municipalities and the real archive never finished downloading, so the
builder is proved on fixtures and the real build waits for the ERA5 quota (data card,
"Qué sigue").
"""

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from techcamp_ml.sources.layout import Layout

WEATHER_FIRST = date(2018, 7, 1)
"""Six months before the first labelled month, the way the real archive starts (docs/08
§M2 "Features")."""
WEATHER_LAST = date(2019, 4, 30)
"""One month past the last labelled one: the extra month proves that a month nobody
labelled is not counted as a negative."""
MISSING_DAY = date(2019, 1, 15)
"""A day ERA5 answers nothing for 08001: the windows that read it are missing evidence."""
FLOOD_MONTH = (2019, 3)
"""The one labelled municipality-month, reported twice."""

MUNICIPALITIES = [
    {
        "code": "08001",
        "name": "Barranquilla",
        "department_code": "08",
        "department_name": "Atlántico",
        "lat": 10.9685,
        "lon": -74.7813,
    },
    {
        "code": "13001",
        "name": "Cartagena",
        "department_code": "13",
        "department_name": "Bolívar",
        "lat": 10.3910,
        "lon": -75.4794,
    },
]

TERRAIN = [
    # Flat around Barranquilla: four neighbours at the centre's own height.
    {
        "code": "08001",
        "lat": 10.9685,
        "lon": -74.7813,
        "elevation_m": 10.0,
        "east_m": 10.0,
        "west_m": 10.0,
        "north_m": 10.0,
        "south_m": 10.0,
        "spacing_m": 1000.0,
    },
    # A 100 m rise to the east over 2 km: `atan(0.05)` in degrees.
    {
        "code": "13001",
        "lat": 10.3910,
        "lon": -75.4794,
        "elevation_m": 20.0,
        "east_m": 110.0,
        "west_m": 10.0,
        "north_m": 20.0,
        "south_m": 20.0,
        "spacing_m": 1000.0,
    },
]


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=step) for step in range((last - first).days + 1)]


def municipalities() -> pd.DataFrame:
    return pd.DataFrame(MUNICIPALITIES)


def weather() -> pd.DataFrame:
    """2 mm of rain and 0.3 m³/m³ of soil moisture a day, with two exceptions.

    `08001` misses one January day, and `13001` gets 100 mm a day through March: the rain
    of the month being predicted, which must not reach that month's own features.
    """
    rows: list[dict[str, object]] = []
    for code in ("08001", "13001"):
        for day in _days(WEATHER_FIRST, WEATHER_LAST):
            missing = code == "08001" and day == MISSING_DAY
            rainy_month = code == "13001" and (day.year, day.month) == FLOOD_MONTH
            rows.append(
                {
                    "code": code,
                    "date": pd.Timestamp(day),
                    "precipitation_sum": float("nan")
                    if missing
                    else (100.0 if rainy_month else 2.0),
                    "soil_moisture_0_to_7cm_mean": float("nan") if missing else 0.3,
                }
            )
    return pd.DataFrame(rows)


def elevation() -> pd.DataFrame:
    return pd.DataFrame(TERRAIN)


def labels() -> pd.DataFrame:
    """Two reports in the same municipality-month, as the UNGRD consolidates really do."""
    return pd.DataFrame(
        [
            {
                "code": "08001",
                "date": pd.Timestamp(date(2019, 3, 10)),
                "event_class": "INUNDACION",
                "source_dataset": "wwkg-r6te",
                "source_row_id": "row-1",
            },
            {
                "code": "08001",
                "date": pd.Timestamp(date(2019, 3, 20)),
                "event_class": "CRECIENTE SUBITA",
                "source_dataset": "wwkg-r6te",
                "source_row_id": "row-2",
            },
        ]
    )


@pytest.fixture
def municipalities_frame() -> pd.DataFrame:
    return municipalities()


@pytest.fixture
def weather_frame() -> pd.DataFrame:
    return weather()


@pytest.fixture
def elevation_frame() -> pd.DataFrame:
    return elevation()


@pytest.fixture
def labels_frame() -> pd.DataFrame:
    return labels()


@pytest.fixture
def sources(tmp_path: Path) -> Layout:
    """A layout whose four source parquets hold the two municipalities above.

    The build reads the parquets the parse step writes and nothing else, so this is the
    whole world it sees.
    """
    layout = Layout(tmp_path)
    layout.data.mkdir(parents=True, exist_ok=True)
    municipalities().to_parquet(layout.data / "municipalities.parquet", index=False)
    weather().to_parquet(layout.data / "weather.parquet", index=False)
    elevation().to_parquet(layout.data / "elevation.parquet", index=False)
    labels().to_parquet(layout.data / "labels.parquet", index=False)
    return layout
