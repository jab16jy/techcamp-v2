"""E16 T1: the recorded weather fixtures validate, pin their bytes, and never
touch the network (docs/06-diseno-detallado.md:490, D-T16.3).

The last test is the one CI depends on: `docs/06:489` makes the seminar demo and
the scenario suite run offline, so a fixture that quietly reached for Open-Meteo
at test time would be a regression nobody notices until the demo.

What each fixture must *make happen* (scenario A's ETc, scenario E's day 16) is
asserted with the scenario YAMLs, in `test_scenario_library.py`: without a
trajectory and a soil block there is no arithmetic to check.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Iterator

import pytest

from .scenario_library import (
    FORECAST_DAYS,
    SCENARIO_NAMES,
    WEATHER_DIR,
    FixtureSchemaError,
    WeatherFixture,
    canonical_days_sha256,
    file_sha256,
    load_fixture,
    parse_fixture,
)

FIXTURE_NAMES = (
    "el-nino-caribe",
    "rainy-season-caribe",
    "normal-caribe",
    "veranillo-caribe",
)
"""Every fixture in the library: the two docs/06-diseno-detallado.md:430,458
names for the scenarios with documented arithmetic, plus the rainy-season and the
benign window the other scenarios read."""

MIN_FUNGAL_OBSERVED_DAYS = {
    "el-nino-caribe": 0,
    "rainy-season-caribe": 8,
    "normal-caribe": 0,
    "veranillo-caribe": 0,
}
"""How many observed rows each fixture needs to satisfy both halves of
`fungal_risk` (docs/06:129), which is what its scenario's `expected` block
depends on. Scenarios A, E and `normal` assert their alert list exhaustively, so
a humid recorded day would add an alert they do not list; scenario B is the
humidity scenario itself, where sustained humidity is the whole point
(docs/06:474)."""


@pytest.fixture(params=FIXTURE_NAMES)
def fixture(request: pytest.FixtureRequest) -> Iterator[WeatherFixture]:
    """Each named fixture, loaded through the contract rather than by hand."""
    yield load_fixture(request.param)


def test_the_library_holds_exactly_the_named_fixtures() -> None:
    found = sorted(path.stem for path in WEATHER_DIR.glob("*.json"))

    assert found == sorted(FIXTURE_NAMES)
    assert [path.suffix for path in WEATHER_DIR.iterdir()] == [".json"] * len(FIXTURE_NAMES)


def test_every_fixture_parses_and_carries_its_provenance(fixture: WeatherFixture) -> None:
    assert fixture.version == 1
    assert fixture.scenario in SCENARIO_NAMES
    assert fixture.timezone == "America/Bogota"
    assert fixture.source_url.startswith("https://archive-api.open-meteo.com/v1/archive?")
    assert fixture.recorded_at.tzinfo is not None
    # D-T16.3 record-first: these recordings reproduce the documented arithmetic,
    # so none of them needed the synthetic flag.
    assert fixture.synthetic is False
    assert len(fixture.observed) >= 14
    assert len(fixture.forecast) == FORECAST_DAYS


def test_each_fixture_pins_its_own_rows(fixture: WeatherFixture) -> None:
    """`data_sha256` covers the row array, so an edited measure cannot pass."""
    path = WEATHER_DIR / f"{fixture.name}.json"
    rows = json.loads(path.read_text(encoding="utf-8"))["days"]

    assert len(file_sha256(path)) == 64, "the byte digest is a sha256 hex string"
    assert canonical_days_sha256(rows) == fixture.data_sha256

    tampered = [dict(row) for row in rows]
    tampered[0]["et0_mm"] = tampered[0]["et0_mm"] + 0.01
    assert canonical_days_sha256(tampered) != fixture.data_sha256


def test_days_are_contiguous_from_day_zero(fixture: WeatherFixture) -> None:
    assert [day.day_offset for day in fixture.days] == list(range(len(fixture.days)))
    # A sliding window, so the shorter side is intentional.
    for previous, current in zip(fixture.days, fixture.days[1:], strict=False):
        assert (current.day - previous.day).days == 1
    # The forecast block is what the recommendation window reads, `[D, D+6]`.
    assert fixture.days[-FORECAST_DAYS].is_forecast is True


def test_measures_are_physically_plausible(fixture: WeatherFixture) -> None:
    for day in fixture.days:
        assert day.et0_mm >= 0.0
        assert day.rain_mm >= 0.0
        assert day.tmin_c <= day.tmax_c
        assert 0.0 <= day.rh_mean_pct <= 100.0


def test_veranillo_keeps_effective_rain_at_zero(fixture: WeatherFixture) -> None:
    """Scenario E: "ninguna lluvia > 5 mm en los 21 dias ni en los 7 de
    pronostico" (docs/06:458), which is what keeps Pe = 0 so Dr only grows."""
    if fixture.name != "veranillo-caribe":
        pytest.skip("scenario E's arithmetic")

    assert all(day.rain_mm <= 5.0 for day in fixture.days)
    assert all(day.effective_rain_mm == 0.0 for day in fixture.days)


def test_each_fixture_meets_its_own_fungal_risk_expectation(fixture: WeatherFixture) -> None:
    """Every fixture carries exactly the humidity its scenario's `expected` needs."""
    qualifying = [day for day in fixture.observed if day.meets_fungal_risk_conditions]
    minimum = MIN_FUNGAL_OBSERVED_DAYS[fixture.name]

    assert len(qualifying) >= minimum, (
        f"{fixture.name}: {len(qualifying)} observed rows satisfy fungal_risk, "
        f"scenario needs at least {minimum}"
    )
    # `heavy_rain_forecast` needs more than 50 mm in a forecast 24 h
    # (docs/06:131); no scenario here expects that alert.
    assert all(day.rain_mm <= 50.0 for day in fixture.forecast)


def test_normal_fixture_keeps_effective_rain_at_zero(fixture: WeatherFixture) -> None:
    """The benign window: no effective rain, so the `normal`, `node-failure` and
    `offline-farmer` plots dry down only along their own trajectory."""
    if fixture.name != "normal-caribe":
        pytest.skip("the contrast fixture's arithmetic")

    assert all(day.effective_rain_mm == 0.0 for day in fixture.days)


def test_parse_fixture_rejects_an_edited_row() -> None:
    """The contract must fail loudly on tampering, not only on a missing file."""
    path = WEATHER_DIR / "el-nino-caribe.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["days"][0]["et0_mm"] = raw["days"][0]["et0_mm"] + 0.01

    with pytest.raises(FixtureSchemaError, match="data_sha256"):
        parse_fixture("el-nino-caribe", raw)


def test_parse_fixture_rejects_an_unknown_key() -> None:
    path = WEATHER_DIR / "el-nino-caribe.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["extra"] = 1

    with pytest.raises(FixtureSchemaError, match="unknown key"):
        parse_fixture("el-nino-caribe", raw)


def test_loading_the_library_opens_no_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """docs/06:489 — fixtures are recorded once and the demo runs offline."""

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("the fixture library must not touch the network")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for path in sorted(WEATHER_DIR.glob("*.json")):
        load_fixture(path.stem)
