"""The two halves of a source: download the raw copies, then parse them.

`fetch` writes into `ml/.cache/raw` and is resumable (a chunk already in the cache is
never requested again); `parse` only reads that cache and writes
`ml/data/flood_m2/sources/*.parquet`. The CLI (`python -m techcamp_ml.sources`) is a
thin wrapper over these two functions.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from datetime import date
from pathlib import Path

import pandas as pd

from techcamp_ml.sources.cache import cached_files, read_raw, save_raw
from techcamp_ml.sources.elevation import (
    ELEVATION_URL,
    MAX_COORDINATES_PER_REQUEST,
    elevation_points,
    parse_elevation,
)
from techcamp_ml.sources.fetching import chunks, fetch
from techcamp_ml.sources.labels import (
    LABEL_SOURCES,
    SOCRATA_PAGE,
    SOCRATA_RESOURCE,
    Dropped,
    label_params,
    parse_labels,
)
from techcamp_ml.sources.layout import DEFAULT_LAYOUT, Layout, write_parquet
from techcamp_ml.sources.municipalities import (
    DIVIPOLA_RESOURCE,
    MGN_LAYER_URL,
    assert_region,
    cross_check_codes,
    divipola_params,
    mgn_params,
    parse_divipola,
    parse_mgn_codes,
)
from techcamp_ml.sources.weather import (
    ARCHIVE_URL,
    CHUNK_TEMPLATE,
    COORDINATE_BATCH,
    WEATHER_START,
    archive_params,
    concat_windows,
    last_complete_month,
    parse_archive,
    weather_windows,
)

SOURCE_NAMES = ("municipalities", "weather", "elevation", "labels")
NEEDS_MUNICIPALITIES = ("weather", "elevation")
DIVIPOLA_RAW = "divipola.json"
MGN_RAW = "mgn317.geojson"


def _requested(names: Sequence[str]) -> tuple[str, ...]:
    unknown = [name for name in names if name not in SOURCE_NAMES]
    if unknown:
        raise KeyError(f"unknown source {unknown[0]!r}; known: {', '.join(SOURCE_NAMES)}")
    return tuple(names)


def fetch_sources(
    names: Sequence[str] = SOURCE_NAMES,
    *,
    layout: Layout = DEFAULT_LAYOUT,
) -> dict[str, int]:
    """Download every raw copy the named sources need. Returns raw copies per source."""
    requested = _requested(names)
    counts: dict[str, int] = {}
    municipalities: pd.DataFrame | None = None
    if "municipalities" in requested or any(name in NEEDS_MUNICIPALITIES for name in requested):
        counts["municipalities"] = _fetch_municipalities(layout)
        municipalities = _parse_municipalities(layout)
    if "labels" in requested:
        counts["labels"] = _fetch_labels(layout)
    if "weather" in requested:
        assert municipalities is not None
        counts["weather"] = _fetch_weather(
            layout, municipalities, last_complete_month(date.today())
        )
    if "elevation" in requested:
        assert municipalities is not None
        counts["elevation"] = _fetch_elevation(layout, municipalities)
    return counts


def parse_sources(
    names: Sequence[str] = SOURCE_NAMES,
    *,
    layout: Layout = DEFAULT_LAYOUT,
) -> dict[str, Path]:
    """Build the parquets of the named sources from the raw cache alone."""
    requested = _requested(names)
    municipalities = _parse_municipalities(layout)
    codes = set(municipalities["code"])
    written = {"municipalities": write_parquet(layout, "municipalities", municipalities)}
    if "labels" in requested:
        written["labels"] = _parse_labels(layout, codes)
    if "weather" in requested:
        written["weather"] = _parse_weather(layout, municipalities)
    if "elevation" in requested:
        written["elevation"] = _parse_elevation(layout)
    return written


def summarise(layout: Layout = DEFAULT_LAYOUT) -> pd.DataFrame:
    """Row count per built parquet, and per year and department for the labels."""
    rows: list[dict[str, object]] = []
    for name in SOURCE_NAMES:
        path = layout.data / f"{name}.parquet"
        if not path.exists():
            continue
        frame = pd.read_parquet(path)
        rows.append({"source": name, "rows": len(frame), "from": _first(frame), "to": _last(frame)})
    counts = pd.DataFrame(rows)
    return counts


def _first(frame: pd.DataFrame) -> object:
    return frame["date"].min() if "date" in frame.columns else ""


def _last(frame: pd.DataFrame) -> object:
    return frame["date"].max() if "date" in frame.columns else ""


def _parse_municipalities(layout: Layout) -> pd.DataFrame:
    frame = parse_divipola(read_raw(layout, "municipalities", DIVIPOLA_RAW))
    cross_check_codes(frame, parse_mgn_codes(read_raw(layout, "municipalities", MGN_RAW)))
    assert_region(frame)
    return frame


def _fetch_municipalities(layout: Layout) -> int:
    written = 0
    if not (layout.raw_copy("municipalities", DIVIPOLA_RAW)).exists():
        fetch(
            layout,
            "municipalities",
            DIVIPOLA_RAW,
            DIVIPOLA_RESOURCE,
            params=divipola_params(),
        )
        written += 1
    # MGN answers `geometry: null` on this layer, so it is the code control, not a
    # geometry source (owner decision D-T3.1).
    if not (layout.raw_copy("municipalities", MGN_RAW)).exists():
        fetch(layout, "municipalities", MGN_RAW, MGN_LAYER_URL, params=mgn_params())
        written += 1
    return written


def _fetch_labels(layout: Layout) -> int:
    written = 0
    for source in LABEL_SOURCES:
        url = f"{SOCRATA_RESOURCE}/{source.dataset}.json"
        offset = 0
        while True:
            name = f"{source.dataset}.p{offset // SOCRATA_PAGE:03d}.json"
            if (layout.raw_copy("labels", name)).exists():
                break
            payload = fetch(layout, "labels", name, url, params=label_params(source, offset=offset))
            written += 1
            if len(json.loads(payload)) < SOCRATA_PAGE:
                break
            offset += SOCRATA_PAGE
    return written


def _parse_labels(layout: Layout, codes: Collection[str]) -> Path:
    frames: list[pd.DataFrame] = []
    dropped = Dropped()
    for source in LABEL_SOURCES:
        pages = [
            name
            for name in cached_files(layout, "labels")
            if name.startswith(f"{source.dataset}.p") and name.endswith(".json")
        ]
        for page in pages:
            frame, page_dropped = parse_labels(read_raw(layout, "labels", page), source, codes)
            frames.append(frame)
            dropped += page_dropped
    labels = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    labels = labels.sort_values(["code", "date"], ignore_index=True)
    report = {
        "rows": int(len(labels)),
        "dropped": {
            "other_event": dropped.other_event,
            "unknown_code": dropped.unknown_code,
            "bad_date": dropped.bad_date,
        },
    }
    path = write_parquet(layout, "labels", labels)
    path.with_suffix(".drops.json").write_text(json.dumps(report, indent=2) + "\n")
    return path


def _fetch_weather(layout: Layout, municipalities: pd.DataFrame, end: date) -> int:
    zipped = zip(municipalities["code"], municipalities["lat"], municipalities["lon"], strict=True)
    points = [(str(code), float(lat), float(lon)) for code, lat, lon in zipped]
    written = 0
    index = 0
    for start, stop in weather_windows(WEATHER_START, end):
        for batch in chunks(points, COORDINATE_BATCH):
            name = CHUNK_TEMPLATE.format(index=index)
            index += 1
            if (layout.raw_copy("weather", f"{name}.json")).exists():
                continue  # an interrupted fetch resumes here
            params = archive_params(start, stop)
            query = "&".join(f"{key}={value}" for key, value in params.items())
            latitude = ",".join(str(lat) for _, lat, _ in batch)
            longitude = ",".join(str(lon) for _, _, lon in batch)
            fetch(
                layout,
                "weather",
                f"{name}.json",
                ARCHIVE_URL,
                params={**params, "latitude": latitude, "longitude": longitude},
                weight=len(batch),
            )
            # The archive answers in request order, so the chunk's window and codes
            # travel with it and the parser never has to guess the request.
            sidecar = {
                "start_date": str(start),
                "end_date": str(stop),
                "codes": [code for code, _, _ in batch],
            }
            save_raw(
                layout,
                "weather",
                f"{name}.request.json",
                f"{ARCHIVE_URL}?{query}&latitude={latitude}&longitude={longitude}",
                json.dumps(sidecar).encode(),
            )
            written += 1
    return written


def _parse_weather(layout: Layout, municipalities: pd.DataFrame) -> Path:
    names = cached_files(layout, "weather")
    frames = []
    for name in names:
        if not name.endswith(".json") or not name.startswith("archive_"):
            continue
        if name.endswith(".request.json"):
            continue
        request = json.loads(read_raw(layout, "weather", f"{name[:-5]}.request.json"))
        frames.append(parse_archive(read_raw(layout, "weather", name), request["codes"]))
    weather = concat_windows(frames)
    if not weather.empty:
        unknown = sorted(set(weather["code"]) - set(municipalities["code"]))
        if unknown:
            raise ValueError(f"archive chunks hold codes outside the region: {unknown}")
    return write_parquet(layout, "weather", weather)


def _fetch_elevation(layout: Layout, municipalities: pd.DataFrame) -> int:
    points = elevation_points(municipalities)
    written = 0
    for index, batch in enumerate(chunks(points, MAX_COORDINATES_PER_REQUEST)):
        name = f"elevation_{index:03d}"
        if (layout.raw_copy("elevation", f"{name}.json")).exists():
            continue
        params = {
            "latitude": ",".join(str(point[1]) for point in batch),
            "longitude": ",".join(str(point[2]) for point in batch),
        }
        url = ELEVATION_URL + "?" + "&".join(f"{key}={value}" for key, value in params.items())
        fetch(
            layout,
            "elevation",
            f"{name}.json",
            ELEVATION_URL,
            params=params,
            weight=len(batch),
        )
        save_raw(layout, "elevation", f"{name}.points.json", url, json.dumps(batch).encode())
        written += 1
    return written


def _parse_elevation(layout: Layout) -> Path:
    frames = [
        parse_elevation(
            read_raw(layout, "elevation", name),
            json.loads(read_raw(layout, "elevation", name.replace(".json", ".points.json"))),
        )
        for name in sorted(cached_files(layout, "elevation"))
        if name.startswith("elevation_")
        and name.endswith(".json")
        and not name.endswith(".points.json")
    ]
    return write_parquet(layout, "elevation", pd.concat(frames, ignore_index=True))


__all__ = [
    "SOURCE_NAMES",
    "fetch_sources",
    "parse_sources",
    "summarise",
]
