"""The municipality reference: DIVIPOLA seats, controlled by MGN 2024.

Owner decision D-T3.1 (2026-10-02): the point is the **municipal seat (cabecera)**
from DANE's DIVIPOLA table on datos.gov.co, because MGN layer 317 answers
`geometry: null` and has no centroid to compute. MGN is kept as the control: its
`mpio_cdpmp` set must equal the DIVIPOLA set or the build fails loudly.

Seats are not centroids. That is a deliberate, documented bias of the dataset: a
municipality's climate is sampled at its seat, which biases every municipality
towards its most accessible (usually most populated) point.
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

DIVIPOLA_RESOURCE = "https://www.datos.gov.co/resource/gdxc-w37w.json"
MGN_LAYER_URL = (
    "https://portalgis.dane.gov.co/mparcgis/rest/services/MGN2024/Serv_CapasMGN_2024"
    "/MapServer/317/query"
)
DEPARTMENT_CODES = ("08", "13", "20", "23", "44", "47", "70")
"""The Caribbean continental departments of docs/08 §M2 "Región" (D-T0.2)."""

EXPECTED_MUNICIPALITIES = 195
DIVIPOLA_SELECT = "cod_dpto,dpto,cod_mpio,nom_mpio,tipo_municipio,longitud,latitud"
MGN_SELECT = "mpio_cdpmp"


def divipola_params() -> dict[str, str]:
    return {
        "$select": DIVIPOLA_SELECT,
        "$where": "cod_dpto in ('" + "','".join(DEPARTMENT_CODES) + "')",
        "$order": "cod_mpio",
        "$limit": "5000",
    }


def mgn_params() -> dict[str, str]:
    return {
        "where": "dpto_ccdgo IN ('" + "','".join(DEPARTMENT_CODES) + "')",
        "outFields": MGN_SELECT,
        "returnGeometry": "false",
        "resultRecordCount": "500",
        "f": "geojson",
    }


def _decimal(value: Any) -> float:
    """DIVIPOLA writes decimals with a comma: `-74,815546`."""
    return float(str(value).strip().replace(",", "."))


def parse_divipola(payload: bytes) -> pd.DataFrame:
    """The saved DIVIPOLA response as `code, name, department_code, department_name, lat, lon`."""
    rows = json.loads(payload)
    frame = pd.DataFrame(
        [
            {
                "code": str(row["cod_mpio"]).strip().zfill(5),
                "name": str(row["nom_mpio"]).strip(),
                "department_code": str(row["cod_dpto"]).strip().zfill(2),
                "department_name": str(row["dpto"]).strip(),
                "lat": _decimal(row["latitud"]),
                "lon": _decimal(row["longitud"]),
            }
            for row in rows
        ]
    )
    return frame.sort_values("code", ignore_index=True)


def parse_mgn_codes(payload: bytes) -> set[str]:
    """The `mpio_cdpmp` set of the saved MGN layer 317 response."""
    collection = json.loads(payload)
    codes = (
        str(feature["properties"][MGN_SELECT]).strip().zfill(5)
        for feature in collection["features"]
    )
    return set(codes)


def assert_region(frame: pd.DataFrame, *, expected: int = EXPECTED_MUNICIPALITIES) -> None:
    """Fail unless the frame is the Caribbean region of docs/08 §M2 "Región"."""
    if len(frame) != expected:
        raise ValueError(
            f"expected {expected} municipalities of the Caribbean region, got {len(frame)}"
        )
    if frame["code"].duplicated().any():
        duplicates = sorted(frame.loc[frame["code"].duplicated(), "code"].unique())
        raise ValueError(f"duplicate municipality codes: {duplicates}")


def cross_check_codes(frame: pd.DataFrame, mgn_codes: set[str]) -> None:
    """Fail unless DIVIPOLA and MGN 2024 describe the same municipalities (D-T3.1)."""
    divipola = set(frame["code"])
    if divipola != mgn_codes:
        raise ValueError(
            "DIVIPOLA and MGN 2024 disagree on the region: "
            f"only in DIVIPOLA {sorted(divipola - mgn_codes)}, "
            f"only in MGN {sorted(mgn_codes - divipola)}"
        )
