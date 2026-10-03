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

ERA5_LAG_DAYS = 5
"""ERA5 arrives with about five days of lag (docs/08 §Fuentes de datos de M2: 'ERA5
llega con ~5 días de retraso'), so the newest days a request can still miss are not
in the dataset."""

MAX_COORDINATES_PER_REQUEST = 100
"""The per-request cap on locations the API enforces (docs/08 §Fuentes de datos de M2
gives 100 for the elevation endpoint; the archive enforces one too, adjustable per
deployment). The downloader never puts more than this in a single call."""

COORDINATE_BATCH = MAX_COORDINATES_PER_REQUEST
CHUNK_TEMPLATE = "archive_{index:03d}"
WINDOW_DAYS = 1460
"""The archive is downloaded in windows of four years.

Open-Meteo's free tier weighs a query by its variables, locations and domains
(docs, "Rate Limiting": 10,000/day per IP, with minute and hourly buckets on top),
and the archive weighs it further by the length of the requested range. A window
bounds that range per request and keeps each response in the megabytes; how many
requests the whole 8-year download takes is a quota question, not a correctness one,
which is why the fetch resumes from the cache instead of trying to be clever."""


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
    """The last day of the last month ERA5 has fully published as of `today`.

    ERA5 lags about five days, so the month before `today` is not necessarily
    complete: on 2026-10-02 the last complete month is still August.
    """
    return (today - timedelta(days=ERA5_LAG_DAYS)).replace(day=1) - timedelta(days=1)


def parse_archive(payload: bytes, codes: Sequence[str]) -> pd.DataFrame:
    """One row per code and day, in the order the coordinates were requested.

    A `null` day stays missing (NaN): docs/08 and the shared feature module read
    missing evidence as a third state, never as zero rain.
    """
    entries = json.loads(payload)
    # One location comes back as an object, several as a list (verified 2026-10-02).
    if isinstance(entries, dict):
        entries = [entries]
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
