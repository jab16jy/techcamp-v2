"""Daily rainfall and soil moisture from the Open-Meteo archive, at the seats.

`ARCHIVE_PARAMS` is the contract the serving client repeats: the T6a adapter
(`server/src/techcamp/risk/adapters/open_meteo_archive.py`) must issue the same
`models`, the same variable names, the same timezone and the same extra-day rule,
because docs/08 §Reglas de gobierno binds training and serving to one data source
("Paridad de features"). `models=era5` is pinned on purpose: `best_match` moves with
the catalogue and ERA5-Land has no precipitation in Open-Meteo (docs/08 §Fuentes).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

import pandas as pd

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
DAILY_VARIABLES = ("precipitation_sum", "soil_moisture_0_to_7cm_mean")
ARCHIVE_PARAMS: dict[str, str] = {
    "models": "era5",
    "daily": ",".join(DAILY_VARIABLES),
    "timezone": "America/Bogota",
}
WEATHER_START = date(2018, 7, 1)
"""Six-month windows (docs/08 §M2 "Features") before the first labelled month, 2019-01."""

EXTRA_DAYS_BEFORE = 1
"""ERA5 answers the first day of a window with nulls now and then, so the window
opens a day earlier and the dataset builder drops that day."""

COORDINATE_BATCH = 100
"""Coordinates per request.

The weight of a call does not depend on how many coordinates it carries, so small
batches cost the budget over and over: at 25 the whole range is 24 calls, at 100 it
is 6. 100 keeps a response in the megabytes instead of a couple of hundred."""
CHUNK_TEMPLATE = "archive_{index:03d}"
WINDOW_DAYS = 1460
"""The archive is downloaded in four-year windows.

The free tier weights a call by `variables x days`, independently of how many
coordinates it carries (measured 2026-10-02: five retries of one 25-coordinate,
eight-year request exhausted the hourly 5,000 budget on its own). A window of
1,460 days is 2,920 weight, under the 5,000/hour cap, and the whole 8-year range
is 6,026 weight, under the 10,000/day one."""


def archive_params(start: date, end: date) -> dict[str, str]:
    """The full query, including the extra day before `start`."""
    return {
        **ARCHIVE_PARAMS,
        "start_date": str(start - timedelta(days=EXTRA_DAYS_BEFORE)),
        "end_date": str(end),
    }


def weather_windows(
    start: date,
    end: date,
    *,
    window_days: int = WINDOW_DAYS,
) -> list[tuple[date, date]]:
    """Split the range into consecutive windows of at most `window_days` days."""
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=window_days - 1), end)
        windows.append((cursor, stop))
        cursor = stop + timedelta(days=1)
    return windows


def concat_windows(frames: Sequence[pd.DataFrame]) -> pd.DataFrame:
    """Join the parsed windows, dropping the day two of them share.

    Each window asks for one day before its start, so consecutive windows repeat the
    boundary day; keeping it twice would double count a day of rain.
    """
    if not frames:
        return pd.DataFrame(columns=["code", "date", *DAILY_VARIABLES])
    joined = pd.concat(frames, ignore_index=True)
    return joined.drop_duplicates(subset=["code", "date"], keep="first", ignore_index=True)


def last_complete_month(today: date) -> date:
    """The last day of the month before `today`: ERA5 lags about five days."""
    return today.replace(day=1) - timedelta(days=1)


def parse_archive(payload: bytes, codes: Sequence[str]) -> pd.DataFrame:
    """One row per code and day, in the order the coordinates were requested.

    A `null` day stays missing (NaN): docs/08 and the shared feature module read
    missing evidence as a third state, never as zero rain.
    """
    entries: list[dict[str, Any]] = json.loads(payload)
    if len(entries) != len(codes):
        raise ValueError(f"expected {len(codes)} archive locations, got {len(entries)}")
    rows: list[dict[str, Any]] = []
    for code, entry in zip(codes, entries, strict=True):
        daily = entry["daily"]
        for index, day in enumerate(daily["time"]):
            row: dict[str, Any] = {"code": code, "date": date.fromisoformat(day)}
            row.update({variable: daily[variable][index] for variable in DAILY_VARIABLES})
            rows.append(row)
    frame = pd.DataFrame(rows, columns=["code", "date", *DAILY_VARIABLES])
    # A real datetime column, so T4 can group by month and the parquet stays typed.
    frame["date"] = pd.to_datetime(frame["date"])
    return frame
