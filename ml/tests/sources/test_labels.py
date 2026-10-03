"""UNGRD flood labels: three datasets, three column mappings, one tidy table."""

import json
from collections.abc import Callable

import pytest

from techcamp_ml.sources.labels import (
    LABEL_SOURCES,
    LabelSource,
    label_params,
    normalise_event,
    parse_labels,
)

CODES = {"08001", "08549", "23068", "23350", "13244", "13430", "13188", "13074", "23417", "20045"}
EXPECTED_DROPPED = {"wwkg-r6te": 1, "rgre-6ak4": 1, "2343-nuqp": 3}
"""How many rows each recorded fixture loses to a code outside the region: the exact
counts, so a miscount cannot pass as a lower bound."""


def test_the_query_window_is_half_open_and_owned_by_its_dataset() -> None:
    where = {source.dataset: label_params(source)["$where"] for source in LABEL_SOURCES}

    # `between` includes both ends, so a 2019-2022 query would also pull the rows stamped
    # 2023-01-01T00:00:00, which are the first day of the next dataset's window.
    assert (
        "fecha >= '2019-01-01T00:00:00' and fecha < '2023-01-01T00:00:00'" in (where["wwkg-r6te"])
    )
    assert (
        "fecha >= '2023-01-01T00:00:00' and fecha < '2025-01-01T00:00:00'" in (where["rgre-6ak4"])
    )
    # The negative halves: no inclusive keyword, and no year the owner never named. The
    # open dataset is `2343-nuqp` (2025 en adelante, docs/08 §Fuentes de datos de M2),
    # so a cap at 2027 would leave its later rows silently out.
    assert all("between" not in clause for clause in where.values())
    assert "fecha >= '2025-01-01T00:00:00'" in where["2343-nuqp"]
    assert all("2027" not in clause for clause in where.values())


def test_the_pages_are_ordered_by_a_column_that_is_not_repeated() -> None:
    # Offset paging over a non-unique order skips and repeats rows across pages.
    assert label_params(LABEL_SOURCES[0])["$order"] == "fecha,:id"


def test_normalise_event_folds_case_and_accents() -> None:
    assert normalise_event("creciente súbita") == "CRECIENTE SUBITA"
    assert normalise_event("INUNDACIÓN") == "INUNDACION"
    assert normalise_event("  Avenida Torrencial ") == "AVENIDA TORRENCIAL"
    # The negative case: a non-event never collapses into one of the three classes.
    assert normalise_event("Incendio estructural") == "INCENDIO ESTRUCTURAL"


@pytest.mark.parametrize("source", LABEL_SOURCES, ids=lambda source: source.dataset)
def test_parse_labels_maps_each_dataset_to_the_same_columns(
    fixture: Callable[[str], bytes],
    source: LabelSource,
) -> None:
    frame, dropped = parse_labels(
        fixture(f"ungrd_{source.dataset}.json"),
        source,
        CODES,
    )

    assert list(frame.columns) == ["code", "date", "event_class", "source_dataset", "source_row_id"]
    assert not frame.empty
    assert set(frame["source_dataset"]) == {source.dataset}
    assert set(frame["code"]) <= CODES
    assert set(frame["event_class"]) <= {"INUNDACION", "CRECIENTE SUBITA", "AVENIDA TORRENCIAL"}
    assert frame["source_row_id"].str.startswith("row-").all()
    assert not frame.duplicated(subset=["source_row_id"]).any()
    assert (frame["date"].astype(str).str.startswith(str(source.year_from))).all()
    # Negative assertion: the rows refused are counted exactly, and nothing else is
    # dropped: the fixture holds five rows and every one is either kept or counted.
    assert dropped.unknown_code == EXPECTED_DROPPED[source.dataset]
    assert (dropped.other_event, dropped.bad_date, dropped.outside_window) == (0, 0, 0)
    assert len(frame) + dropped.unknown_code == 5


def test_a_row_outside_the_window_its_dataset_owns_is_dropped_and_counted(
    fixture: Callable[[str], bytes],
) -> None:
    payload = json.loads(fixture("ungrd_wwkg-r6te.json"))
    # The boundary day the inclusive query used to pull into this dataset.
    boundary = dict(payload[0], **{":id": "row-boundary", "fecha": "2023-01-01T00:00:00.000"})
    payload.append(boundary)

    frame, dropped = parse_labels(json.dumps(payload).encode(), LABEL_SOURCES[0], CODES)

    assert dropped.outside_window == 1
    assert "row-boundary" not in set(frame["source_row_id"])
    # Negative half: a row inside the window survives the same parser.
    assert len(frame) + dropped.unknown_code + dropped.outside_window == len(payload)


def test_the_open_dataset_keeps_its_rows_past_2027(fixture: Callable[[str], bytes]) -> None:
    payload = json.loads(fixture("ungrd_2343-nuqp.json"))
    late = dict(payload[0], **{":id": "row-late", "fecha": "2027-06-01T00:00:00.000"})
    payload.append(late)

    frame, dropped = parse_labels(json.dumps(payload).encode(), LABEL_SOURCES[2], CODES)

    assert "row-late" in set(frame["source_row_id"])
    assert frame.loc[frame["source_row_id"] == "row-late", "date"].iloc[0].year == 2027
    # Negative half: the year above the window of a closed dataset is still refused.
    closed = {
        ":id": "row-late",
        "fecha": "2027-06-01T00:00:00.000",
        "evento": "INUNDACION",
        "divipola": "8549",
    }
    assert (
        parse_labels(json.dumps([closed]).encode(), LABEL_SOURCES[0], CODES)[1].outside_window == 1
    )


def test_wwkg_divipola_is_padded_back_to_five_digits(fixture: Callable[[str], bytes]) -> None:
    frame, _ = parse_labels(
        fixture("ungrd_wwkg-r6te.json"),
        LABEL_SOURCES[0],
        CODES,
    )

    assert "08549" in set(frame["code"]), "the source drops the leading zero of the DIVIPOLA code"
    assert "8549" not in set(frame["code"])


def test_a_code_outside_the_region_is_dropped_and_counted(
    fixture: Callable[[str], bytes],
) -> None:
    frame, dropped = parse_labels(
        fixture("ungrd_2343-nuqp.json"),
        LABEL_SOURCES[2],
        CODES,
    )

    assert dropped.unknown_code >= 1, "20050, 20054 and 25126 are outside the Caribbean region"
    assert "25126" not in set(frame["code"])
    assert len(frame) + dropped.unknown_code + dropped.other_event + dropped.bad_date == 5


def test_a_bad_date_is_counted_and_not_guessed(fixture: Callable[[str], bytes]) -> None:
    import json

    payload = json.loads(fixture("ungrd_rgre-6ak4.json"))
    payload[0]["fecha"] = "no-es-una-fecha"

    frame, dropped = parse_labels(json.dumps(payload).encode(), LABEL_SOURCES[1], CODES)

    assert dropped.bad_date == 1
    assert len(frame) == len(payload) - dropped.bad_date - dropped.unknown_code
    assert frame["date"].notna().all()
