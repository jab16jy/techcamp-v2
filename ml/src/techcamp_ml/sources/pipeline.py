"""The two halves of a source: download the raw copies, then parse them.

`fetch` writes into `ml/.cache/raw` and is resumable (a chunk already in the cache is
never requested again); `parse` only reads that cache and writes
`ml/data/flood_m2/sources/*.parquet`. The CLI (`python -m techcamp_ml.sources`) is a
thin wrapper over these two functions.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import cycle
from pathlib import Path
from typing import Any

import pandas as pd

from techcamp_ml.sources.cache import cached_files, manifest_names, read_raw, save_raw
from techcamp_ml.sources.elevation import (
    ELEVATION_URL,
    MAX_COORDINATES_PER_REQUEST,
    POINT,
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
PLAN_RAW = "archive_plan.json"
"""The range the last weather fetch committed to, cached before its first chunk.

The parse reads it instead of the wall clock: the dataset is built only from the cached
copies (docs/08 §Reglas de gobierno), so the same cache gives the same parquet on any
day, and a cache without a plan says so instead of guessing a range."""


def _chunk_ready(
    layout: Layout,
    source: str,
    name: str,
    sidecar: str,
    proven: set[str],
) -> bool:
    """A chunk counts as downloaded only when the manifest documents both of its files.

    The sidecar is what tells the parser which coordinates and window the response
    answers, so a response without it is an interrupted chunk, not a finished one; a
    payload the manifest never documented is a chunk nobody can trace back to a host.
    """
    return (
        {name, sidecar} <= proven
        and layout.raw_copy(source, name).exists()
        and layout.raw_copy(source, sidecar).exists()
    )


def _window_of(layout: Layout, name: str) -> tuple[str, str] | None:
    """The window an archive chunk states in its sidecar, `None` when it has none."""
    path = layout.raw_copy("weather", f"{name}.request.json")
    if not path.exists():
        return None
    sidecar = json.loads(path.read_bytes())
    return str(sidecar["start_date"]), str(sidecar["end_date"])


def _codes_of(layout: Layout, name: str) -> tuple[str, ...] | None:
    """The coordinates an archive chunk answers, `None` when it has no sidecar."""
    path = layout.raw_copy("weather", f"{name}.request.json")
    if not path.exists():
        return None
    return tuple(str(code) for code in json.loads(path.read_bytes())["codes"])


@dataclass(frozen=True, slots=True)
class PlannedChunk:
    """One archive chunk a fetch committed to download for one day."""

    name: str
    start: date
    end: date
    codes: tuple[str, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "start_date": str(self.start),
            "end_date": str(self.end),
            "codes": list(self.codes),
        }

    @classmethod
    def from_json(cls, saved: dict[str, Any]) -> PlannedChunk:
        return cls(
            str(saved["name"]),
            date.fromisoformat(str(saved["start_date"])),
            date.fromisoformat(str(saved["end_date"])),
            tuple(str(code) for code in saved["codes"]),
        )


def _archive_plan(batches: Sequence[Sequence[POINT]], today: date) -> list[PlannedChunk]:
    """Every chunk the range owes on `today`, in fetch order.

    A pure function of the day it is given: the windows stop at the last month ERA5
    published whole, so they move with `today` and with nothing else (docs/08
    §Fuentes de datos de M2, "ERA5 llega con ~5 días de retraso").
    """
    plan: list[PlannedChunk] = []
    index = 0
    for start, stop in weather_windows(WEATHER_START, last_complete_month(today)):
        for batch in batches:
            plan.append(
                PlannedChunk(
                    CHUNK_TEMPLATE.format(index=index),
                    start,
                    stop,
                    tuple(str(code) for code, _, _ in batch),
                )
            )
            index += 1
    return plan


def _requested(names: Sequence[str]) -> tuple[str, ...]:
    unknown = [name for name in names if name not in SOURCE_NAMES]
    if unknown:
        raise KeyError(f"unknown source {unknown[0]!r}; known: {', '.join(SOURCE_NAMES)}")
    return tuple(names)


def fetch_sources(
    names: Sequence[str] = SOURCE_NAMES,
    *,
    layout: Layout = DEFAULT_LAYOUT,
    today: date | None = None,
) -> dict[str, int]:
    """Download every raw copy the named sources need. Returns raw copies per source.

    `today` is the day the download is planned for: the archive windows stop at the last
    month ERA5 published whole, so it decides which chunks are owed. It defaults to the
    wall clock here and nowhere else.
    """
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
        counts["weather"] = _fetch_weather(layout, municipalities, today or date.today())
    if "elevation" in requested:
        assert municipalities is not None
        counts["elevation"] = _fetch_elevation(layout, municipalities)
    return counts


def parse_sources(
    names: Sequence[str] = SOURCE_NAMES,
    *,
    layout: Layout = DEFAULT_LAYOUT,
) -> dict[str, Path]:
    """Build the parquets of the named sources from the raw cache alone.

    It takes no day: the weather range is the one the fetch planned and cached, so the
    same cache gives the same parquet whatever day the parse runs on (docs/08
    §Reglas de gobierno: "el dataset se arma solo desde esas copias").
    """
    requested = _requested(names)
    municipalities = _parse_municipalities(layout)
    codes = set(municipalities["code"])
    written = {"municipalities": write_parquet(layout, "municipalities", municipalities)}
    if "labels" in requested:
        written["labels"] = _parse_labels(layout, codes)
    if "weather" in requested:
        written["weather"] = _parse_weather(layout, municipalities)
    if "elevation" in requested:
        written["elevation"] = _parse_elevation(layout, municipalities)
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
    proven = manifest_names(layout, "municipalities")
    if not _downloaded(layout, "municipalities", DIVIPOLA_RAW, proven):
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
    if not _downloaded(layout, "municipalities", MGN_RAW, proven):
        fetch(layout, "municipalities", MGN_RAW, MGN_LAYER_URL, params=mgn_params())
        written += 1
    return written


def _downloaded(layout: Layout, source: str, name: str, proven: set[str]) -> bool:
    """Whether the cache holds this raw copy *and* the manifest documents it."""
    return name in proven and layout.raw_copy(source, name).exists()


def _fetch_labels(layout: Layout) -> int:
    written = 0
    proven = manifest_names(layout, "labels")
    for source in LABEL_SOURCES:
        url = f"{SOCRATA_RESOURCE}/{source.dataset}.json"
        offset = 0
        while True:
            name = f"{source.dataset}.p{offset // SOCRATA_PAGE:03d}.json"
            cached = _downloaded(layout, "labels", name, proven)
            # Resume keeps walking the pages, it does not stop at the first one it has:
            # a cache holding page 0 alone still owes every page after it.
            payload = (
                read_raw(layout, "labels", name)
                if cached
                else fetch(layout, "labels", name, url, params=label_params(source, offset=offset))
            )
            written += 0 if cached else 1
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
    if not frames:
        # An empty frame has no columns, and sorting it by `code` would answer a
        # KeyError instead of naming the step that was never run.
        raise ValueError("the raw cache holds no label page; run the fetch step and parse again")
    labels = pd.concat(frames, ignore_index=True)
    labels = labels.sort_values(["code", "date"], ignore_index=True)
    report = {
        "rows": int(len(labels)),
        "dropped": {
            "other_event": dropped.other_event,
            "unknown_code": dropped.unknown_code,
            "bad_date": dropped.bad_date,
            "outside_window": dropped.outside_window,
        },
    }
    path = write_parquet(layout, "labels", labels)
    path.with_suffix(".drops.json").write_text(json.dumps(report, indent=2) + "\n")
    return path


def _fetch_weather(layout: Layout, municipalities: pd.DataFrame, today: date) -> int:
    zipped = zip(municipalities["code"], municipalities["lat"], municipalities["lon"], strict=True)
    points = [(str(code), float(lat), float(lon)) for code, lat, lon in zipped]
    batches = list(chunks(points, COORDINATE_BATCH))
    plan = _archive_plan(batches, today)
    # The plan lands in the cache before the first chunk: the parse builds the range this
    # fetch committed to, so the same cache gives the same parquet on any day (docs/08
    # §Reglas de gobierno: "el dataset se arma solo desde esas copias").
    save_raw(
        layout,
        "weather",
        PLAN_RAW,
        f"{ARCHIVE_URL}?plan_for={today}",
        json.dumps({"today": str(today), "chunks": [chunk.as_json() for chunk in plan]}).encode(),
    )
    written = 0
    proven = manifest_names(layout, "weather")
    # The plan fixes the order and the window; every window asks the same batches again,
    # so the batches cycle under the planned chunks.
    for planned, batch in zip(plan, cycle(batches), strict=False):
        # A chunk is finished only when its sidecar states the window the plan gave it:
        # the same file names answer an older window once the months move on.
        if _window_of(layout, planned.name) == (str(planned.start), str(planned.end)) and (
            _chunk_ready(
                layout,
                "weather",
                f"{planned.name}.json",
                f"{planned.name}.request.json",
                proven,
            )
        ):
            continue  # an interrupted fetch resumes here
        params = archive_params(planned.start, planned.end)
        query = "&".join(f"{key}={value}" for key, value in params.items())
        latitude = ",".join(str(lat) for _, lat, _ in batch)
        longitude = ",".join(str(lon) for _, _, lon in batch)
        fetch(
            layout,
            "weather",
            f"{planned.name}.json",
            ARCHIVE_URL,
            params={**params, "latitude": latitude, "longitude": longitude},
            weight=len(batch),
        )
        # The archive answers in request order, so the chunk's window and codes
        # travel with it and the parser never has to guess the request.
        sidecar = {
            "start_date": str(planned.start),
            "end_date": str(planned.end),
            "codes": list(planned.codes),
        }
        save_raw(
            layout,
            "weather",
            f"{planned.name}.request.json",
            f"{ARCHIVE_URL}?{query}&latitude={latitude}&longitude={longitude}",
            json.dumps(sidecar).encode(),
        )
        written += 1
    return written


def _read_plan(layout: Layout) -> list[PlannedChunk]:
    """The range the last fetch committed to, or a refusal naming the step to run."""
    if not layout.raw_copy("weather", PLAN_RAW).exists():
        raise ValueError(
            f"the raw cache holds no archive plan ({PLAN_RAW}): the parse builds the range "
            "the fetch planned, so run the fetch step and parse again"
        )
    saved = json.loads(read_raw(layout, "weather", PLAN_RAW))
    return [PlannedChunk.from_json(chunk) for chunk in saved["chunks"]]


def _parse_weather(
    layout: Layout,
    municipalities: pd.DataFrame,
) -> Path:
    plan = _read_plan(layout)
    _assert_plan_cached(layout, plan)
    frames = [
        parse_archive(
            read_raw(layout, "weather", f"{planned.name}.json"),
            list(planned.codes),
        )
        for planned in plan
    ]
    weather = concat_windows(frames)
    if not weather.empty:
        unknown = sorted(set(weather["code"]) - set(municipalities["code"]))
        if unknown:
            raise ValueError(f"archive chunks hold codes outside the region: {unknown}")
    return write_parquet(layout, "weather", weather)


def _assert_plan_cached(layout: Layout, plan: Sequence[PlannedChunk]) -> None:
    """Every planned chunk has to be cached, with the window and codes the plan gave it.

    A chunk fetched for an older window, or for other coordinates, would build a series
    that is not the one the fetch committed to, and a partial cache would build a series
    that stops short of what the parquet's name implies.
    """
    missing: list[str] = []
    stale: list[str] = []
    for planned in plan:
        if not layout.raw_copy("weather", f"{planned.name}.json").exists():
            missing.append(f"{planned.name}.json")
        elif _window_of(layout, planned.name) != (str(planned.start), str(planned.end)):
            stale.append(f"{planned.name}.json (another window)")
        elif _codes_of(layout, planned.name) != planned.codes:
            stale.append(f"{planned.name}.json (other codes)")
    if missing or stale:
        raise ValueError(
            f"the archive download does not answer the plan in {PLAN_RAW}: "
            f"missing chunks: {missing}; chunks downloaded for another window or codes: {stale}; "
            "run the fetch step and parse again"
        )


def _fetch_elevation(layout: Layout, municipalities: pd.DataFrame) -> int:
    points = elevation_points(municipalities)
    written = 0
    proven = manifest_names(layout, "elevation")
    for index, batch in enumerate(chunks(points, MAX_COORDINATES_PER_REQUEST)):
        name = f"elevation_{index:03d}"
        if _chunk_ready(layout, "elevation", f"{name}.json", f"{name}.points.json", proven):
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


def _parse_elevation(layout: Layout, municipalities: pd.DataFrame) -> Path:
    """The slope table, or a refusal: every municipality of the region has to be in it.

    A missing chunk leaves the seat (and its four neighbours) out of the table, and T4
    would take a short frame for the whole region.
    """
    names = [
        name
        for name in sorted(cached_files(layout, "elevation"))
        if name.startswith("elevation_")
        and name.endswith(".json")
        and not name.endswith(".points.json")
    ]
    if not names:
        raise ValueError(
            "the raw cache holds no elevation chunk; run the fetch step and parse again"
        )
    frame = pd.concat(
        [
            parse_elevation(
                read_raw(layout, "elevation", name),
                json.loads(read_raw(layout, "elevation", name.replace(".json", ".points.json"))),
            )
            for name in names
        ],
        ignore_index=True,
    )
    missing = sorted(set(municipalities["code"]) - set(frame["code"]))
    if missing:
        raise ValueError(
            f"the elevation download does not cover {len(missing)} municipalities of the "
            f"region, among them {missing[:5]}; run the fetch step and parse again"
        )
    return write_parquet(layout, "elevation", frame)


__all__ = [
    "SOURCE_NAMES",
    "fetch_sources",
    "parse_sources",
    "summarise",
]
