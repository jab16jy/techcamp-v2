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
import io
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
from techcamp_ml.sources.layout import DEFAULT_LAYOUT, Layout
from techcamp_ml.sources.municipalities import assert_region
from techcamp_ml.sources.pipeline import LABELS_PLAN_RAW, PLAN_RAW, plan_trace_path

DATASET_NAME = "flood_m2"
DATASET_DIRNAME = "dataset"
"""`ml/data/flood_m2/dataset/`, beside the `sources/` the parse step writes and out of git
(docs/08 §Estructura de `ml/`)."""
PARQUET_NAME = f"{DATASET_NAME}.parquet"
MANIFEST_NAME = "manifest.json"
SOURCE_NAMES = ("municipalities", "weather", "elevation", "labels")
"""The four parquets of the parse step, in the order the table is built from them."""

PLAN_OF_SOURCE = {"weather": PLAN_RAW, "labels": LABELS_PLAN_RAW}
"""The raw plan that governs each plan-governed parquet. The municipalities and the
elevation have none: they are a pure function of their own raw copies."""

LABEL_LAST_YEAR = 2025
LABEL_LAST_MONTH = 12
"""The closed label window of docs/08 §Fuentes de datos de M2 and D-T3.2: UNGRD 2019-2025,
no DesInventar and no pre-2019 source."""

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
    """First and last month of the label window docs/08 names, never of the data.

    The window is **2019-2025**: the UNGRD consolidados that docs/08 §Fuentes de datos de
    M2 and D-T3.2 close for M2, and the months inside it are the ones the download claims,
    so a month there with no report is a negative.

    It is written down rather than measured. `2343-nuqp` is declared open (`year_to=None`)
    so the query is never capped, and the newest report in the parquet is not a window
    either: a dataset whose horizon moved with its downloads would grow and shrink with
    them, and a month the source had not published yet is unknown, not a negative (data
    card, sesgo 3 — the reporting lag stays a bias, not a bound).

    A month outside this range is left out of the table. Adding it as a `0` would train
    the model on months whose silence nobody vouched for.
    """
    if labels.empty:
        raise ValueError(
            "the labels table holds no flood report: run the parse step for labels and build again"
        )
    first_year = min(source.year_from for source in sources)
    return date(first_year, 1, 1), date(LABEL_LAST_YEAR, LABEL_LAST_MONTH, 1)


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

    The region is asserted here and not only in the parse: 195 municipalities of
    docs/08 §M2 "Región" is what makes this table M2's, and a dataset built over any other
    region would train a model the docs never described (docs/08:68).
    """
    assert_region(municipalities)
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

    Two rows for the same municipality and day are refused rather than resolved. A mapping
    would keep whichever came last, so the value a window sees would depend on parquet row
    order instead of on a rule — and the parse is where a duplicated day belongs (#245).
    """
    duplicated = weather[weather.duplicated(subset=["code", "date"], keep=False)]
    if not duplicated.empty:
        repeated = duplicated[["code", "date"]].drop_duplicates().head(3)
        pairs = ", ".join(
            f"{code} {pd.Timestamp(day).date()}" for code, day in repeated.itertuples(index=False)
        )
        raise ValueError(
            f"the weather parquet holds {len(duplicated)} rows for {len(repeated)}+ repeated "
            f"(code, date) pairs, e.g. {pairs}; which one a window would read is row order, "
            "not a rule, so run the parse step and build again"
        )
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

    Read by direction name, never by position: `NEIGHBOUR_DIRECTIONS` belongs to the
    elevation module, and unpacking it in order would swap the slope axes without an error
    the day its order changed (#245).

    A slope built from an invented neighbour is the slope of a hill that does not exist,
    so `slope_deg` stays missing evidence instead (the same rule the serving job applies).
    """
    readings = {
        direction: _number(getattr(row, f"{direction}_m")) for direction in NEIGHBOUR_DIRECTIONS
    }
    east = readings["east"]
    west = readings["west"]
    north = readings["north"]
    south = readings["south"]
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
class Source:
    """One source parquet: the frame it was read into, and the digest of those bytes.

    The digest belongs to the read, not to the file: a parse that rewrites the file while
    the build runs cannot change what the table was built from (#245).
    """

    frame: pd.DataFrame
    sha256: str


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

    `out_dir` and `manifest_dir` default to the directories of **`layout`**, not of the
    repository: a caller that points the build at another cache publishes into that cache,
    never over the real dataset with one built from somewhere else.

    The manifest is written whole and renamed **before** the parquet lands, so at no point
    does a dataset exist without the manifest that traces it, and neither file is ever
    visible half written (#245).

    The build is a pure function of those parquets. Nothing here reads the clock or a
    random seed, so the same cache gives the same file and the same sha256 on any day
    (docs/08 §Reglas de gobierno: "reproducible o no existe").
    """
    sources = {name: _read_source(layout, name) for name in SOURCE_NAMES}
    for name in PLAN_OF_SOURCE:
        _assert_built_from_the_cached_plan(layout, name)
    table = build_table(
        sources["municipalities"].frame,
        sources["weather"].frame,
        sources["elevation"].frame,
        sources["labels"].frame,
    )

    path = (out_dir or layout.data.parent / DATASET_DIRNAME) / PARQUET_NAME
    pending = _write_parquet(table, path)
    # Hashed and described before either rename, so the manifest records the file that is
    # about to be published rather than one that was already there.
    manifest = _manifest(table, path, pending, sources)
    manifest_path = (manifest_dir or layout.ml_root / "datasets" / DATASET_NAME) / MANIFEST_NAME
    _write_atomically(
        manifest_path, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    )
    _rename_into_place(pending, path)
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


def month_range(table: pd.DataFrame) -> dict[str, str]:
    """The first and last month the table predicts for.

    Read from `horizon_start`, which names the month as a whole: the smallest value of the
    `month` column is January whatever year it belongs to, so a window that opened in
    March would report a January it never covered (#245).
    """
    return {
        "from": _month_text(table["horizon_start"].min()),
        "to": _month_text(table["horizon_start"].max()),
    }


def _month_text(stamp: Any) -> str:
    moment = pd.Timestamp(stamp)
    return f"{moment.year:04d}-{moment.month:02d}"


def _read_source(layout: Layout, name: str) -> Source:
    """One source parquet and the digest of the bytes it was read from, or a refusal.

    The dataset is built from the cached copies alone (docs/08 §Reglas de gobierno), so a
    source the parse never wrote is a step that never ran, not an empty table.

    The bytes are read once and hashed from that read: a parse landing mid-build cannot
    then make the manifest describe bytes the table was not built from (#245).
    """
    path = layout.data / f"{name}.parquet"
    if not path.exists():
        raise ValueError(
            f"the {name} parquet is missing ({path}): the dataset is built only from the "
            "parquets of the parse step, so run it and build again"
        )
    payload = path.read_bytes()
    return Source(pd.read_parquet(io.BytesIO(payload)), _sha256(payload))


def _write_parquet(table: pd.DataFrame, path: Path) -> Path:
    """Write the dataset beside its target and return the pending file, unrenamed.

    The caller publishes it, so that the manifest can describe it first.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f"{path.name}.part")
    table.to_parquet(pending, index=False)
    return pending


def _write_atomically(path: Path, payload: bytes) -> None:
    """Write one file beside its target and rename it there.

    The manifest is read together with the dataset, so a truncated one is as useless as a
    missing one: it lands whole or not at all (#245).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f"{path.name}.part")
    pending.write_bytes(payload)
    _rename_into_place(pending, path)


def _rename_into_place(pending: Path, path: Path) -> None:
    """Publish a pending file under its final name."""
    pending.replace(path)


def _assert_built_from_the_cached_plan(layout: Layout, name: str) -> None:
    """Refuse a parquet that this cache's plan did not build.

    The parse writes the digest of the plan it consumed beside its parquet, because the
    plan lives in the raw cache and the parquet does not: a fetch that ran again, or one
    that was cut short, leaves the old parquet readable and plausible (docs/08:68, "el
    dataset se arma solo desde esas copias"). Without this check a stale parquet builds a
    dataset that no longer answers the cache it claims to come from.
    """
    plan_name = PLAN_OF_SOURCE[name]
    plan_path = layout.raw_copy(name, plan_name)
    trace_path = plan_trace_path(layout, name)
    if not plan_path.exists():
        raise ValueError(
            f"the raw cache holds no {plan_name}: the parse needs it to build the {name} "
            "parquet, so run the fetch step and parse again"
        )
    if not trace_path.exists():
        raise ValueError(
            f"the {name} parquet records no {plan_name} ({trace_path.name}): a parquet left "
            "by an earlier parse cannot be traced to this cache, so run the parse step and "
            "build again"
        )
    recorded = json.loads(trace_path.read_bytes()).get("sha256")
    if recorded != _sha256(plan_path.read_bytes()):
        raise ValueError(
            f"the {name} parquet was built from another {plan_name} than the one in the cache: "
            "the download moved on, so run the parse step and build again"
        )


def _manifest(
    table: pd.DataFrame,
    path: Path,
    pending: Path,
    sources: dict[str, Source],
) -> dict[str, Any]:
    """What the dataset is, what it cost and what it was built from.

    `pending` is the parquet about to be published, so the digest and the size recorded are
    the ones the published file will have, and `sources` carries the digest each source was
    read with rather than a second read of a file a parse may already have rewritten.

    No build date: the manifest is part of what has to be reproducible, and a clock would
    make two builds of one cache differ.
    """
    positives = int(table[LABEL_COLUMN].sum())
    return {
        "dataset": DATASET_NAME,
        "file": path.name,
        "sha256": _sha256(pending.read_bytes()),
        "bytes": pending.stat().st_size,
        "rows": int(len(table)),
        "positives": positives,
        "negatives": int(len(table)) - positives,
        "municipalities": int(table["code"].nunique()),
        "months": month_range(table),
        "columns": list(COLUMNS),
        "all_null_features": [
            column for column in FEATURE_NAMES if bool(table[column].isna().all())
        ],
        "sources": {
            name: {"rows": int(len(source.frame)), "sha256": source.sha256}
            for name, source in sources.items()
        },
    }


def _sha256(payload: bytes) -> str:
    """The digest of these bytes, the same one the raw-cache manifest and the plan traces
    record: one function, so the freshness check cannot start refusing valid parquets
    because two digests drifted apart (#245)."""
    return hashlib.sha256(payload).hexdigest()
