"""E16 T1: the scenario contract rejects every shape the library must never accept.

The `expected` block is why this suite exists. docs/06-diseno-detallado.md:485 turns
every scenario into an end-to-end test, so a scenario whose oracle names a rule code,
an irrigation kind or an advice code the system does not have would be an e2e that can
never pass — and the failure would land in T5's Playwright run, hours from the typo
that caused it. Here it fails in the same commit.

Every case builds its own scenario dict instead of loading a real YAML, so the
contract is proved against inputs the library does not contain. The scenarios
themselves are asserted in `test_scenario_library.py`, against the arithmetic
docs/06-diseno-detallado.md:442-471 promises.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from .scenario_library import SCENARIOS_DIR, WEATHER_DIR, file_sha256
from .scenario_schema import MAX_NODES, ScenarioSchemaError, parse_scenario

FIXTURE = "el-nino-caribe.json"
"""The recording scenario A pins; present since U1, so its digest is real here."""

RAINFED_FIXTURE = "veranillo-caribe.json"
"""The recording scenario E pins (docs/06-diseno-detallado.md:458)."""


def recording_digest(name: str) -> str:
    """The bytes digest of a recorded fixture, or a clearly-named failure.

    Both accept-path cases below pin a real recording (R3-001). Calling
    `file_sha256` directly meant a missing file raised `FileNotFoundError` from a
    module-level helper, which errored the whole file — including the 21 tests
    that assert nothing about this fixture. Failing the one test that needs the
    recording keeps the contract's own coverage intact when a fixture is absent.
    """
    path = WEATHER_DIR / name
    if not path.exists():
        pytest.fail(
            f"{name} is missing from the recorded library; the scenario contract pins "
            "real recordings (D-T16.3), so this case cannot run until U1/U1b lands it"
        )
    return file_sha256(path)


def a_valid_scenario() -> dict[str, Any]:
    """The smallest scenario the contract must accept, shaped like scenario A."""
    return {
        "name": "el-nino",
        "description": "Estrés hídrico por El Niño en maíz (escenario A)",
        "plot": {
            "crop": "maize",
            "sown_days_ago": 42,
            "area_ha": 1.5,
            "soil": "sandy_loam",
            "irrigation_system": "drip",
        },
        "soil": {"field_capacity_pct": 23, "wilting_point_pct": 9},
        "nodes": {"count": 2, "soil_moisture_depth_cm": 30, "calibration": "field"},
        "interval_s": 900,
        "weather_fixture": FIXTURE,
        "weather_fixture_sha256": recording_digest(FIXTURE),
        "trajectories": {
            "soil_moisture_30cm": {"start": 22, "end": 13, "noise": 0.8},
            "air_temp": {"daily_min": 26, "daily_max": 37, "noise": 0.5},
            "air_rh": {"daily_min": 38, "daily_max": 70},
        },
        "faults": [{"at_day": 10, "node": 2, "type": "offline", "hours": 8}],
        "expected": {"alerts": ["water_stress"], "irrigation": "irrigate"},
    }


def corrupt(**changes: Any) -> dict[str, Any]:
    """A valid scenario with one nested path replaced; `{'soil': {...}}` merges."""
    scenario = a_valid_scenario()
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(scenario[key], dict):
            scenario[key] = scenario[key] | value
        else:
            scenario[key] = value
    return scenario


def test_the_contract_accepts_a_scenario_shaped_like_the_docs() -> None:
    scenario = parse_scenario("el-nino", a_valid_scenario())

    assert scenario.name == "el-nino"
    assert scenario.nodes.reads_soil
    assert scenario.trajectory("soil_moisture_30cm") == {"start": 22.0, "end": 13.0, "noise": 0.8}
    assert scenario.faults[0].hours == 8
    assert scenario.alerts == ("water_stress",)


def test_the_contract_accepts_the_node_less_rainfed_shape() -> None:
    """docs/06-diseno-detallado.md:457: scenario E provisions no node, so the balance
    runs on weather alone with `K = 0` — no trajectories, no depth, no calibration."""
    scenario = parse_scenario(
        "dry-spell-rainfed",
        {
            "name": "dry-spell-rainfed",
            "description": "Veranillo durante la floración en maíz de secano (escenario E)",
            "plot": {
                "crop": "maize",
                "sown_days_ago": 60,
                "area_ha": 2,
                "soil": "sandy_loam",
                "irrigation_system": "none",
            },
            "soil": {"field_capacity_pct": 23, "wilting_point_pct": 9},
            "nodes": {"count": 0},
            "interval_s": 900,
            "weather_fixture": "veranillo-caribe.json",
            "weather_fixture_sha256": recording_digest(RAINFED_FIXTURE),
            "expected": {
                "alerts": ["water_stress"],
                "irrigation": "rainfed",
                "advice": ["conserve_moisture"],
            },
        },
    )

    assert not scenario.nodes.reads_soil
    assert scenario.plot.is_rainfed
    assert scenario.trajectories == {}
    assert scenario.faults == ()
    assert scenario.expected.advice == ("conserve_moisture",)


def test_it_rejects_a_filename_that_does_not_match_its_name() -> None:
    with pytest.raises(ScenarioSchemaError, match="name must match the filename"):
        parse_scenario("rainy-season", a_valid_scenario())


def test_it_rejects_an_unknown_expected_alert_code() -> None:
    with pytest.raises(ScenarioSchemaError, match=r"unknown code\(s\) \['not_a_rule'\]"):
        parse_scenario("el-nino", corrupt(expected={"alerts": ["water_stress", "not_a_rule"]}))


def test_it_rejects_an_unknown_irrigation_kind() -> None:
    with pytest.raises(ScenarioSchemaError, match="unknown kind"):
        parse_scenario("el-nino", corrupt(expected={"irrigation": "flood_everything"}))


def test_it_rejects_an_unknown_advice_code() -> None:
    with pytest.raises(ScenarioSchemaError, match="unknown code"):
        parse_scenario("el-nino", corrupt(expected={"advice": ["pray_for_rain"]}))


def test_it_rejects_a_repeated_expected_code() -> None:
    """A duplicate in the oracle would make the e2e's assertion ambiguous."""
    with pytest.raises(ScenarioSchemaError, match="repeated code"):
        parse_scenario("el-nino", corrupt(expected={"alerts": ["water_stress", "water_stress"]}))


def test_it_rejects_a_fault_on_a_node_that_does_not_exist() -> None:
    with pytest.raises(ScenarioSchemaError, match="does not exist"):
        parse_scenario(
            "el-nino", corrupt(faults=[{"at_day": 3, "node": 9, "type": "offline", "hours": 8}])
        )


def test_it_rejects_a_fault_type_it_does_not_know() -> None:
    with pytest.raises(ScenarioSchemaError, match="unknown type"):
        parse_scenario(
            "el-nino", corrupt(faults=[{"at_day": 3, "node": 1, "type": "meltdown", "hours": 8}])
        )


def test_it_rejects_an_edited_fixture_pin() -> None:
    """D-T16.3's pin is the only thing standing between the scenario and a recording
    that no longer reproduces its arithmetic."""
    with pytest.raises(ScenarioSchemaError, match="weather_fixture_sha256 does not match"):
        parse_scenario("el-nino", corrupt(weather_fixture_sha256="0" * 64))


def test_it_rejects_a_soil_block_missing_a_key() -> None:
    scenario = a_valid_scenario()
    del scenario["soil"]["wilting_point_pct"]

    with pytest.raises(ScenarioSchemaError, match="missing key"):
        parse_scenario("el-nino", scenario)


def test_it_rejects_a_soil_where_wilting_point_is_above_field_capacity() -> None:
    """θWP ≥ θFC inverts the whole balance: θ_estrés would come out above θFC and no
    day could ever cross it."""
    with pytest.raises(ScenarioSchemaError, match="must be below field_capacity_pct"):
        parse_scenario("el-nino", corrupt(soil={"field_capacity_pct": 9, "wilting_point_pct": 23}))


def test_it_rejects_a_ramp_shaped_like_an_air_channel() -> None:
    """A ramp is what crosses θ_estrés at a computable day; an air channel has a
    band. Mixing them makes the crossing unreproducible."""
    with pytest.raises(ScenarioSchemaError, match="air channel is a band"):
        parse_scenario(
            "el-nino", corrupt(trajectories={"air_temp": {"start": 26, "end": 30, "noise": 0.5}})
        )


def test_it_rejects_a_trajectory_that_mixes_a_ramp_and_a_band() -> None:
    with pytest.raises(ScenarioSchemaError, match="not both"):
        parse_scenario(
            "el-nino",
            corrupt(trajectories={"soil_moisture_30cm": {"start": 22, "end": 13, "daily_min": 1}}),
        )


def test_it_rejects_a_soil_ramp_that_never_moves() -> None:
    with pytest.raises(ScenarioSchemaError, match="not a trajectory"):
        parse_scenario(
            "el-nino",
            corrupt(trajectories={"soil_moisture_30cm": {"start": 20, "end": 20, "noise": 0.8}}),
        )


def test_it_rejects_a_nodes_with_a_trajectory() -> None:
    """`K = 0` means the balance never reads a sensor, so a trajectory on a node-less
    plot would be a curve nothing publishes."""
    scenario = a_valid_scenario() | {
        "nodes": {"count": 0},
        "trajectories": {"soil_moisture_30cm": {"start": 22, "end": 13, "noise": 0.8}},
    }

    with pytest.raises(ScenarioSchemaError, match="must not"):
        parse_scenario("el-nino", scenario)


def test_it_rejects_a_plot_with_nodes_but_no_trajectory() -> None:
    scenario = a_valid_scenario()
    del scenario["trajectories"]

    with pytest.raises(ScenarioSchemaError, match="must declare its trajectories"):
        parse_scenario("el-nino", scenario)


def test_it_rejects_nodes_without_a_depth() -> None:
    """A node that reads no depth publishes no `soil_moisture_*` ramp, so the balance
    would have a sensor it cannot place in the root zone."""
    scenario = a_valid_scenario()
    scenario["nodes"] = {"count": 2, "calibration": "field"}

    with pytest.raises(ScenarioSchemaError, match="missing key"):
        parse_scenario("el-nino", scenario)


def test_it_rejects_more_nodes_than_the_room_holds() -> None:
    with pytest.raises(ScenarioSchemaError, match="exceeds"):
        parse_scenario(
            "el-nino",
            corrupt(
                nodes={"count": MAX_NODES + 1, "soil_moisture_depth_cm": 30, "calibration": "field"}
            ),
        )


def test_it_rejects_a_bool_where_a_number_belongs() -> None:
    """YAML reads `on`, `yes` and `true` as booleans, so a mistyped threshold would
    otherwise pass as a number and compute silently."""
    with pytest.raises(ScenarioSchemaError, match="expected a number"):
        parse_scenario("el-nino", corrupt(interval_s=True))


def test_it_rejects_an_interval_finer_than_the_completeness_window() -> None:
    with pytest.raises(ScenarioSchemaError, match="is below 60"):
        parse_scenario("el-nino", corrupt(interval_s=30))


def test_it_rejects_an_unknown_top_level_key() -> None:
    with pytest.raises(ScenarioSchemaError, match="unknown key"):
        parse_scenario("el-nino", corrupt(prize="university"))


def test_the_library_files_are_not_yaml_with_a_second_document() -> None:
    """`safe_load` reads the first document and ignores the rest, so a scenario file
    carrying two documents would silently lose its half."""
    for path in sorted(SCENARIOS_DIR.glob("*.yaml")) if SCENARIOS_DIR.exists() else []:
        documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        assert len(documents) == 1, f"{path.name} carries more than one YAML document"
