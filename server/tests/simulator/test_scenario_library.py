"""E16 T1/U3: the six scenario YAMLs exist, and each one makes the thing
`docs/06-diseno-detallado.md:473-480` says it demonstrates.

The format itself is proved in `test_scenario_schema.py`, against scenario dicts
the library does not contain. This file asserts the real files: that the library
holds exactly the six, that each pins the recording it was built against, and that
the two scenarios with documented arithmetic reproduce their documented numbers.

Those two are the seminar's promise, so they are asserted as arithmetic and not as
their own YAML:

- **Scenario A** (`el-nino`, docs/06-diseno-detallado.md:442-448) drops from 22 %
  to 13 % over a 14-day backfill, crossing θ_estrés = 15,3 % on day 10,4, six
  hours before `water_stress` opens, and ends at Dr 60 mm against RAW 46,2 mm.
- **Scenario E** (`dry-spell-rainfed`, docs/06-diseno-detallado.md:465-471) has no
  node and no rain, so Dr grows by ETc ≈ 5 mm a day, passes RAW = 77 mm on day 16
  and goes critical 48 h later, on day 18.

Scenario B is the humidity one; the other three are `normal`, a node failure and
the offline farmer. Each `expected` list is exhaustive, so this file also proves no
scenario asserts a `fungal_risk` its own recording cannot open, and that every
scenario's alert list is reachable from the recording and trajectories it ships with.

The last test is the one CI depends on: docs/06:489 makes the suite and the demo run
offline, so the library may not reach for Open-Meteo at read time.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator

import pytest

from techcamp.simulator.scenario_library import SCENARIO_NAMES, SCENARIOS_DIR, load_fixture
from techcamp.simulator.scenario_schema import Scenario, load_all_scenarios, load_scenario

MAIZE_P = 0.55
"""maize's depletion fraction `p` from the crop catalog (FAO-56 Table 22)."""

MAIZE_MID_KC = 1.20
"""Kc of the mid stage, which scenario E sits in (docs/06-diseno-detallado.md:465)."""

STRESS_THRESHOLD_PCT = 15.3
"""θ_estrés = 23 − 0,55 × (23 − 9) = 15,3 % (docs/06-diseno-detallado.md:444)."""

BACKFILL_DAYS = 14
"""docs/06-diseno-detallado.md:411,446: the backfill is 14 days for scenario A."""

TAW_A_MM = 1000 * 0.14 * 0.6
RAW_A_MM = 0.55 * TAW_A_MM
"""Scenario A: Zr ≈ 0,6 m, so TAW 84 mm and RAW 46,2 mm (docs/06-diseno-detallado.md:445)."""

TAW_E_MM = 1000 * 0.14 * 1.0
RAW_E_MM = 0.55 * TAW_E_MM
"""Scenario E: Zr = 1,0 m at flowering, so TAW 140 mm and RAW 77 mm
(docs/06-diseno-detallado.md:468)."""

SCENARIO_A_CROSSING_DAY = 10.4
"""14 × (22 − 15,3) / (22 − 13) days in (docs/06-diseno-detallado.md:446)."""

SCENARIO_E_STRESS_DAY = 16
SCENARIO_E_CRITICAL_DAY = 18
"""Dr passes RAW on day 16 and the open alert is critical 48 h later
(docs/06-diseno-detallado.md:470)."""

FUNGAL_RH_PCT = 85.0
FUNGAL_TEMP_RANGE_C = (20.0, 30.0)
"""Both halves of `fungal_risk` (docs/06-diseno-detallado.md:129, alerts/adapters/seed.py)."""


@pytest.fixture(scope="module")
def library() -> tuple[Scenario, ...]:
    """Every scenario, parsed once: they are files, so parsing is pure I/O to share."""
    return load_all_scenarios()


@pytest.fixture(params=SCENARIO_NAMES)
def scenario(request: pytest.FixtureRequest) -> Iterator[Scenario]:
    """Each scenario in turn, loaded through the contract rather than by hand."""
    yield load_scenario(request.param)


def test_the_library_holds_exactly_the_six_documented_scenarios() -> None:
    """docs/06-diseno-detallado.md:473-480 names six; a seventh or a rename is an
    epic change, not a YAML addition."""
    found = sorted(path.stem for path in SCENARIOS_DIR.glob("*.yaml"))

    assert found == sorted(SCENARIO_NAMES)
    assert [scenario.name for scenario in load_all_scenarios()] == list(SCENARIO_NAMES)


def test_every_scenario_names_its_own_file_and_reads_a_presenter_sentence(
    scenario: Scenario,
) -> None:
    assert scenario.name == scenario.path.stem
    assert scenario.path.exists()
    # docs/06-diseno-detallado.md:424 is the one Spanish sentence of the format.
    assert len(scenario.description) > 15
    assert any("á" <= character.lower() <= "ú" for character in scenario.description)


def test_every_scenario_is_a_maize_plot_on_sandy_loam(scenario: Scenario) -> None:
    """docs/06-diseno-detallado.md:473-480 is six maize scenarios; the two with
    arithmetic state 23 / 9 % explicitly (docs/06:427, :456)."""
    assert scenario.plot.crop == "maize"
    assert scenario.plot.soil == "sandy_loam"
    assert scenario.plot.area_ha > 0.0
    assert 0 <= scenario.plot.sown_days_ago < 90, "maize's catalog cycle is 90 days"
    assert scenario.soil.field_capacity_pct == 23.0
    assert scenario.soil.wilting_point_pct == 9.0

    threshold = scenario.soil.stress_threshold_pct_with(MAIZE_P)
    assert threshold == pytest.approx(STRESS_THRESHOLD_PCT, abs=0.05)


def test_every_scenario_pins_a_recording_it_reproduces(scenario: Scenario) -> None:
    """D-T16.3: the pin covers the recording's bytes, and the recording is real
    (docs/06:489 — record first, the demo runs offline)."""
    fixture = load_fixture(scenario.weather_fixture.removesuffix(".json"))

    assert len(scenario.weather_fixture_sha256) == 64
    assert scenario.weather_fixture_path.exists()
    assert fixture.observed, "the balance starts on scenario day 0"
    assert fixture.forecast, "the recommendation reads a forecast window (docs/06:246)"
    assert fixture.synthetic is False, (
        "these recordings reproduce the documented arithmetic; the synthetic flag is a "
        "last resort (D-T16.3)"
    )


def test_every_scenario_declares_the_interval_completeness_counts_against(
    scenario: Scenario,
) -> None:
    """docs/03-modelo-datos.md:145 makes `interval_s` the base of expected readings,
    so a scenario that omits it or states a nonsense one cannot be judged complete."""
    assert scenario.interval_s == 900, (
        "the simulator's default interval (simulator/provision.py DEFAULT_INTERVAL_S)"
    )


def test_a_rainfed_plot_takes_a_rainfed_recommendation(scenario: Scenario) -> None:
    """`none` is a legal `irrigation_system` and the domain refuses an efficiency or
    a flow rate on one, so a rainfed plot can only end as `rainfed`."""
    if scenario.plot.irrigation_system != "none":
        assert scenario.plot.irrigation_system in {"drip", "sprinkler", "gravity"}
        return

    assert scenario.plot.is_rainfed
    assert scenario.expected.irrigation == "rainfed"
    assert scenario.nodes.count == 0, (
        "the rainfed path is the node-less one, K = 0 (docs/06-diseno-detallado.md:457)"
    )


def test_a_scenario_with_nodes_declares_the_channels_it_publishes(
    scenario: Scenario,
) -> None:
    """A node that reads no depth publishes no `soil_moisture_*` ramp, so the
    balance would have a sensor it cannot place in the root zone."""
    if not scenario.nodes.reads_soil:
        assert scenario.trajectories == {}
        assert scenario.faults == ()
        return

    depth = scenario.nodes.soil_moisture_depth_cm
    assert depth is not None
    ramp = scenario.trajectory(f"soil_moisture_{depth}cm")
    assert ramp["start"] != ramp["end"], "a constant soil moisture is not a trajectory"
    assert "air_temp" in scenario.trajectories
    assert "air_rh" in scenario.trajectories
    assert scenario.nodes.calibration == "field", (
        "every simulated node is field-calibrated (telemetry.domain.models)"
    )


def test_a_soil_ramp_stays_inside_its_own_field_capacity(scenario: Scenario) -> None:
    """A ramp above θFC would trip `waterlogging` instead of the stress the scenario
    demonstrates, and a ramp below 0 % is not a volumetric reading."""
    if not scenario.nodes.reads_soil:
        pytest.skip("the balance-on-weather path reads no sensor")

    ramp = scenario.trajectory(f"soil_moisture_{scenario.nodes.soil_moisture_depth_cm}cm")
    assert min(ramp["start"], ramp["end"]) >= 0.0
    assert max(ramp["start"], ramp["end"]) <= scenario.soil.field_capacity_pct


def test_every_expected_block_names_only_codes_the_domain_has(scenario: Scenario) -> None:
    """The oracle may not name a code that does not exist: it is what T5's e2e asserts
    against, and a typo there is a test that fails for the wrong reason."""
    from techcamp.alerts.adapters.seed import FACTORY_RULES
    from techcamp.irrigation.domain.models import RainfedAdvice, RecommendationKind

    rule_codes = {str(rule["code"]) for rule in FACTORY_RULES}

    assert set(scenario.expected.alerts) <= rule_codes
    assert scenario.expected.irrigation in {kind.value for kind in RecommendationKind}
    assert set(scenario.expected.advice) <= {advice.value for advice in RainfedAdvice}
    assert len(set(scenario.expected.alerts)) == len(scenario.expected.alerts)
    assert scenario.expected.alerts or scenario.expected.irrigation, (
        "an `expected` that asserts nothing proves nothing"
    )


def test_only_scenario_b_asserts_the_humidity_alert(library: tuple[Scenario, ...]) -> None:
    """docs/06-diseno-detallado.md:476 makes B the humidity scenario. Every other
    `expected` list is exhaustive, so one humid recorded day would add an alert they
    do not list — and the e2e would fail for a reason nobody wrote down."""
    by_name = {scenario.name: scenario for scenario in library}

    assert "fungal_risk" in by_name["rainy-season"].alerts
    for name, scenario in by_name.items():
        if name == "rainy-season":
            continue
        humid_days = sum(
            1
            for day in load_fixture(scenario.weather_fixture.removesuffix(".json")).observed
            if day.rh_mean_pct > FUNGAL_RH_PCT
            and FUNGAL_TEMP_RANGE_C[0] <= day.mean_daily_temp_c <= FUNGAL_TEMP_RANGE_C[1]
        )
        assert humid_days == 0, (
            f"{name}: its recording has {humid_days} days that satisfy both halves of "
            "fungal_risk, so that alert could open and the exhaustive list would be wrong"
        )
        assert "fungal_risk" not in scenario.alerts


def test_scenario_a_crosses_stress_on_the_documented_day(scenario: Scenario) -> None:
    """docs/06-diseno-detallado.md:442-448, stated as arithmetic and not as the
    YAML's own claim: 14 days from 22 % to 13 % crosses θ_estrés = 15,3 % on day 10,4,
    and `water_stress` opens six hours later."""
    if scenario.name != "el-nino":
        pytest.skip("scenario A's arithmetic")

    ramp = scenario.trajectory("soil_moisture_30cm")
    threshold = scenario.soil.stress_threshold_pct_with(MAIZE_P)
    crossing = BACKFILL_DAYS * (ramp["start"] - threshold) / (ramp["start"] - ramp["end"])
    depletion_mm = 1000 * (scenario.soil.field_capacity_pct / 100.0 - ramp["end"] / 100.0) * 0.6

    assert scenario.plot.sown_days_ago == 42
    assert scenario.nodes.soil_moisture_depth_cm == 30, "Zr/2 = 30 cm (docs/06:445)"
    assert ramp["start"] == 22.0 and ramp["end"] == 13.0
    assert crossing == pytest.approx(SCENARIO_A_CROSSING_DAY, abs=0.1)
    assert depletion_mm == pytest.approx(60.0, abs=0.6), "Dr_obs = 60 mm (docs/06:447)"
    assert depletion_mm > RAW_A_MM, "60 mm > RAW 46,2 mm, so the advice is to irrigate"
    assert scenario.expected.irrigation == "irrigate"
    assert scenario.alerts == ("water_stress", "heat_stress", "node_offline")


def test_scenario_a_trajectory_can_reach_the_heat_threshold(scenario: Scenario) -> None:
    """`heat_stress` is `air_temp > 35 °C` for 180 min (alerts/adapters/seed.py), so
    scenario A's band has to sit above it — otherwise the alert it asserts can never
    open and the e2e would wait forever."""
    if scenario.name != "el-nino":
        pytest.skip("scenario A's arithmetic")

    assert scenario.trajectory("air_temp")["daily_max"] > 35.0


def test_scenario_e_passes_raw_on_the_documented_day(scenario: Scenario) -> None:
    """docs/06-diseno-detallado.md:465-471: no node, no rain, so Pe = 0 and Dr grows
    by ETc a day. ETc = Kc 1,20 × ET0 ≈ 4,2 mm, so Dr passes RAW = 77 mm on day 16
    and the open alert is critical 48 h later."""
    if scenario.name != "dry-spell-rainfed":
        pytest.skip("scenario E's arithmetic")

    fixture = load_fixture("veranillo-caribe")
    observed = fixture.observed
    etc_mm = MAIZE_MID_KC * sum(day.et0_mm for day in observed) / len(observed)
    depletion_mm = [etc_mm * day.day_offset for day in observed]
    first_above_raw = next(
        day.day_offset
        for day, depletion in zip(observed, depletion_mm, strict=True)
        if depletion > RAW_E_MM
    )

    assert scenario.nodes.count == 0, "E is the node-less plot, K = 0 (docs/06:457)"
    assert scenario.plot.irrigation_system == "none"
    assert all(day.effective_rain_mm == 0.0 for day in fixture.days), (
        "no rain above 5 mm in the 21 days nor in the forecast (docs/06:458)"
    )
    assert etc_mm == pytest.approx(5.0, abs=0.5)
    assert first_above_raw == SCENARIO_E_STRESS_DAY, "Dr = 80 mm > RAW = 77 mm on day 16"
    assert first_above_raw + 2 == SCENARIO_E_CRITICAL_DAY, "critical 48 h later"
    assert depletion_mm[-1] < TAW_E_MM, "day 21 is below TAW, so never wilting (docs/06:471)"
    assert depletion_mm[-1] < fixture.forecast[0].effective_rain_mm + 140.0, (
        "Dr ≥ RAW with 0 mm of forecast rain is what makes it conserve (docs/06:471)"
    )
    assert scenario.expected.alerts == ("water_stress",)
    assert scenario.expected.irrigation == "rainfed"
    assert scenario.expected.advice == ("conserve_moisture",)
    assert "prioritize_harvest" not in scenario.expected.advice, (
        "mid is not late stage, so no harvest advice (docs/06:471)"
    )


def test_scenario_c_faults_a_node_for_the_documented_hours(scenario: Scenario) -> None:
    """docs/06-diseno-detallado.md:436, :477: scenario C is a node down for 8 h, which
    opens `node_offline` and leaves a gap for completeness to observe. D-T16.8: the
    fault is a publish pause, so nothing about the readings already sent changes."""
    if scenario.name != "node-failure":
        pytest.skip("scenario C's fault")

    assert len(scenario.faults) == 1
    fault = scenario.faults[0]
    assert fault.type == "offline"
    assert fault.hours == 8
    assert 1 <= fault.node <= scenario.nodes.count
    assert fault.at_day >= 1, "a fault on day 0 has no backfill before it to interrupt"
    assert "node_offline" in scenario.alerts


def test_scenario_c_is_not_also_the_heat_scenario(library: tuple[Scenario, ...]) -> None:
    """C demonstrates the gap, A demonstrates the heat. Overlapping the two would
    make each scenario's exhaustive `expected` list depend on the other's point."""
    by_name = {scenario.name: scenario for scenario in library}

    assert "heat_stress" in by_name["el-nino"].alerts
    assert "heat_stress" not in by_name["node-failure"].alerts
    assert by_name["node-failure"].trajectory("air_temp")["daily_max"] <= 35.0


def test_the_normal_scenario_stays_below_every_threshold(library: tuple[Scenario, ...]) -> None:
    """docs/06-diseno-detallado.md:480: `normal` is the contrast. Its value is that
    nothing opens — so its soil ramp must stay above θ_estrés and its bands below the
    heat and humidity thresholds."""
    normal = next(s for s in library if s.name == "normal")
    ramp = normal.trajectory(f"soil_moisture_{normal.nodes.soil_moisture_depth_cm}cm")

    assert normal.alerts == ()
    assert min(ramp["start"], ramp["end"]) > STRESS_THRESHOLD_PCT
    assert normal.trajectory("air_temp")["daily_max"] <= 35.0
    assert normal.trajectory("air_rh")["daily_max"] <= FUNGAL_RH_PCT
    assert normal.faults == ()
    assert normal.nodes.reads_soil, "the contrast is a normally working, sensored plot"


def test_loading_the_library_opens_no_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """docs/06-diseno-detallado.md:489 — recorded once, then read offline."""

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("the scenario library must not touch the network")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    assert len(load_all_scenarios()) == len(SCENARIO_NAMES)
