"""A small M2 table over the real label window, two departments, some floods.

The window is docs/08 §Fuentes de datos de M2's own (2019-01 → 2025-12, D-T3.2) so the
split constants are exercised against the months they will really see. Four municipalities
keep the unit test fast; nothing here needs the 195 of the data card, because the split
reads months and departments, not the size of the region.
"""

from __future__ import annotations

import math

import pandas as pd
from techcamp.risk.domain.features import FEATURE_NAMES

WINDOW_FIRST = "2019-01"
WINDOW_LAST = "2025-12"

MONTHS: list[tuple[str, int, int]] = [
    (f"{year}-{month:02d}", year, month) for year in range(2019, 2026) for month in range(1, 13)
]

MUNICIPALITIES = (
    ("08001", "08", "Atlántico"),
    ("08002", "08", "Atlántico"),
    ("13001", "13", "Bolívar"),
    ("13002", "13", "Bolívar"),
)

FLOOD_MONTHS = ("2020-05", "2022-08", "2023-10", "2025-04")
"""One flooded month per year in the window, so every block of the split carries positives
and a prevalence of 1/84 ≈ 0.012 rather than the real 0.092: the harness never assumes a
frequency, it only refuses to change one (docs/08 §Reglas de gobierno, "Frecuencia real")."""


def features(month: int, flooded: bool) -> dict[str, float | None]:
    """The shared feature vector of docs/08 §M2 "Features", for one issue month.

    A flooded month gets a wet `precip_sum_1m`, because that is the signal docs/08 §M2
    "Features" says the model sees — accumulated rainfall of one to six months — and it is
    what lets a test scorer rank the flooded rows above the dry ones from the features
    alone, the way a fitted estimator would.

    The anomalies are null on purpose: T4 leaves them null because the climatology is the
    split's (data card §Contrato de columnas) and this split is T5's. A fixture that filled
    them would test a column the real table does not carry yet.
    """
    values: dict[str, float | None] = {name: None for name in FEATURE_NAMES}
    for name in FEATURE_NAMES:
        if name.startswith("precip_sum"):
            values[name] = 40.0 if (name == "precip_sum_1m" and flooded) else float(month) / 2.0
        elif name.startswith("soil_moisture"):
            values[name] = 0.4
        elif name in ("elevation_m", "slope_deg"):
            values[name] = 30.0
    values.update(
        {
            "month_sin": round(math.sin(2 * math.pi * month / 12), 6),
            "month_cos": round(math.cos(2 * math.pi * month / 12), 6),
        }
    )
    return values


def flood_table() -> pd.DataFrame:
    """Every municipality × month of the window, `label` on the flood months."""
    rows: list[dict[str, object]] = []
    for code, department_code, department_name in MUNICIPALITIES:
        for month, year, number in MONTHS:
            flooded = month in FLOOD_MONTHS
            rows.append(
                {
                    "code": code,
                    "department_code": department_code,
                    "department_name": department_name,
                    "year": year,
                    "month": number,
                    "horizon_start": pd.Timestamp(month + "-01"),
                    **features(number, flooded),
                    "label": int(flooded),
                }
            )
    return pd.DataFrame(rows).sort_values(["code", "horizon_start"]).reset_index(drop=True)
