"""E16 T1: the scenario YAMLs' executable contract (docs/06-diseno-detallado.md:421-440).

A scenario is data, not code. Six YAMLs live beside the simulator (D-T16.1,
`docs/06-diseno-detallado.md:421`) and every task from T2 on consumes them, so their
shape is written down here as code: a parser that refuses anything outside the
schema, and a vocabulary check that fails when a YAML drifts from the domain enums
the runner will bind it to.

Three decisions from T0 shape the schema:

- **The recorded fixture is pinned.** `weather_fixture_sha256` covers the JSON's
  bytes, so an edited recording cannot pass unnoticed by its consumer
  (`scenario_library.py` states the same pin from the fixture's side).
- **An `expected` block is an assertion, so it may only name what exists.** Alert
  codes come from the factory seed, irrigation kinds from `RecommendationKind`,
  advice from `RainfedAdvice`. A typo in an `expected` list is caught here instead
  of becoming an e2e that can never pass (docs/06-diseno-detallado.md:485).
- **Faults are publish pauses** (D-T16.8, `docs/06-diseno-detallado.md:488`): an
  `offline` fault publishes nothing for `hours`, then `seq` jumps and completeness
  observes the gap, so a fault only ever carries *when* and *how long*.

A node-less plot is legal too (docs/06:457, scenario E): the balance runs on weather
alone with `K = 0`, so `count: 0` needs neither depth nor calibration.

Vocabularies are imported from the real domain rather than restated, so a change to
`RecommendationKind` or to the factory rules breaks these scenarios on purpose
instead of letting a stale string through. Those two imports live inside
`_parse_expected` because they are the only borrowings this contract makes from the
implementation, and T2 owns both modules.

Spanish appears here only as a rule to *require* it (`description`): the sentence each
presenter reads is Spanish per `docs/06-diseno-detallado.md:424`, while keys,
comments and code stay English (AGENTS.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .scenario_library import SCENARIOS_DIR, file_sha256

SCENARIO_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "plot",
        "soil",
        "nodes",
        "interval_s",
        "weather_fixture",
        "weather_fixture_sha256",
        "expected",
    }
)
OPTIONAL_SCENARIO_KEYS: frozenset[str] = frozenset({"trajectories", "faults"})
"""`trajectories` is absent for the node-less rainfed plot (scenario E reads no
sensor) and `faults` for every scenario whose nodes stay online."""

PLOT_KEYS: frozenset[str] = frozenset(
    {"crop", "sown_days_ago", "area_ha", "soil", "irrigation_system"}
)
SOIL_KEYS: frozenset[str] = frozenset({"field_capacity_pct", "wilting_point_pct"})
NODE_KEYS: frozenset[str] = frozenset({"count", "soil_moisture_depth_cm", "calibration"})
FAULT_KEYS: frozenset[str] = frozenset({"at_day", "node", "type", "hours"})
EXPECTED_KEYS: frozenset[str] = frozenset({"alerts", "irrigation"})
ADVICE_OPTIONAL: frozenset[str] = frozenset({"advice"})
"""`advice` is the one optional key of `expected`: only a rainfed plot carries it
(docs/06-diseno-detallado.md:459-462), and an irrigated plot has no advice to give."""
RAMP_KEYS: frozenset[str] = frozenset({"start", "end", "noise"})
BAND_KEYS: frozenset[str] = frozenset({"daily_min", "daily_max", "noise"})
NOISE_OPTIONAL: frozenset[str] = frozenset({"noise"})
TRAJECTORY_VALUES: tuple[str, ...] = ("soil_moisture", "air_temp", "air_rh")
"""The channels the runner publishes (docs/06-diseno-detallado.md:432-434): one soil
ramp per sensor depth, plus the two air channels a balance and the heat rule read."""

FAULT_TYPES: frozenset[str] = frozenset({"offline"})
"""D-T16.8 fixes `offline` as the fault a scenario configures; the out-of-range
`quality = 2` reading and the `seq` gap are what it produces (docs/06:488)."""

MIN_INTERVAL_S = 60
"""A backfill interval below a minute would make the completeness window in
`docs/03-modelo-datos.md:145` meaningless, so the contract refuses it."""

MAX_NODES = 4
"""How many nodes one scenario may provision. The seminar is one room with one stack
behind it — `docs/04:268` says the same of the outbox — so more nodes than the room
holds is a scenario nobody can demonstrate."""


class ScenarioSchemaError(ValueError):
    """A scenario YAML does not satisfy the scenario-library contract."""


def _require(condition: Any, message: str) -> None:
    if not condition:
        raise ScenarioSchemaError(message)


def _keys(
    raw: Any, expected: frozenset[str], where: str, optional: frozenset[str] = frozenset()
) -> dict[str, Any]:
    """A mapping carrying `expected`: no missing key, no invented one.

    `optional` names keys the format may leave out. Only `advice` may be absent,
    and only because an irrigated plot has no advice to give (docs/06:459-462):
    `alerts` and `irrigation` are the oracle's spine, so a scenario without them
    would assert nothing.
    """
    if not isinstance(raw, dict):
        raise ScenarioSchemaError(f"{where}: must be a mapping")
    missing = expected - optional - raw.keys()
    unknown = raw.keys() - expected
    _require(not missing, f"{where}: missing key(s) {sorted(missing)}")
    _require(not unknown, f"{where}: unknown key(s) {sorted(unknown)}")
    return raw


def _block(raw: Any, where: str) -> dict[str, Any]:
    """`_keys` without a known key set, for a block whose keys vary by shape."""
    if not isinstance(raw, dict):
        raise ScenarioSchemaError(f"{where}: must be a mapping")
    return raw


def _number(value: Any, where: str, *, minimum: float | None = None) -> float:
    """A YAML number that is really a number: a bool is never one."""
    _require(
        isinstance(value, (int, float)) and not isinstance(value, bool),
        f"{where}: expected a number, got {type(value).__name__} ({value!r})",
    )
    number = float(value)
    if minimum is not None:
        _require(number >= minimum, f"{where}: {number} is below {minimum}")
    return number


def _whole(value: Any, where: str, *, minimum: int) -> int:
    number = _number(value, where, minimum=float(minimum))
    _require(number.is_integer(), f"{where}: expected a whole number, got {number}")
    return int(number)


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScenarioSchemaError(f"{where}: must be a non-empty string")
    return value


def _codes(raw: Any, vocabulary: frozenset[str], where: str) -> tuple[str, ...]:
    """An assertion list: every entry must be a code the domain actually has.

    Exhaustive on purpose. `expected` is the e2e's oracle, so an unknown code would
    either raise inside the checker or, worse, silently match nothing.
    """
    if not isinstance(raw, list):
        raise ScenarioSchemaError(f"{where}: must be a list")
    for index, code in enumerate(raw):
        _text(code, f"{where}[{index}]")
    unknown = sorted(set(raw) - vocabulary)
    _require(not unknown, f"{where}: unknown code(s) {unknown}")
    _require(len(set(raw)) == len(raw), f"{where}: repeated code(s)")
    return tuple(raw)


@dataclass(frozen=True, slots=True)
class ScenarioPlot:
    """The plot the scenario creates: crop, stage, area, soil and system."""

    crop: str
    sown_days_ago: int
    area_ha: float
    soil: str
    irrigation_system: str

    @property
    def is_rainfed(self) -> bool:
        """A `none` plot takes a rainfed recommendation, never a depth (docs/00)."""
        return self.irrigation_system == "none"


@dataclass(frozen=True, slots=True)
class ScenarioSoil:
    """The soil's water limits as the scenario states them.

    This block is the authority for the scenario's arithmetic: FAO-56's table value
    for `sandy_loam` is not consulted, because docs/06-diseno-detallado.md:427 pins
    θWP = 9 % for exactly this demonstration. `stress_threshold_pct_with` is the
    formula docs/06-diseno-detallado.md:444 spells out, exposed so T4's domain test
    reads θ_estrés off the scenario instead of restating it.
    """

    field_capacity_pct: float
    wilting_point_pct: float

    def stress_threshold_pct_with(self, depletion_fraction: float) -> float:
        """θ_estrés = `θFC − p × (θFC − θWP)` for a crop's depletion fraction `p`."""
        _require(0.0 < depletion_fraction <= 1.0, f"p must be in (0, 1], got {depletion_fraction}")
        return self.field_capacity_pct - depletion_fraction * (
            self.field_capacity_pct - self.wilting_point_pct
        )


@dataclass(frozen=True, slots=True)
class ScenarioNodes:
    """How many nodes the scenario provisions and how they read the soil."""

    count: int
    soil_moisture_depth_cm: int | None
    calibration: str | None

    @property
    def reads_soil(self) -> bool:
        """False for `count: 0`, the `K = 0` balance-on-weather path (docs/06:457)."""
        return self.count > 0


@dataclass(frozen=True, slots=True)
class ScenarioFault:
    """A configured fault: when it starts, which node, and how long it lasts.

    D-T16.8: an `offline` fault is a *publish pause*, so nothing here says what happens
    to the readings already sent — the past is never rewritten and completeness
    observes the resulting gap.
    """

    at_day: int
    node: int
    type: str
    hours: int


@dataclass(frozen=True, slots=True)
class ScenarioExpected:
    """The e2e oracle: alert codes, the irrigation kind, and any advice codes.

    `advice` is optional because only a rainfed plot carries it
    (docs/06-diseno-detallado.md:459-462); an irrigated plot has no advice to give.
    """

    alerts: tuple[str, ...]
    irrigation: str
    advice: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Scenario:
    """A parsed, validated scenario."""

    name: str
    description: str
    plot: ScenarioPlot
    soil: ScenarioSoil
    nodes: ScenarioNodes
    interval_s: int
    weather_fixture: str
    weather_fixture_sha256: str
    expected: ScenarioExpected
    trajectories: dict[str, dict[str, float]]
    faults: tuple[ScenarioFault, ...]
    path: Path

    @property
    def weather_fixture_path(self) -> Path:
        """The recorded file this scenario pins, whether or not it exists yet."""
        return SCENARIOS_DIR / "weather" / self.weather_fixture

    @property
    def alerts(self) -> tuple[str, ...]:
        """Shorthand for the oracle, so a checker reads `scenario.alerts`."""
        return self.expected.alerts

    def trajectory(self, key: str) -> dict[str, float]:
        """One trajectory block; an absent block is an error, not an empty dict."""
        _require(key in self.trajectories, f"{self.name}: no trajectory {key!r}")
        return self.trajectories[key]


def _parse_trajectories(raw: Any, where: str) -> dict[str, dict[str, float]]:
    """Each block is a ramp (`start` → `end`) or a daily band (`daily_min`/`daily_max`).

    The key decides which: a `soil_moisture_*` reading falls linearly across the
    backfill (docs/06-diseno-detallado.md:432) while an air channel has a daily band
    instead, so a soil block carrying a band could not cross θ_estrés at a day anyone
    could compute.
    """
    trajectories = _block(raw, where)
    _require(bool(trajectories), f"{where}: must declare its trajectories")
    parsed: dict[str, dict[str, float]] = {}
    for key, block in trajectories.items():
        block_where = f"{where}.{key}"
        values = _block(block, block_where)
        is_ramp = "start" in values or "end" in values
        is_band = "daily_min" in values or "daily_max" in values
        _require(
            is_ramp != is_band,
            f"{block_where}: needs a ramp (start/end) or a daily band "
            "(daily_min/daily_max), not both",
        )
        # `noise` is optional: docs/06-diseno-detallado.md:434 gives `air_rh` a
        # bare band. Noise is configurable (docs/06:488), never mandatory — and a
        # channel the simulator models exactly is a channel whose noiseless reading
        # is still worth reading.
        _keys(values, RAMP_KEYS if is_ramp else BAND_KEYS, block_where, NOISE_OPTIONAL)
        _require(
            key.startswith("soil_moisture") == is_ramp,
            f"{block_where}: a soil_moisture trajectory is a ramp, an air channel is a band",
        )
        # `noise` absent means exactly zero, so a downstream reader never has to
        # ask whether a missing key is "no noise yet" or "noiseless" (docs/06:488).
        numbers = {
            field: _number(values.get(field, 0.0), f"{block_where}.{field}", minimum=0.0)
            for field in sorted(RAMP_KEYS if is_ramp else BAND_KEYS)
        }
        if is_ramp:
            _require(
                numbers["start"] != numbers["end"],
                f"{block_where}: a constant soil moisture is a constant, not a trajectory",
            )
        else:
            _require(
                numbers["daily_min"] <= numbers["daily_max"],
                f"{block_where}: daily_min above daily_max",
            )
        parsed[key] = numbers
    return parsed


def _parse_faults(raw: Any, where: str, node_count: int) -> tuple[ScenarioFault, ...]:
    if not isinstance(raw, list):
        raise ScenarioSchemaError(f"{where}: must be a list")
    faults: list[ScenarioFault] = []
    for index, entry in enumerate(raw):
        entry_where = f"{where}[{index}]"
        values = _keys(entry, FAULT_KEYS, entry_where)
        fault = ScenarioFault(
            at_day=_whole(values["at_day"], f"{entry_where}.at_day", minimum=0),
            node=_whole(values["node"], f"{entry_where}.node", minimum=1),
            type=_text(values["type"], f"{entry_where}.type"),
            hours=_whole(values["hours"], f"{entry_where}.hours", minimum=1),
        )
        _require(fault.type in FAULT_TYPES, f"{entry_where}.type: unknown type {fault.type!r}")
        _require(
            fault.node <= node_count,
            f"{entry_where}.node: node {fault.node} does not exist "
            f"(the scenario provisions {node_count})",
        )
        faults.append(fault)
    return tuple(faults)


def _parse_expected(raw: Any, where: str) -> ScenarioExpected:
    """The oracle, checked against the domain's own vocabularies."""
    from techcamp.alerts.adapters.seed import FACTORY_RULES
    from techcamp.irrigation.domain.models import RainfedAdvice, RecommendationKind

    expected = _keys(raw, EXPECTED_KEYS | ADVICE_OPTIONAL, where, ADVICE_OPTIONAL)
    irrigation = _text(expected["irrigation"], f"{where}.irrigation")
    _require(
        irrigation in frozenset(kind.value for kind in RecommendationKind),
        f"{where}.irrigation: unknown kind {irrigation!r}",
    )
    return ScenarioExpected(
        alerts=_codes(
            expected["alerts"],
            frozenset(str(rule["code"]) for rule in FACTORY_RULES),
            f"{where}.alerts",
        ),
        irrigation=irrigation,
        advice=_codes(
            expected.get("advice", []),
            frozenset(advice.value for advice in RainfedAdvice),
            f"{where}.advice",
        ),
    )


def parse_scenario(name: str, raw: Any) -> Scenario:
    """Validate one scenario's parsed YAML and return it.

    Raises:
        ScenarioSchemaError: on any shape, range or vocabulary violation, naming the
            offending block so a broken scenario is cheap to fix.
    """
    where = f"{name}.yaml"
    values = _keys(raw, SCENARIO_KEYS | OPTIONAL_SCENARIO_KEYS, where, OPTIONAL_SCENARIO_KEYS)
    _require(values["name"] == name, f"{where}: name must match the filename")

    node_values = _block(values["nodes"], f"{where}.nodes")
    count = _whole(node_values.get("count"), f"{where}.nodes.count", minimum=0)
    reads_soil = count > 0
    _keys(
        node_values,
        NODE_KEYS if reads_soil else frozenset({"count"}),
        f"{where}.nodes",
    )
    if reads_soil:
        _require(count <= MAX_NODES, f"{where}.nodes.count: {count} exceeds {MAX_NODES}")
        nodes = ScenarioNodes(
            count=count,
            soil_moisture_depth_cm=_whole(
                node_values["soil_moisture_depth_cm"],
                f"{where}.nodes.soil_moisture_depth_cm",
                minimum=1,
            ),
            calibration=_text(node_values["calibration"], f"{where}.nodes.calibration"),
        )
    else:
        nodes = ScenarioNodes(count=0, soil_moisture_depth_cm=None, calibration=None)

    _require(
        reads_soil == ("trajectories" in values),
        f"{where}: a scenario with nodes must declare its trajectories, and one "
        f"without nodes must not (trajectories are "
        f"{'missing' if reads_soil else 'present'})",
    )

    _require(
        any(
            character.isalpha()
            for character in _text(values["description"], f"{where}.description")
        ),
        f"{where}.description: must be the Spanish sentence a presenter reads (docs/06:424)",
    )

    fixture = _text(values["weather_fixture"], f"{where}.weather_fixture")
    _require(fixture.endswith(".json"), f"{where}.weather_fixture: must name a .json fixture")
    _require(
        values["weather_fixture_sha256"] == file_sha256(SCENARIOS_DIR / "weather" / fixture),
        f"{where}.weather_fixture_sha256 does not match {fixture} (a recorded value was edited)",
    )

    soil_values = _keys(values["soil"], SOIL_KEYS, f"{where}.soil")
    field_capacity = _number(
        soil_values["field_capacity_pct"], f"{where}.soil.field_capacity_pct", minimum=0.0
    )
    wilting_point = _number(
        soil_values["wilting_point_pct"], f"{where}.soil.wilting_point_pct", minimum=0.0
    )
    _require(
        wilting_point < field_capacity,
        f"{where}.soil: wilting_point_pct {wilting_point} must be below "
        f"field_capacity_pct {field_capacity}",
    )

    plot_values = _keys(values["plot"], PLOT_KEYS, f"{where}.plot")
    return Scenario(
        name=values["name"],
        description=values["description"],
        plot=ScenarioPlot(
            crop=_text(plot_values["crop"], f"{where}.plot.crop"),
            sown_days_ago=_whole(
                plot_values["sown_days_ago"], f"{where}.plot.sown_days_ago", minimum=0
            ),
            area_ha=_number(plot_values["area_ha"], f"{where}.plot.area_ha", minimum=0.0),
            soil=_text(plot_values["soil"], f"{where}.plot.soil"),
            irrigation_system=_text(
                plot_values["irrigation_system"], f"{where}.plot.irrigation_system"
            ),
        ),
        soil=ScenarioSoil(field_capacity_pct=field_capacity, wilting_point_pct=wilting_point),
        nodes=nodes,
        interval_s=_whole(values["interval_s"], f"{where}.interval_s", minimum=MIN_INTERVAL_S),
        weather_fixture=fixture,
        weather_fixture_sha256=values["weather_fixture_sha256"],
        expected=_parse_expected(values["expected"], f"{where}.expected"),
        trajectories=(
            _parse_trajectories(values["trajectories"], f"{where}.trajectories")
            if reads_soil
            else {}
        ),
        faults=(
            _parse_faults(values["faults"], f"{where}.faults", count) if "faults" in values else ()
        ),
        path=SCENARIOS_DIR / f"{name}.yaml",
    )


def load_scenario(name: str) -> Scenario:
    """Read and validate the scenario named `<name>.yaml` from the library.

    `yaml.safe_load` resolves only basic tags, which is all a scenario file in the
    repository needs: nothing here constructs a Python object out of the bytes
    (PyYAML documents `safe_load` as its loader for untrusted input).
    """
    path = SCENARIOS_DIR / f"{name}.yaml"
    return parse_scenario(name, yaml.safe_load(path.read_text(encoding="utf-8")))
