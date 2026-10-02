"""The `parse` step: raw copies in, tidy parquets out, offline."""

import json
from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pytest

from techcamp_ml.sources.cache import save_raw
from techcamp_ml.sources.layout import Layout
from techcamp_ml.sources.pipeline import parse_sources

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


def _cache(layout: Layout, fixture: Callable[[str], bytes]) -> None:
    """A raw cache holding a full 195-municipality region plus one real label response."""
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
    save_raw(layout, "municipalities", "divipola.json", "test", json.dumps(rows).encode())
    save_raw(
        layout,
        "municipalities",
        "mgn317.geojson",
        "test",
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature", "geometry": None, "properties": {"mpio_cdpmp": code}}
                    for code in codes
                ],
            }
        ).encode(),
    )
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
