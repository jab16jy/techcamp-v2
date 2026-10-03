"""The `parse` step: raw copies in, tidy parquets out, offline."""

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from techcamp_ml.sources import pipeline
from techcamp_ml.sources.cache import read_manifest, save_raw
from techcamp_ml.sources.layout import Layout
from techcamp_ml.sources.pipeline import parse_sources
from techcamp_ml.sources.weather import (
    WEATHER_START,
    last_complete_month,
    weather_windows,
)

DEPARTMENT_SIZES = {"08": 23, "13": 46, "20": 25, "23": 30, "44": 15, "47": 30, "70": 26}
REGION_LABELS = {"08549", "23068", "23350", "13244"}
"""The four Caribbean codes the recorded label fixture reports, kept inside the fake
region so the label fixture parses instead of being dropped as out of region."""


def _codes() -> list[str]:
    generated = [
        f"{dept}{i:03d}" for dept, size in DEPARTMENT_SIZES.items() for i in range(1, size + 1)
    ]
    fillers = [code for code in generated if code not in REGION_LABELS]
    return sorted(REGION_LABELS | set(fillers[: 195 - len(REGION_LABELS)]))


def _region_payloads() -> tuple[bytes, bytes]:
    """A synthetic 195-municipality DIVIPOLA response and its MGN control."""
    codes = _codes()
    rows = [
        {
            "cod_dpto": code[:2],
            "dpto": code[:2],
            "cod_mpio": code,
            "nom_mpio": f"MUNICIPIO {code}",
            "tipo_municipio": "Municipio",
            "longitud": f"{-74.5 + i / 100:.6f}".replace(".", ","),
            "latitud": f"{10.5 + i / 100:.6f}".replace(".", ","),
        }
        for i, code in enumerate(codes)
    ]
    divipola = json.dumps(rows).encode()
    mgn = json.dumps(
        {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": None, "properties": {"mpio_cdpmp": code}}
                for code in codes
            ],
        }
    ).encode()
    return divipola, mgn


def _archive_day() -> dict[str, object]:
    """One archive location and one day, shaped as the API answers it."""
    return {
        "daily": {
            "time": ["2026-01-02"],
            "precipitation_sum": [1.0],
            "soil_moisture_0_to_7cm_mean": [0.3],
        }
    }


def _recording_fetch(requested: list[str]) -> Callable[..., bytes]:
    """A stand-in for the network: it answers like the API and caches like `fetch`."""

    def fake_fetch(
        target: Layout,
        source: str,
        name: str,
        url: str,
        **kwargs: object,
    ) -> bytes:
        params = kwargs.get("params") or {}
        requested.append(name)
        if source == "municipalities":
            payload = _region_payloads()[0 if name == "divipola.json" else 1]
        elif name.endswith((".json",)) and "labels" == source:
            payload = json.dumps(_LABEL_PAGE[:2]).encode()
        elif source == "elevation":
            # The elevation endpoint answers one value per requested coordinate.
            asked = len(str(params.get("latitude", "")).split(","))
            payload = json.dumps({"elevation": [10.0] * asked}).encode()
        else:
            payload = json.dumps(
                [_archive_day()] * len(str(params.get("latitude", "")).split(","))
            ).encode()
        save_raw(target, source, name, url, payload)
        return payload

    return fake_fetch


_LABEL_PAGE: list[dict[str, object]] = []


def _cache(layout: Layout, fixture: Callable[[str], bytes]) -> None:
    """A raw cache holding a full 195-municipality region plus one real label response."""
    divipola, mgn = _region_payloads()
    save_raw(layout, "municipalities", "divipola.json", "test", divipola)
    save_raw(layout, "municipalities", "mgn317.geojson", "test", mgn)
    save_raw(layout, "labels", "wwkg-r6te.p000.json", "test", fixture("ungrd_wwkg-r6te.json"))


def test_parse_builds_the_region_and_only_the_requested_sources(
    tmp_path: Path,
    fixture: Callable[[str], bytes],
) -> None:
    layout = Layout(tmp_path)
    _cache(layout, fixture)

    written = parse_sources(["labels"], layout=layout)

    assert set(written) == {"municipalities", "labels"}, "municipalities is a dependency of labels"
    assert not (layout.data / "weather.parquet").exists(), "an unrequested source is not built"
    municipalities = pd.read_parquet(layout.data / "municipalities.parquet")
    assert len(municipalities) == 195
    labels = pd.read_parquet(layout.data / "labels.parquet")
    assert list(labels.columns) == [
        "code",
        "date",
        "event_class",
        "source_dataset",
        "source_row_id",
    ]
    assert not labels.empty
    # Negative assertion: labels never escape the region parsed above.
    assert set(labels["code"]) <= set(_codes())


def test_parse_rejects_an_unknown_source_name(
    tmp_path: Path, fixture: Callable[[str], bytes]
) -> None:
    layout = Layout(tmp_path)
    _cache(layout, fixture)

    with pytest.raises(KeyError, match="satellites"):
        parse_sources(["satellites"], layout=layout)


def test_the_weather_fetch_splits_into_windows_and_resumes(
    tmp_path: Path,
    fixture: Callable[[str], bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = Layout(tmp_path)
    divipola, mgn = _region_payloads()
    requested: list[tuple[str, dict[str, object]]] = []

    def fake_fetch(
        target: Layout,
        source: str,
        name: str,
        url: str,
        *,
        params: dict[str, object] | None = None,
        weight: int = 0,
        timeout: float = 0.0,
        retries: int = 0,
    ) -> bytes:
        requested.append((name, dict(params or {})))
        if source == "municipalities":
            payload = divipola if name == "divipola.json" else mgn
        else:
            # One archive location per requested coordinate, as the real API answers.
            locations = str((params or {}).get("latitude", "")).count(",") + 1
            payload = json.dumps(
                [
                    {
                        "daily": {
                            "time": ["2026-01-02"],
                            "precipitation_sum": [1.0],
                            "soil_moisture_0_to_7cm_mean": [0.3],
                        }
                    }
                    for _ in range(locations)
                ]
            ).encode()
        # The real `fetch` saves what it downloads; the stub has to, or the parse
        # step would find an empty cache.
        save_raw(target, source, name, url, payload)
        return payload

    monkeypatch.setattr(pipeline, "fetch", fake_fetch)

    # The day is given, never taken from the wall clock: the same cache answers the same
    # question in November that it answered in October.
    today = date(2026, 10, 2)
    windows = weather_windows(WEATHER_START, last_complete_month(today))
    assert pipeline.fetch_sources(["weather"], layout=layout, today=today)["weather"] == (
        len(windows) * 2
    )

    # A second run asks for nothing at all: the region and every chunk are cached.
    assert pipeline.fetch_sources(["weather"], layout=layout, today=today)["weather"] == 0
    assert len(requested) == len(windows) * 2 + 2, "plus the two municipality calls, once"

    frame = pd.read_parquet(pipeline.parse_sources(["weather"], layout=layout)["weather"])
    assert len(frame) == 195
    assert frame["code"].nunique() == 195
    assert frame["precipitation_sum"].sum() == pytest.approx(195.0)


def test_a_label_source_resumes_past_the_pages_it_already_has(
    tmp_path: Path,
    fixture: Callable[[str], bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global _LABEL_PAGE  # noqa: PLW0603 - a fixture the shared stub reads
    layout = Layout(tmp_path)
    _cache(layout, fixture)
    _LABEL_PAGE = json.loads(fixture("ungrd_wwkg-r6te.json"))
    monkeypatch.setattr(pipeline, "SOCRATA_PAGE", len(_LABEL_PAGE))
    requested: list[str] = []
    monkeypatch.setattr(pipeline, "fetch", _recording_fetch(requested))

    assert pipeline.fetch_sources(["labels"], layout=layout)["labels"] == 3

    # Page 0 of wwkg is cached, so its page 1 still has to be asked for: stopping at the
    # first cached page would leave the labels parquet silently short.
    assert requested == ["wwkg-r6te.p001.json", "rgre-6ak4.p000.json", "2343-nuqp.p000.json"]
    assert layout.raw_copy("labels", "wwkg-r6te.p001.json").exists()
    # The negative half: page 1 came back short, so nothing follows it.
    assert "wwkg-r6te.p002.json" not in requested


def test_an_archived_chunk_without_its_sidecar_is_fetched_again(
    tmp_path: Path,
    fixture: Callable[[str], bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = Layout(tmp_path)
    _cache(layout, fixture)
    requested: list[str] = []
    monkeypatch.setattr(pipeline, "fetch", _recording_fetch(requested))
    pipeline.fetch_sources(["weather"], layout=layout, today=date(2026, 10, 2))

    # An interruption between the response and its sidecar leaves the chunk unfinished.
    (layout.raw_copy("weather", "archive_000.request.json")).unlink()
    requested.clear()
    pipeline.fetch_sources(["weather"], layout=layout, today=date(2026, 10, 2))

    assert requested == ["archive_000.json"], "a response without its sidecar is re-fetched"
    assert layout.raw_copy("weather", "archive_000.request.json").exists()


def test_parsing_refuses_an_incomplete_archive_download(
    tmp_path: Path,
    fixture: Callable[[str], bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = Layout(tmp_path)
    _cache(layout, fixture)
    requested: list[str] = []
    monkeypatch.setattr(pipeline, "fetch", _recording_fetch(requested))
    pipeline.fetch_sources(["weather"], layout=layout, today=date(2026, 10, 2))
    (layout.raw_copy("weather", "archive_005.json")).unlink()

    # A partial climate series must not come out as an ordinary parquet: T4 would take
    # it for the whole region.
    with pytest.raises(ValueError, match="archive_005.json"):
        pipeline.parse_sources(["weather"], layout=layout)


def _move_sidecar(layout: Layout, name: str, **window: str) -> None:
    """Rewrite a chunk's sidecar so it states another window than the one it holds."""
    path = layout.raw_copy("weather", f"{name}.request.json")
    sidecar = json.loads(path.read_bytes())
    sidecar.update(window)
    path.write_bytes(json.dumps(sidecar).encode())


def test_parsing_refuses_a_chunk_downloaded_for_another_window(
    tmp_path: Path,
    fixture: Callable[[str], bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = Layout(tmp_path)
    _cache(layout, fixture)
    monkeypatch.setattr(pipeline, "fetch", _recording_fetch([]))
    today = date(2026, 10, 2)
    pipeline.fetch_sources(["weather"], layout=layout, today=today)
    # A cache downloaded when the range stopped a day earlier: every chunk name is
    # there, and the days it holds are not the ones the fetch planned.
    _move_sidecar(layout, "archive_002", end_date="2026-06-27")

    with pytest.raises(ValueError, match="archive_002.json"):
        pipeline.parse_sources(["weather"], layout=layout)

    # Negative half: the same chunk with the window the plan gave it parses.
    _move_sidecar(layout, "archive_002", end_date="2026-06-28")
    assert (
        len(pd.read_parquet(pipeline.parse_sources(["weather"], layout=layout)["weather"])) == 195
    )

    # And a chunk that answers other coordinates than the plan named is refused too.
    sidecar_path = layout.raw_copy("weather", "archive_002.request.json")
    sidecar = json.loads(sidecar_path.read_bytes())
    sidecar["codes"] = sidecar["codes"][:1]
    sidecar_path.write_bytes(json.dumps(sidecar).encode())
    with pytest.raises(ValueError, match="archive_002.json"):
        pipeline.parse_sources(["weather"], layout=layout)


def test_the_fetch_replaces_a_chunk_whose_window_moved(
    tmp_path: Path,
    fixture: Callable[[str], bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = Layout(tmp_path)
    _cache(layout, fixture)
    requested: list[str] = []
    monkeypatch.setattr(pipeline, "fetch", _recording_fetch(requested))
    today = date(2026, 10, 2)
    pipeline.fetch_sources(["weather"], layout=layout, today=today)
    _move_sidecar(layout, "archive_000", end_date="2019-06-29")
    requested.clear()

    assert pipeline.fetch_sources(["weather"], layout=layout, today=today)["weather"] == 1

    # Negative half: a chunk whose sidecar already states the window this day owes is
    # not requested again.
    assert requested == ["archive_000.json"]
    start, stop = weather_windows(WEATHER_START, last_complete_month(today))[0]
    sidecar = json.loads(layout.raw_copy("weather", "archive_000.request.json").read_bytes())
    assert (sidecar["start_date"], sidecar["end_date"]) == (str(start), str(stop))


def _clock(day: date) -> type[date]:
    """A `date` whose `today()` is pinned to `day`, so a hidden clock read fails loudly."""
    return type("_FrozenClock", (date,), {"today": classmethod(lambda _cls: day)})


def test_the_same_cache_builds_the_same_parquet_on_any_day(
    tmp_path: Path,
    fixture: Callable[[str], bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The range a parse owes is the one the fetch planned, not the day it runs on."""
    layout = Layout(tmp_path)
    _cache(layout, fixture)
    monkeypatch.setattr(pipeline, "fetch", _recording_fetch([]))
    pipeline.fetch_sources(["weather"], layout=layout, today=date(2026, 10, 2))
    first = pd.read_parquet(pipeline.parse_sources(["weather"], layout=layout)["weather"])
    assert len(first) == 195
    # The plan is a cached copy like any other: the manifest has to document it.
    assert pipeline.PLAN_RAW in set(read_manifest(layout)["file"])

    for day in (date(2026, 10, 2), date(2026, 11, 20), date(2035, 1, 1)):
        monkeypatch.setattr(pipeline, "date", _clock(day))
        again = pd.read_parquet(pipeline.parse_sources(["weather"], layout=layout)["weather"])
        # Negative half: a later day cannot add a window the fetch never planned, and it
        # cannot refuse a cache that answers the plan either.
        assert again.equals(first), f"the parse changed on {day}"


def test_parsing_refuses_a_chunk_the_plan_promises_and_the_cache_lacks(
    tmp_path: Path,
    fixture: Callable[[str], bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = Layout(tmp_path)
    _cache(layout, fixture)
    monkeypatch.setattr(pipeline, "fetch", _recording_fetch([]))
    pipeline.fetch_sources(["weather"], layout=layout, today=date(2026, 10, 2))
    (layout.raw_copy("weather", "archive_003.json")).unlink()

    with pytest.raises(ValueError, match="archive_003.json"):
        pipeline.parse_sources(["weather"], layout=layout)


def test_parsing_a_cache_with_no_plan_says_run_fetch_first(
    tmp_path: Path,
    fixture: Callable[[str], bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cache T3 closed with: three chunks and no plan, because plans did not exist."""
    layout = Layout(tmp_path)
    _cache(layout, fixture)
    monkeypatch.setattr(pipeline, "fetch", _recording_fetch([]))
    pipeline.fetch_sources(["weather"], layout=layout, today=date(2026, 10, 2))
    layout.raw_copy("weather", pipeline.PLAN_RAW).unlink()
    assert layout.raw_copy("weather", "archive_000.json").exists(), "the bytes are still there"

    with pytest.raises(ValueError, match="fetch"):
        pipeline.parse_sources(["weather"], layout=layout)


def test_the_elevation_download_asks_for_every_seat_and_its_neighbours(
    tmp_path: Path,
    fixture: Callable[[str], bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = Layout(tmp_path)
    _cache(layout, fixture)
    requested: list[str] = []
    monkeypatch.setattr(pipeline, "fetch", _recording_fetch(requested))
    today = date(2026, 10, 2)

    # 195 municipalities x (the seat plus four neighbours) at 100 coordinates per call.
    assert pipeline.fetch_sources(["elevation"], layout=layout, today=today)["elevation"] == 10
    # Negative half: a resume asks for nothing, the chunks are already in the cache.
    assert pipeline.fetch_sources(["elevation"], layout=layout, today=today)["elevation"] == 0

    frame = pd.read_parquet(pipeline.parse_sources(["elevation"], layout=layout)["elevation"])
    assert len(frame) == 195
    assert set(frame["code"]) == set(_codes()), "every municipality, and no neighbour row"
    assert not frame.duplicated(subset=["code"]).any()
    assert {"east_m", "west_m", "north_m", "south_m"} <= set(frame.columns)


def test_parsing_refuses_an_elevation_that_leaves_a_municipality_out(
    tmp_path: Path,
    fixture: Callable[[str], bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = Layout(tmp_path)
    _cache(layout, fixture)
    monkeypatch.setattr(pipeline, "fetch", _recording_fetch([]))
    today = date(2026, 10, 2)
    pipeline.fetch_sources(["elevation"], layout=layout, today=today)
    (layout.raw_copy("elevation", "elevation_009.json")).unlink()

    # A short slope table would leave T4's neighbour features undefined for the
    # municipalities it lost, with nothing saying so.
    with pytest.raises(ValueError, match="elevation"):
        pipeline.parse_sources(["elevation"], layout=layout)
    assert not (layout.data / "elevation.parquet").exists(), "no parquet from a short cache"


def test_parsing_an_elevation_cache_with_nothing_in_it_says_run_fetch_first(
    tmp_path: Path,
    fixture: Callable[[str], bytes],
) -> None:
    layout = Layout(tmp_path)
    divipola, mgn = _region_payloads()
    save_raw(layout, "municipalities", "divipola.json", "test", divipola)
    save_raw(layout, "municipalities", "mgn317.geojson", "test", mgn)

    with pytest.raises(ValueError, match="fetch"):
        pipeline.parse_sources(["elevation"], layout=layout)


def test_parsing_labels_with_nothing_in_the_cache_says_run_fetch_first(
    tmp_path: Path,
    fixture: Callable[[str], bytes],
) -> None:
    layout = Layout(tmp_path)
    divipola, mgn = _region_payloads()
    save_raw(layout, "municipalities", "divipola.json", "test", divipola)
    save_raw(layout, "municipalities", "mgn317.geojson", "test", mgn)

    # A KeyError on `code` would tell nobody that the step to run is the fetch one.
    with pytest.raises(ValueError, match="fetch"):
        pipeline.parse_sources(["labels"], layout=layout)


def test_a_cached_payload_the_manifest_never_documented_is_downloaded_again(
    tmp_path: Path,
    fixture: Callable[[str], bytes],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = Layout(tmp_path)
    _cache(layout, fixture)
    requested: list[str] = []
    monkeypatch.setattr(pipeline, "fetch", _recording_fetch(requested))
    today = date(2026, 10, 2)
    pipeline.fetch_sources(["weather"], layout=layout, today=today)
    # A crash between the payload and its manifest row leaves bytes nobody can trace.
    layout.manifest.unlink()
    requested.clear()

    owed = len(weather_windows(WEATHER_START, last_complete_month(today))) * 2
    assert pipeline.fetch_sources(["weather"], layout=layout, today=today)["weather"] == owed

    # Negative half: every payload is still sitting on disk, and the resume ignores it.
    assert all(layout.raw_copy("weather", f"archive_{i:03d}.json").exists() for i in range(owed))
    assert sorted(name for name in requested if name.startswith("archive_")) == [
        f"archive_{i:03d}.json" for i in range(owed)
    ]
