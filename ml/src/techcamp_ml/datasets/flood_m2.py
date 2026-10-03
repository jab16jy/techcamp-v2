"""The M2 dataset: one row per municipality and month, with the shared features.

docs/08 §M2 "Unidad" (municipio × mes), "Horizonte" (the features of month M read data
through the last day of M-1), "Etiqueta" (1 when a UNGRD flood report falls in that
municipality and month) and "Negativos" (**every** municipality-month without one, no
subsampling anywhere).

The features come from `techcamp.risk.domain.features`, the module the serving job
imports too: one implementation of the feature contract on both sides (docs/08
§Reglas de gobierno, "Paridad de features"). Nothing here recomputes a window, a slope or
a season.

The three anomaly columns are stored as null. An anomaly is an accumulation minus the
climatology of **train** (docs/08 §M2 "Features") and the split belongs to the harness
task, so a climatology computed here would be this table's own: the experiment step fills
those columns from its train years. Null is the honest state meanwhile — a zero anomaly
would be a claim about the weather (docs/08, "Cero no es evidencia faltante").
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from calendar import monthrange
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from techcamp.risk.domain.features import (
    FEATURE_NAMES,
    DailySeries,
    Neighbours,
    build_features,
)

from techcamp_ml.sources.elevation import NEIGHBOUR_DIRECTIONS
from techcamp_ml.sources.labels import LABEL_SOURCES, LabelSource
from techcamp_ml.sources.layout import DEFAULT_LAYOUT, ML_ROOT, Layout

DATASET_NAME = "flood_m2"
PARQUET_NAME = f"{DATASET_NAME}.parquet"
MANIFEST_NAME = "manifest.json"
SOURCE_NAMES = ("municipalities", "weather", "elevation", "labels")
"""The four parquets of the parse step, in the order the table is built from them."""

IDENTITY_COLUMNS = (
    "code",
    "department_code",
    "department_name",
    "year",
    "month",
    "horizon_start",
    "horizon_days",
)
"""What identifies the row: the municipality, the month being predicted, and the horizon
docs/08 §M2 "Horizonte" names."""

LABEL_COLUMN = "label"
COLUMNS = (*IDENTITY_COLUMNS, *FEATURE_NAMES, LABEL_COLUMN)
"""The column contract, in order: identity, the shared features as `FEATURE_NAMES` spells
them, then the label. T5 wires the harness against this list."""


@dataclass(frozen=True, slots=True)
class Terrain:
    """A municipality's own elevation and its slope input, either of which may be absent."""

    elevation_m: float | None
    neighbours: Neighbours | None


def label_coverage(
    labels: pd.DataFrame,
    *,
    sources: Sequence[LabelSource] = LABEL_SOURCES,
) -> tuple[date, date]:
    """First and last month whose label is known, in evidence rather than in a guess.

    The first is January of the earliest window a label source declares: the download
    claims those years (docs/08 §Fuentes de datos de M2), so a month inside them with no
    report is a negative. The last is the month of the newest report in the region: the
    source that owns the open window had published nothing past it, and a month nobody
    reported on is unknown, not a negative (data card, sesgo 3).

    A month outside this range is left out of the table. Adding it as a `0` would train
    the model on months whose silence nobody vouched for.
    """
    if labels.empty:
        raise ValueError(
            "the labels table holds no flood report: run the parse step for labels and build again"
        )
    newest = pd.Timestamp(labels["date"].max())
    return (
        date(min(source.year_from for source in sources), 1, 1),
        date(newest.year, newest.month, 1),
    )


def build_table(
    municipalities: pd.DataFrame,
    weather: pd.DataFrame,
    elevation: pd.DataFrame,
    labels: pd.DataFrame,
) -> pd.DataFrame:
    """The municipality × month table of docs/08 §M2, sorted by code and month.

    The four parquets T3 built are its only inputs, so the table is reproducible from the
    cached copies alone (docs/08 §Reglas de gobierno). A municipality whose series the
    archive never answered keeps its row with null features: the month is known, only its
    evidence is missing, and dropping it would resample the region by download luck.
    """
    first, last = label_coverage(labels)
    terrain = _terrain(elevation)
    series = _series_by_code(weather)
    labelled = _labelled_months(labels)
    without_terrain = sorted(set(municipalities["code"]) - set(terrain))
    if without_terrain:
        raise ValueError(
            f"the elevation table has no row for {len(without_terrain)} municipalities of the "
            f"region: {without_terrain[:5]}; run the parse step for elevation and build again"
        )

    rows: list[dict[str, Any]] = []
    for record in municipalities.sort_values("code").itertuples():
        code = str(record.code)
        ground = terrain[code]
        precipitation, soil_moisture = series.get(code, ({}, {}))
        for issue_month in _months(first, last):
            rows.append(
                {
                    "code": code,
                    "department_code": str(record.department_code),
                    "department_name": str(record.department_name),
                    "year": issue_month.year,
                    "month": issue_month.month,
                    "horizon_start": pd.Timestamp(issue_month),
                    "horizon_days": monthrange(issue_month.year, issue_month.month)[1],
                    # `climatology=None` keeps the anomalies null: the train years are the
                    # split's, and they are T5's.
                    **build_features(
                        issue_month=issue_month,
                        precipitation=precipitation,
                        soil_moisture=soil_moisture,
                        elevation_m=ground.elevation_m,
                        neighbours=ground.neighbours,
                        climatology=None,
                    ),
                    LABEL_COLUMN: int((code, issue_month.year, issue_month.month) in labelled),
                }
            )
    return pd.DataFrame(rows, columns=list(COLUMNS))


def _months(first: date, last: date) -> list[date]:
    """Every first-of-month from `first` to `last`, both included, in order."""
    return [
        date(index // 12, index % 12 + 1, 1)
        for index in range(first.year * 12 + first.month - 1, last.year * 12 + last.month)
    ]


def _series_by_code(weather: pd.DataFrame) -> dict[str, tuple[DailySeries, DailySeries]]:
    """Each municipality's precipitation and soil moisture, keyed by day.

    A NaN stays in the mapping: the shared module reads it as the missing day it is, and
    turning it into 0 mm would claim the weather was dry and the soil empty.
    """
    series: dict[str, tuple[DailySeries, DailySeries]] = {}
    for code, frame in weather.groupby("code", sort=False):
        days = [pd.Timestamp(day).date() for day in frame["date"]]
        series[str(code)] = (
            dict(zip(days, frame["precipitation_sum"].tolist(), strict=True)),
            dict(zip(days, frame["soil_moisture_0_to_7cm_mean"].tolist(), strict=True)),
        )
    return series


def _labelled_months(labels: pd.DataFrame) -> set[tuple[str, int, int]]:
    """`(code, year, month)` of every municipality-month that has a flood report.

    Reports repeat — 550 combinations of municipality, day and class arrive more than
    once with different source ids (data card, sesgo 4) — and the label of docs/08 §M2
    "Etiqueta" is binary per municipality and month, so they collapse into one key.
    """
    stamps = pd.to_datetime(labels["date"])
    return {
        (str(code), int(stamp.year), int(stamp.month))
        for code, stamp in zip(labels["code"], stamps, strict=True)
    }


def _terrain(elevation: pd.DataFrame) -> dict[str, Terrain]:
    """`code -> Terrain`, the elevation pair `build_features` takes."""
    terrain: dict[str, Terrain] = {}
    for row in elevation.itertuples():
        terrain[str(row.code)] = Terrain(_number(row.elevation_m), _neighbours(row))
    return terrain


def _neighbours(row: Any) -> Neighbours | None:
    """The four neighbours, or `None` when one of them is missing.

    A slope built from an invented neighbour is the slope of a hill that does not exist,
    so `slope_deg` stays missing evidence instead (the same rule the serving job applies).
    """
    east, west, north, south = (
        _number(getattr(row, f"{direction}_m")) for direction in NEIGHBOUR_DIRECTIONS
    )
    spacing_m = _number(row.spacing_m)
    if spacing_m is None or east is None or west is None or north is None or south is None:
        return None
    return Neighbours(east=east, west=west, north=north, south=south, spacing_m=spacing_m)


def _number(value: Any) -> float | None:
    """A parquet reading as a number, or `None` when pandas stored a missing one as NaN."""
    if value is None:
        return None
    reading = float(value)
    return None if math.isnan(reading) else reading


@dataclass(frozen=True, slots=True)
class Built:
    """Where the dataset landed, and the manifest that traces it."""

    path: Path
    manifest_path: Path
    manifest: dict[str, Any]


def build_dataset(
    *,
    layout: Layout = DEFAULT_LAYOUT,
    out_dir: Path | None = None,
    manifest_dir: Path | None = None,
) -> Built:
    """Build the table, write it as one parquet and record its sha256 in the manifest.

    Every input is read before anything is written, so a cache that answers less than the
    four parquets leaves no dataset and no manifest behind: the harness must never find a
    parquet it cannot trace.

    The build is a pure function of those parquets. Nothing here reads the clock or a
    random seed, so the same cache gives the same file and the same sha256 on any day
    (docs/08 §Reglas de gobierno: "reproducible o no existe").
    """
    frames = {name: _read_source(layout, name) for name in SOURCE_NAMES}
    table = build_table(
        frames["municipalities"], frames["weather"], frames["elevation"], frames["labels"]
    )

    path = (out_dir or DEFAULT_LAYOUT.data.parent / "dataset") / PARQUET_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written beside its target and renamed, like every raw copy: a half-written parquet
    # is a dataset a later run reads as complete.
    partial = path.with_name(f"{path.name}.part")
    table.to_parquet(partial, index=False)
    partial.replace(path)

    manifest = _manifest(table, path, layout, frames)
    manifest_path = (manifest_dir or ML_ROOT / "datasets" / DATASET_NAME) / MANIFEST_NAME
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return Built(path, manifest_path, manifest)


def main(argv: Sequence[str] | None = None, *, layout: Layout = DEFAULT_LAYOUT) -> int:
    """The build script's entry point: build, write the manifest, print it."""
    parser = argparse.ArgumentParser(
        description="Build the M2 municipality x month dataset from the source parquets."
    )
    parser.add_argument("--out", type=Path, default=None, help="directory for the parquet")
    parser.add_argument("--manifest", type=Path, default=None, help="directory for manifest.json")
    arguments = parser.parse_args(argv)
    built = build_dataset(layout=layout, out_dir=arguments.out, manifest_dir=arguments.manifest)
    print(json.dumps(built.manifest, indent=2, sort_keys=True))
    return 0


def _read_source(layout: Layout, name: str) -> pd.DataFrame:
    """One source parquet, or a refusal naming the step that writes it.

    The dataset is built from the cached copies alone (docs/08 §Reglas de gobierno), so a
    source the parse never wrote is a step that never ran, not an empty table.
    """
    path = layout.data / f"{name}.parquet"
    if not path.exists():
        raise ValueError(
            f"the {name} parquet is missing ({path}): the dataset is built only from the "
            "parquets of the parse step, so run it and build again"
        )
    return pd.read_parquet(path)


def _manifest(
    table: pd.DataFrame,
    path: Path,
    layout: Layout,
    frames: dict[str, pd.DataFrame],
) -> dict[str, Any]:
    """What the dataset is, what it cost and what it was built from.

    No build date: the manifest is part of what has to be reproducible, and a clock would
    make two builds of one cache differ.
    """
    positives = int(table[LABEL_COLUMN].sum())
    return {
        "dataset": DATASET_NAME,
        "file": path.name,
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        "rows": int(len(table)),
        "positives": positives,
        "negatives": int(len(table)) - positives,
        "municipalities": int(table["code"].nunique()),
        "months": {
            "from": f"{table['year'].min():04d}-{table['month'].min():02d}",
            "to": f"{table['year'].max():04d}-{table['month'].max():02d}",
        },
        "columns": list(COLUMNS),
        "all_null_features": [
            column for column in FEATURE_NAMES if bool(table[column].isna().all())
        ],
        "sources": {
            name: {
                "rows": int(len(frame)),
                "sha256": _sha256(layout.data / f"{name}.parquet"),
            }
            for name, frame in frames.items()
        },
    }


def _sha256(path: Path) -> str:
    """The hash of a file, the same digest the raw-cache manifest records."""
    return hashlib.sha256(path.read_bytes()).hexdigest()
