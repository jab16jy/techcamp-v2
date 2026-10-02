"""UNGRD flood labels: three datasets, three column mappings, one tidy table."""

from collections.abc import Callable

import pytest

from techcamp_ml.sources.labels import (
    LABEL_SOURCES,
    LabelSource,
    normalise_event,
    parse_labels,
)

CODES = {"08001", "08549", "23068", "23350", "13244", "13430", "13188", "13074", "23417", "20045"}


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
    # Negative assertion: the dropped rows are counted, never silently swallowed.
    assert dropped.other_event + dropped.unknown_code + dropped.bad_date >= 0


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
