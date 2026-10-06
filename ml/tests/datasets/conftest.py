"""The region of docs/08 §M2, two municipalities with weather, one flood month.

195 municipalities so the region guard is the real one, but a daily series for only two of
them: that is the state the real cache is in (the data card records 95 municipalities whose
archive never passed 2022-06-29), and it is what keeps this fast enough to be a unit test —
a window over a day the archive never answered stops at that day, so 193 unanswered
municipalities cost a lookup each instead of six months of them.
"""

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from techcamp_ml.sources import pipeline
from techcamp_ml.sources.cache import save_raw
from techcamp_ml.sources.layout import Layout
from techcamp_ml.sources.pipeline import write_plan_trace

DEPARTMENT_SIZES = {"08": 23, "13": 46, "20": 25, "23": 30, "44": 15, "47": 30, "70": 26}
"""The Caribbean continental departments of docs/08 §M2 "Región" with the counts the data
card records: 195 municipalities, San Andrés out."""
ANSWERED = ("08001", "13001")
"""The only two the archive answered: Barranquilla and Cartagena, the first code of their
department."""
NAMED = {
    "08001": ("Barranquilla", "Atlántico", 10.9685, -74.7813),
    "13001": ("Cartagena", "Bolívar", 10.3910, -75.4794),
}

WEATHER_FIRST = date(2018, 7, 1)
"""Six months before the first labelled month, the way the real archive starts (docs/08
§M2 "Features")."""
WEATHER_LAST = date(2026, 2, 28)
"""Past the labelled window of docs/08 (2019-2025): the extra months prove that weather
the owner never labelled is not a row either."""
MISSING_DAY = date(2019, 1, 15)
"""A day ERA5 answers nothing for 08001: the windows that read it are missing evidence."""
FLOOD_MONTH = (2019, 3)
"""The one labelled municipality-month, reported twice."""

FLAT = {"elevation_m": 10.0, "east_m": 10.0, "west_m": 10.0, "north_m": 10.0, "south_m": 10.0}
RISING = {"elevation_m": 20.0, "east_m": 110.0, "west_m": 10.0, "north_m": 20.0, "south_m": 20.0}
"""Cartagena rises 100 m to the east over 2 km; everyone else is flat at 10 m."""


def codes() -> list[str]:
    """The 195 codes of the region, in DIVIPOLA order."""
    return [
        f"{department}{index:03d}"
        for department, size in DEPARTMENT_SIZES.items()
        for index in range(1, size + 1)
    ]


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=step) for step in range((last - first).days + 1)]


def municipalities() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "code": code,
                "name": NAMED[code][0] if code in NAMED else f"Municipio {index}",
                "department_code": code[:2],
                "department_name": NAMED[code][1] if code in NAMED else f"Departamento {code[:2]}",
                "lat": NAMED[code][2] if code in NAMED else 0.0,
                "lon": NAMED[code][3] if code in NAMED else 0.0,
            }
            for index, code in enumerate(codes(), start=1)
        ]
    )


def weather() -> pd.DataFrame:
    """2 mm of rain and 0.3 m³/m³ of soil moisture a day for the two answered seats.

    `08001` misses one January day, and `13001` gets 100 mm a day through March 2019: the
    rain of the month being predicted, which must not reach that month's own features.
    """
    rows: list[dict[str, object]] = []
    for code in ANSWERED:
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
    return pd.DataFrame(
        [
            {
                "code": code,
                "lat": 1.0,
                "lon": -1.0,
                "spacing_m": 1000.0,
                **(RISING if code == "13001" else FLAT),
            }
            for code in codes()
        ]
    )


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
def region_codes() -> list[str]:
    """The 195 DIVIPOLA codes of the region, in order."""
    return codes()


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
    """A layout whose four source parquets hold the region above.

    The build reads the parquets the parse step writes and nothing else, so this is the
    whole world it sees. The two parquets a plan governs carry the digest of that plan,
    written by the same code the parse step uses.
    """
    layout = Layout(tmp_path)
    layout.data.mkdir(parents=True, exist_ok=True)
    municipalities().to_parquet(layout.data / "municipalities.parquet", index=False)
    weather().to_parquet(layout.data / "weather.parquet", index=False)
    elevation().to_parquet(layout.data / "elevation.parquet", index=False)
    labels().to_parquet(layout.data / "labels.parquet", index=False)
    save_raw(
        layout,
        "weather",
        pipeline.PLAN_RAW,
        "test",
        json.dumps({"today": "2026-10-02", "chunks": []}).encode(),
    )
    write_plan_trace(layout, "weather", pipeline.PLAN_RAW)
    save_raw(
        layout,
        "labels",
        pipeline.LABELS_PLAN_RAW,
        "test",
        json.dumps({"complete": True, "pages": ["wwkg-r6te.p000.json"]}).encode(),
    )
    write_plan_trace(layout, "labels", pipeline.LABELS_PLAN_RAW)
    return layout
