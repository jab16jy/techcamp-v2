"""Municipality reference: DIVIPOLA seats, with MGN 2024 as the code control."""

import json
from collections.abc import Callable

import pytest

from techcamp_ml.sources.municipalities import (
    assert_region,
    cross_check_codes,
    parse_divipola,
    parse_mgn_codes,
)


def test_parse_divipola_reads_comma_decimals_and_five_digit_codes(
    fixture: Callable[[str], bytes],
) -> None:
    frame = parse_divipola(fixture("divipola_municipalities.json"))

    assert list(frame.columns) == [
        "code",
        "name",
        "department_code",
        "department_name",
        "lat",
        "lon",
    ]
    assert len(frame) == 8
    row = frame[frame["code"] == "08001"].iloc[0]
    assert row["name"] == "BARRANQUILLA"
    assert row["department_code"] == "08"
    assert row["department_name"] == "ATLÁNTICO"
    # DIVIPOLA writes decimals with a comma: a plain float() would raise here.
    assert row["lat"] == pytest.approx(10.977961)
    assert row["lon"] == pytest.approx(-74.815546)
    # Negative assertion: every code is the 5-digit DIVIPOLA form.
    assert all(len(str(code)) == 5 and code.isdigit() for code in frame["code"])
    assert all(
        4.0 < lat < 13.5 and -76.5 < lon < -70.0
        for lat, lon in zip(frame["lat"], frame["lon"], strict=True)
    )


def test_parse_mgn_codes_returns_the_control_set(fixture: Callable[[str], bytes]) -> None:
    codes = parse_mgn_codes(fixture("mgn317_municipios.geojson"))

    assert len(codes) == 8
    assert "08001" in codes
    assert "70823" in codes
    assert all(len(code) == 5 for code in codes)


def test_cross_check_passes_on_equal_code_sets(
    fixture: Callable[[str], bytes],
) -> None:
    frame = parse_divipola(fixture("divipola_municipalities.json"))
    cross_check_codes(frame, parse_mgn_codes(fixture("mgn317_municipios.geojson")))


def test_cross_check_fails_loudly_on_a_difference(
    fixture: Callable[[str], bytes],
) -> None:
    frame = parse_divipola(fixture("divipola_municipalities.json"))
    mgn = parse_mgn_codes(fixture("mgn317_municipios.geojson")) - {"08001"}

    with pytest.raises(ValueError, match="08001") as error:
        cross_check_codes(frame, mgn)
    # The negative assertion names the code that differs, not just "mismatch".
    assert "only in DIVIPOLA" in str(error.value)


def test_assert_region_rejects_a_region_that_is_not_the_caribbean(
    fixture: Callable[[str], bytes],
) -> None:
    frame = parse_divipola(fixture("divipola_municipalities.json"))
    assert_region(frame, expected=len(frame))

    with pytest.raises(ValueError, match="195"):
        assert_region(frame)
    assert json.loads(fixture("divipola_municipalities.json"))[0]["cod_dpto"] == "08"
