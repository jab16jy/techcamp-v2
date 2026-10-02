"""The `parse` step: raw copies in, tidy parquets out, offline."""

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from techcamp_ml.sources import pipeline
from techcamp_ml.sources.cache import save_raw
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

    windows = weather_windows(WEATHER_START, last_complete_month(date(2026, 10, 2)))
    assert pipeline.fetch_sources(["weather"], layout=layout)["weather"] == len(windows) * 2

    # A second run asks for nothing at all: the region and every chunk are cached.
    assert pipeline.fetch_sources(["weather"], layout=layout)["weather"] == 0
    assert len(requested) == len(windows) * 2 + 2, "plus the two municipality calls, once"

    frame = pd.read_parquet(pipeline.parse_sources(["weather"], layout=layout)["weather"])
    assert len(frame) == 195
    assert frame["code"].nunique() == 195
    assert frame["precipitation_sum"].sum() == pytest.approx(195.0)
