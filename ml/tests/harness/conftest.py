"""A small M2 table over the real label window, two departments, some floods.

The window is docs/08 §Fuentes de datos de M2's own (2019-01 → 2025-12, D-T3.2) so the
split constants are exercised against the months they will really see. Four municipalities
keep the unit test fast; nothing here needs the 195 of the data card, because the split
reads months and departments, not the size of the region.
"""

from __future__ import annotations

import pandas as pd

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


def flood_table() -> pd.DataFrame:
    """Every municipality × month of the window, `label` on the flood months."""
    rows: list[dict[str, object]] = []
    for code, department_code, department_name in MUNICIPALITIES:
        for month, year, number in MONTHS:
            rows.append(
                {
                    "code": code,
                    "department_code": department_code,
                    "department_name": department_name,
                    "year": year,
                    "month": number,
                    "horizon_start": pd.Timestamp(month + "-01"),
                    "precip_sum_6m": float(number) / 2.0,
                    "label": int(month in FLOOD_MONTHS),
                }
            )
    return pd.DataFrame(rows).sort_values(["code", "horizon_start"]).reset_index(drop=True)
