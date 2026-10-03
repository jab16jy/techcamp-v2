"""The written dataset: one parquet, its sha256 and the manifest that traces it."""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from techcamp_ml.datasets import flood_m2
from techcamp_ml.datasets.flood_m2 import COLUMNS, build_dataset, main, month_range
from techcamp_ml.sources import pipeline
from techcamp_ml.sources.cache import save_raw
from techcamp_ml.sources.layout import Layout
from techcamp_ml.sources.pipeline import plan_trace_path

PARQUET = "flood_m2.parquet"
MANIFEST = "manifest.json"


def _out(sources: Layout) -> Path:
    return sources.ml_root / "out"


def _card(sources: Layout) -> Path:
    return sources.ml_root / "card"


def _build(sources: Layout) -> object:
    return build_dataset(layout=sources, out_dir=_out(sources), manifest_dir=_card(sources))


def test_the_build_writes_one_parquet_and_the_manifest_that_traces_it(sources: Layout) -> None:
    built = _build(sources)

    assert built.path == _out(sources) / PARQUET
    assert built.manifest_path == _card(sources) / MANIFEST
    table = pd.read_parquet(built.path)
    assert list(table.columns) == list(COLUMNS)
    assert built.manifest["sha256"] == hashlib.sha256(built.path.read_bytes()).hexdigest()
    assert built.manifest["rows"] == len(table) == 195 * 84
    assert built.manifest["positives"] == 1
    assert built.manifest["negatives"] == 195 * 84 - 1
    assert built.manifest["months"] == {"from": "2019-01", "to": "2025-12"}
    assert json.loads(built.manifest_path.read_text()) == built.manifest


def test_the_same_inputs_build_the_same_dataset_on_every_run(sources: Layout) -> None:
    first = _build(sources)
    first_bytes = first.path.read_bytes()

    second = _build(sources)

    # The parquet is written beside its target and renamed, so the second run replaces it
    # whole; byte equality is the reproducibility docs/08 §Reglas de gobierno asks for.
    assert first.path.read_bytes() == first_bytes == second.path.read_bytes()
    assert first.manifest == second.manifest


def test_the_manifest_records_the_source_parquets_it_came_from(sources: Layout) -> None:
    built = _build(sources)

    for name, recorded in built.manifest["sources"].items():
        payload = (sources.data / f"{name}.parquet").read_bytes()
        assert recorded["sha256"] == hashlib.sha256(payload).hexdigest()
        assert recorded["rows"] == len(pd.read_parquet(sources.data / f"{name}.parquet"))
    # The anomalies are the columns a later step fills, so the manifest names them.
    assert built.manifest["all_null_features"] == [
        "precip_anomaly_1m",
        "precip_anomaly_3m",
        "precip_anomaly_6m",
    ]
    assert built.manifest["columns"] == list(COLUMNS)


def test_a_missing_source_parquet_is_refused_before_anything_is_written(
    sources: Layout,
) -> None:
    (sources.data / "labels.parquet").unlink()

    with pytest.raises(ValueError, match="labels"):
        _build(sources)

    # Negative assertion: no half dataset and no manifest left behind for the harness.
    assert not _out(sources).exists()
    assert not _card(sources).exists()


def test_a_parquet_left_by_a_parse_that_the_current_cache_would_refuse_is_refused(
    sources: Layout,
) -> None:
    # The parquet is still there and still readable; what moved is the plan underneath it.
    save_raw(
        sources,
        "weather",
        pipeline.PLAN_RAW,
        "test",
        json.dumps({"today": "2026-11-02", "chunks": []}).encode(),
    )

    with pytest.raises(ValueError, match="weather"):
        _build(sources)

    assert not _out(sources).exists()


def test_a_parquet_with_no_record_of_the_plan_it_came_from_is_refused(sources: Layout) -> None:
    plan_trace_path(sources, "labels").unlink()

    with pytest.raises(ValueError, match="labels"):
        _build(sources)

    assert not _out(sources).exists()


def test_the_build_script_prints_the_manifest_it_wrote(
    sources: Layout,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        ["--out", str(_out(sources)), "--manifest", str(_card(sources))],
        layout=sources,
    )

    printed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert printed == json.loads((_card(sources) / MANIFEST).read_text())
    assert printed["dataset"] == "flood_m2"


def test_the_dataset_lands_only_once_its_manifest_exists(
    sources: Layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("the disk is full")

    # The dataset is the file the harness reads, so a build that dies before writing its
    # manifest must not have published it: a parquet beside a manifest that does not
    # describe it is a dataset nobody can trace (#245).
    monkeypatch.setattr(flood_m2, "_manifest", refuse)

    with pytest.raises(RuntimeError, match="disk is full"):
        _build(sources)

    assert not (_out(sources) / PARQUET).exists(), "published without its manifest"
    assert not (_card(sources) / MANIFEST).exists()
    # Negative assertion: what a dead build leaves is recognisably pending — the same
    # `.part` convention the raw cache uses — and never a file a reader takes as the
    # dataset.
    assert [pending.name for pending in _out(sources).glob("*")] == [f"{PARQUET}.part"]


def test_a_successful_build_leaves_no_part_file_behind(sources: Layout) -> None:
    _build(sources)

    assert list(_out(sources).glob("*.part")) == []
    assert list(_card(sources).glob("*.part")) == []
    # The manifest is written whole or not at all: a truncated one is not valid JSON.
    assert json.loads((_card(sources) / MANIFEST).read_text())["rows"] == 195 * 84


def test_the_month_range_is_read_from_the_horizon_not_from_the_columns() -> None:
    table = pd.DataFrame(
        {
            "year": [2019, 2025],
            "month": [1, 12],
            "horizon_start": pd.to_datetime(["2019-03-01", "2025-07-01"]),
        }
    )

    assert month_range(table) == {"from": "2019-03", "to": "2025-07"}
    # Negative assertion: the smallest month in the column is January whatever year it
    # belongs to, so a window that starts mid-year would report the wrong first month.
    assert month_range(table)["from"] != "2019-01"


def test_the_default_output_directories_follow_the_layout_it_was_given(
    sources: Layout,
) -> None:
    built = build_dataset(layout=sources)

    assert built.path == sources.data.parent / "dataset" / PARQUET
    assert built.manifest_path == sources.ml_root / "datasets" / "flood_m2" / MANIFEST
    # Negative assertion: a custom cache must never publish into the real repository tree.
    assert not Path("ml/data/flood_m2/dataset").exists()
    assert not Path("ml/datasets/flood_m2/manifest.json").exists()


def test_the_manifest_hashes_the_bytes_the_table_was_built_from(
    sources: Layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    weather = sources.data / "weather.parquet"
    before = hashlib.sha256(weather.read_bytes()).hexdigest()
    written = flood_m2._write_atomically

    def parse_lands_mid_build(path: Path, payload: bytes) -> None:
        if path.name == MANIFEST:
            # A parse rewriting the source while the build is writing its manifest must
            # not change what the manifest says was used to build the table.
            weather.write_bytes(b"rewritten by a parse the build never saw")
        written(path, payload)

    monkeypatch.setattr(flood_m2, "_write_atomically", parse_lands_mid_build)

    built = _build(sources)

    assert built.manifest["sources"]["weather"]["sha256"] == before
    assert hashlib.sha256(weather.read_bytes()).hexdigest() != before


def test_the_manifest_of_a_source_that_never_changed_is_still_readable(sources: Layout) -> None:
    built = _build(sources)

    payloads: dict[str, Callable[[], Any]] = {
        name: (lambda path=sources.data / f"{name}.parquet": path.read_bytes())
        for name in built.manifest["sources"]
    }
    for name, recorded in built.manifest["sources"].items():
        assert recorded["sha256"] == hashlib.sha256(payloads[name]()).hexdigest()
