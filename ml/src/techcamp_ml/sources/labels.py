"""UNGRD flood labels: three Socrata datasets, three column mappings, one table.

Every year ships different columns, so the mapping lives here per dataset
(docs/08 §Fuentes de datos de M2: "un mapeo por dataset"), and each download is
filtered server-side on the year window it owns. Sources are never mixed inside a
year: 2019-2022 from `wwkg-r6te`, 2023-2024 from `rgre-6ak4`, 2025+ from `2343-nuqp`.

Raw values are not clean even inside one dataset: `wwkg-r6te` holds `INUNDACIÓN`,
`INUNDACIoN` and `Creciente Subita` next to the canonical spellings (verified
2026-10-02), which is why the event class is folded before it is matched.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

SOCRATA_RESOURCE = "https://www.datos.gov.co/resource"
EVENT_CLASSES = ("INUNDACION", "CRECIENTE SUBITA", "AVENIDA TORRENCIAL")
"""The three classes of docs/08 §Fuentes de datos de M2; anything else is not a flood."""

SOCRATA_PAGE = 50_000
COLUMNS = ["code", "date", "event_class", "source_dataset", "source_row_id"]


@dataclass(frozen=True, slots=True)
class LabelSource:
    """One consolidated dataset and the columns it uses."""

    dataset: str
    code_column: str
    year_from: int
    year_to: int | None


@dataclass(frozen=True, slots=True)
class Dropped:
    """Rows the parser refused, and why. Missing evidence is never silently zero."""

    other_event: int = 0
    unknown_code: int = 0
    bad_date: int = 0
    outside_window: int = 0

    def __add__(self, other: Dropped) -> Dropped:
        return Dropped(
            self.other_event + other.other_event,
            self.unknown_code + other.unknown_code,
            self.bad_date + other.bad_date,
            self.outside_window + other.outside_window,
        )


LABEL_SOURCES: tuple[LabelSource, ...] = (
    LabelSource("wwkg-r6te", "divipola", 2019, 2022),
    LabelSource("rgre-6ak4", "codificaci_n_segun_divipola", 2023, 2024),
    LabelSource("2343-nuqp", "codificaci_n_segun_divipola", 2025, None),
)


def normalise_event(value: Any) -> str:
    """Upper case without accents: `INUNDACIÓN` and `creciente súbita` both fold."""
    decomposed = unicodedata.normalize("NFKD", str(value).strip().upper())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def label_params(source: LabelSource, *, offset: int = 0) -> dict[str, str]:
    """The SoQL query for one dataset, one page.

    The window is half-open (`>= start and < end`): `between` includes both ends, so an
    inclusive end would pull the rows stamped the first midnight of the *next* dataset's
    window, and a year is never assembled from two sources (docs/08 §Fuentes de datos de
    M2). A dataset with no `year_to` has no upper bound at all: a cap the owner never
    named would leave its later rows silently out.

    The event filter is a loose pattern on purpose: the raw values carry accents and
    odd casing, and an exact `in (...)` would silently drop the variants.
    """
    window = f"fecha >= '{source.year_from}-01-01T00:00:00'"
    if source.year_to is not None:
        window += f" and fecha < '{source.year_to + 1}-01-01T00:00:00'"
    stems = ("INUNDACI", "CRECIENTE", "AVENIDA")
    events = " or ".join(f"upper(evento) like '%{stem}%'" for stem in stems)
    return {
        "$select": f":id,fecha,evento,{source.code_column}",
        "$where": f"{window} and ({events})",
        # Paging walks an offset, so the order has to be total: `fecha` repeats, `:id`
        # does not, and a row that moves between pages is a row that is skipped.
        "$order": "fecha,:id",
        "$limit": str(SOCRATA_PAGE),
        "$offset": str(offset),
    }


def parse_labels(
    payload: bytes,
    source: LabelSource,
    codes: Collection[str],
) -> tuple[pd.DataFrame, Dropped]:
    """One row per flood report inside the region, plus the rows it refused."""
    rows: list[dict[str, Any]] = []
    other_event = unknown_code = bad_date = outside_window = 0
    for record in json.loads(payload):
        event_class = normalise_event(record.get("evento", ""))
        if event_class not in EVENT_CLASSES:
            other_event += 1
            continue
        code = _code_of(record.get(source.code_column), codes)
        if code is None:
            unknown_code += 1
            continue
        try:
            day = date.fromisoformat(str(record["fecha"])[:10])
        except (KeyError, ValueError):
            bad_date += 1
            continue
        # The query already filters on the window, but a downloaded page is evidence in
        # its own right: a row outside the years its dataset owns belongs to another one.
        if day.year < source.year_from or (
            source.year_to is not None and day.year > source.year_to
        ):
            outside_window += 1
            continue
        rows.append(
            {
                "code": code,
                "date": day,
                "event_class": event_class,
                "source_dataset": source.dataset,
                "source_row_id": str(record.get(":id", "")),
            }
        )
    frame = pd.DataFrame(rows, columns=COLUMNS)
    if not frame.empty:
        frame["date"] = pd.to_datetime(frame["date"])
    return frame, Dropped(other_event, unknown_code, bad_date, outside_window)


def _code_of(value: Any, codes: Collection[str]) -> str | None:
    """The 5-digit DIVIPOLA code, or `None` when it is outside the region.

    `wwkg-r6te` stores DIVIPOLA as a number, so the leading zero of codes below 10000
    is gone (docs/08 §Fuentes de datos de M2).
    """
    digits = "".join(character for character in str(value) if character.isdigit())
    code = digits.zfill(5)
    return code if len(code) == 5 and code in codes else None
