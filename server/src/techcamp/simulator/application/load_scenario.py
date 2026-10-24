"""E16 T3: the scenario loader — the base data a scenario needs before the
simulator publishes anything (docs/04-api.md:263; D-T16.2).

`POST /dev/scenarios/{name}:load` prepares the base and nothing else: the
farm, the plot, the soil, the cycle and the nodes go through the same
application use cases the API itself uses. The scenario's weather fixture
lands in `weather_daily` through the weather repository (the route's own
call, `load_weather_fixture`). The backfill is published by the CLI over MQTT
(docs/06-diseno-detallado.md:424-426), so nothing here enqueues or publishes.

Three rules shape the loader:

- **It is idempotent per (scenario, day-anchor).** The anchor is the
  scenario's day 0, and the load's identity is the plot's own name, so a
  second load of the same scenario on the same day finds what the first one
  left and returns the same ids instead of a second plot.
- **It never touches another organization's resources.** The load is resolved
  inside the caller's organizations, and a load that belongs to one of them
  answers 404 like any other foreign resource (docs/04-api.md:16) — never
  403, which would reveal that it exists.
- **It writes through use cases.** Every row it creates is written by the
  module that owns it (`farms` for the farm, the plot, the soil and the cycle;
  `telemetry` for the node), so the same organization filter, the same role
  check and the same validations the API applies apply here too.

The organization itself is not created here: `sim run` owns that creation
(D-T16.5) and identity has no use case for it, so the loader resolves the
organization the caller belongs to.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol
from uuid import UUID

from techcamp.farms.application.manage_cycles import create_cycle, update_cycle
from techcamp.farms.application.manage_farms import create_farm
from techcamp.farms.application.manage_plots import create_plot
from techcamp.farms.application.manage_soil import put_soil_profile
from techcamp.farms.application.ports import (
    CropCycleRepository,
    CropRepository,
    FarmRepository,
    PlotRepository,
    SoilProfileRepository,
)
from techcamp.farms.domain.models import CropCycleStatus, IrrigationSystem, ensure_can_write
from techcamp.identity.application.ports import MembershipRepository
from techcamp.identity.domain.models import Role
from techcamp.telemetry.application.manage_nodes import claim_node, rotate_credentials, update_node
from techcamp.telemetry.application.ports import NodeRepository
from techcamp.telemetry.domain.models import NodeStatus

from ..scenario_library import WeatherFixture
from ..scenario_schema import Scenario
from ..scenario_schema import load_scenario as load_scenario_definition
from .load_weather import WeatherFixtureWriter, load_weather_fixture

MUNICIPALITY_CODE = "47001"
"""Santa Marta's DIVIPOLA code, the municipality the Caribe cell's recorded
fixtures describe (their own cell is 11,0 N / 74,8 W). The same code the
repository's test fixtures use, because the scenario states no municipality."""

METERS_PER_DEGREE_LAT = 110_574.0
"""One degree of latitude, in meters (the WGS-84 mean)."""

METERS_PER_DEGREE_LON = 111_320.0
"""One degree of longitude at the equator, in meters; the loader divides it by
`cos(latitude)` for the cell it places the plot in."""

ROOT_DEPTH_CM_WITHOUT_SENSOR = 100.0
"""The root depth of the scenario that reads no sensor: maize at flowering
takes the lower end of FAO-56's 1,0-1,7 m (docs/06-diseno-detallado.md:465)."""

SOIL_MOISTURE_UNIT = "%"
AIR_TEMPERATURE_UNIT = "°C"
AIR_HUMIDITY_UNIT = "%RH"
"""The humidity is deliberately not "%": the simulator's calibration rule reads
a "%" sensor as soil moisture (`simulator/node_client.py`), and the humidity
channel has to keep the identity linear calibration to stay the humidity the
scenario states."""


class ScenarioNotFoundError(Exception):
    """The library has no scenario by that name."""


class ScenarioOrgNotFoundError(Exception):
    """The caller belongs to no organization, so there is nothing to load into."""


class ScenarioAmbiguousOrgError(Exception):
    """The caller belongs to several organizations and the body names none."""


class ScenarioDataInAnotherOrgError(Exception):
    """The load exists, in another organization."""


@dataclass(frozen=True, slots=True)
class ScenarioSensor:
    """One sensor a scenario's node carries."""

    channel_key: str
    metric: str
    depth_cm: int | None
    unit: str


@dataclass(frozen=True, slots=True)
class ScenarioNode:
    """One claimed node and the credential it was claimed with."""

    node_id: UUID
    mqtt_username: str
    mqtt_password: str


@dataclass(frozen=True, slots=True)
class ScenarioPlotRef:
    """The plot a previous load left, and the organization that owns it."""

    plot_id: UUID
    org_id: UUID


@dataclass(frozen=True, slots=True)
class ScenarioLoad:
    """What a load prepared: the ids the simulator publishes to."""

    scenario: str
    org_id: UUID
    plot_ids: tuple[UUID, ...]
    node_ids: tuple[UUID, ...]
    cycle_ids: tuple[UUID, ...]
    nodes: tuple[ScenarioNode, ...]


class ScenarioNodeProvisioner(Protocol):
    """The dev-only shortcut that inserts an unclaimed node with its sensors.

    Docs have no public API to create an unclaimed node — the same gap
    `simulator/provision.py` documents — so the loader inserts the rows
    directly and then claims the node through the real use case.
    """

    async def provision(self, *, sensors: Sequence[ScenarioSensor]) -> str:
        """Insert one unclaimed node carrying `sensors`; return its claim code."""
        ...


class ScenarioLoadRegistry(Protocol):
    """Where a previous load of this (scenario, day-anchor) is looked up.

    The farms' repositories are organization-scoped by design, and the loader
    has to see a load that belongs to *another* organization in order to
    answer 404 instead of quietly creating a second copy of the same
    scenario.
    """

    async def find_plot(self, name: str) -> ScenarioPlotRef | None:
        """The newest plot named `name`, whatever organization owns it."""
        ...


def plot_name_for(scenario: str, day: date) -> str:
    """The name that carries the load's identity: the scenario and its anchor."""
    return f"Escenario {scenario} {day.isoformat()}"


def farm_name_for(scenario: str) -> str:
    """The name of the farm the scenario's parcels live on."""
    return f"Escenario {scenario}"


def boundary_wkt(*, lat: float, lon: float, area_ha: float) -> str:
    """A square around the cell with the scenario's area.

    The plot's area is a computed column (`ST_Area` over the boundary,
    docs/03-modelo-datos.md:118), so the scenario's `area_ha` has to come out
    of the geometry rather than be written beside it.
    """
    half_side_m = math.sqrt(area_ha * 10_000.0) / 2.0
    half_lat = half_side_m / METERS_PER_DEGREE_LAT
    half_lon = half_side_m / (METERS_PER_DEGREE_LON * math.cos(math.radians(lat)))
    south, north = lat - half_lat, lat + half_lat
    west, east = lon - half_lon, lon + half_lon
    return (
        "SRID=4326;POLYGON(("
        f"{west:.8f} {south:.8f}, {east:.8f} {south:.8f}, "
        f"{east:.8f} {north:.8f}, {west:.8f} {north:.8f}, {west:.8f} {south:.8f}"
        "))"
    )


def point_wkt(*, lat: float, lon: float) -> str:
    """The farm's location: the cell the scenario's weather belongs to."""
    return f"SRID=4326;POINT({lon:.8f} {lat:.8f})"


def scenario_sensors(scenario: Scenario) -> tuple[ScenarioSensor, ...]:
    """The sensors a scenario's node carries, one per trajectory it publishes.

    The channel key is the trajectory's own name, so the runner publishes the
    scenario's channel and the node carries the sensor that channel belongs to
    (docs/06-diseno-detallado.md:432-434).
    """
    sensors: list[ScenarioSensor] = []
    for channel_key in scenario.trajectories:
        if channel_key.startswith("soil_moisture"):
            sensors.append(
                ScenarioSensor(
                    channel_key=channel_key,
                    metric="soil_moisture",
                    depth_cm=scenario.nodes.soil_moisture_depth_cm,
                    unit=SOIL_MOISTURE_UNIT,
                )
            )
        elif channel_key == "air_temp":
            sensors.append(
                ScenarioSensor(
                    channel_key=channel_key,
                    metric="air_temp",
                    depth_cm=None,
                    unit=AIR_TEMPERATURE_UNIT,
                )
            )
        elif channel_key == "air_rh":
            sensors.append(
                ScenarioSensor(
                    channel_key=channel_key,
                    metric="air_rh",
                    depth_cm=None,
                    unit=AIR_HUMIDITY_UNIT,
                )
            )
    return tuple(sensors)


def scenario_root_depth_cm(scenario: Scenario) -> float:
    """The root depth the scenario's balance reads (Zr).

    The scenario's own numbers carry it: the sensor sits at Zr/2
    (docs/06-diseno-detallado.md:445), so a scenario that reads the soil has
    Zr = 2 × the sensor's depth. A scenario that reads no sensor has no such
    hint, and the only one there is — the veranillo at flowering — takes FAO-56's
    lower end of 1,0 m (docs/06-diseno-detallado.md:465).
    """
    depth = scenario.nodes.soil_moisture_depth_cm
    if depth is None:
        return ROOT_DEPTH_CM_WITHOUT_SENSOR
    return float(depth) * 2.0


async def _target_org(*, user_id: UUID, memberships: MembershipRepository) -> tuple[UUID, Role]:
    """The organization the load belongs to, and the caller's role in it: the
    caller's own.

    The body carries no organization (docs/04-api.md:263), so a caller that
    belongs to several of them has no way to say which one the scenario is for
    and is told so instead of being given one of them at random.
    """
    caller_memberships = await memberships.list_for_user(user_id)
    if not caller_memberships:
        raise ScenarioOrgNotFoundError(user_id)
    if len(caller_memberships) > 1:
        raise ScenarioAmbiguousOrgError(user_id)
    membership = caller_memberships[0]
    return membership.org_id, membership.role


async def _farm_for(
    *,
    scenario: Scenario,
    org_id: UUID,
    user_id: UUID,
    lat: float,
    lon: float,
    farms: FarmRepository,
    memberships: MembershipRepository,
) -> UUID:
    """The scenario's farm, created the first time the scenario is loaded."""
    name = farm_name_for(scenario.name)
    for farm in await farms.list_for_org(org_id):
        if farm.name == name:
            return farm.id
    farm = await create_farm(
        user_id=user_id,
        org_id=org_id,
        name=name,
        municipality_code=MUNICIPALITY_CODE,
        location_wkt=point_wkt(lat=lat, lon=lon),
        technician_id=None,
        farms=farms,
        memberships=memberships,
    )
    return farm.id


async def _cycle_for(
    *,
    scenario: Scenario,
    plot_id: UUID,
    crop_id: int,
    day: date,
    user_id: UUID,
    plots: PlotRepository,
    crops: CropRepository,
    cycles: CropCycleRepository,
    memberships: MembershipRepository,
) -> UUID:
    """The plot's active cycle, created when it has none.

    `create_cycle` refuses a second active cycle, so a plot whose cycle was
    closed (a `replace`) gets a new one instead of failing.
    """
    active = await cycles.get_active_for_plot(plot_id)
    if active is not None:
        return active.id
    cycle = await create_cycle(
        user_id=user_id,
        plot_id=plot_id,
        crop_id=crop_id,
        sown_on=day - timedelta(days=scenario.plot.sown_days_ago),
        plots=plots,
        crops=crops,
        cycles=cycles,
        memberships=memberships,
    )
    return cycle.id


async def _nodes_for(
    *,
    scenario: Scenario,
    plot_id: UUID,
    org_id: UUID,
    user_id: UUID,
    nodes: NodeRepository,
    plots: PlotRepository,
    memberships: MembershipRepository,
    provisioner: ScenarioNodeProvisioner,
    rotate: bool,
) -> tuple[ScenarioNode, ...]:
    """The plot's nodes, claimed through the claim path.

    A node is provisioned unclaimed and then claimed with the real use case,
    so the node carries the organization, the plot and the credential the same
    way it does when a farmer claims it. A node a previous load already
    claimed is reused — the load is idempotent — and its credential is
    rotated, because the claim's password is only readable once.
    """
    claimed: list[ScenarioNode] = []
    for node in await nodes.list_for_org(org_id, plot_id=plot_id):
        if node.status is NodeStatus.RETIRED:
            continue
        password = (
            await rotate_credentials(
                user_id=user_id, node_id=node.id, nodes=nodes, memberships=memberships
            )
            if rotate
            else ""
        )
        claimed.append(
            ScenarioNode(
                node_id=node.id,
                mqtt_username=str(node.id),
                mqtt_password=password,
            )
        )
    sensors = scenario_sensors(scenario)
    while len(claimed) < scenario.nodes.count:
        claim_code = await provisioner.provision(sensors=sensors)
        node, password = await claim_node(
            user_id=user_id,
            claim_code=claim_code,
            plot_id=plot_id,
            nodes=nodes,
            plots=plots,
            memberships=memberships,
        )
        claimed.append(
            ScenarioNode(
                node_id=node.id,
                mqtt_username=str(node.id),
                mqtt_password=password,
            )
        )
    return tuple(claimed)


async def load_scenario(
    *,
    name: str,
    day: date,
    replace: bool,
    user_id: UUID,
    crop_id: int,
    lat: float,
    lon: float,
    fixture: WeatherFixture,
    farms: FarmRepository,
    plots: PlotRepository,
    crops: CropRepository,
    cycles: CropCycleRepository,
    soil_profiles: SoilProfileRepository,
    nodes: NodeRepository,
    memberships: MembershipRepository,
    weather: WeatherFixtureWriter,
    provisioner: ScenarioNodeProvisioner,
    registry: ScenarioLoadRegistry,
) -> ScenarioLoad:
    """Prepare the base data one scenario needs, idempotently."""
    try:
        scenario = load_scenario_definition(name)
    except FileNotFoundError as error:
        raise ScenarioNotFoundError(name) from error

    org_id, role = await _target_org(user_id=user_id, memberships=memberships)
    plot_name = plot_name_for(scenario.name, day)

    previous = await registry.find_plot(plot_name)
    if previous is not None and previous.org_id != org_id:
        raise ScenarioDataInAnotherOrgError(previous.org_id)

    # The caller's role is checked before the first write. The use cases below
    # check it too, but they are reached one row at a time: the weather fixture
    # lands before the parcel does, so a refusal that came from a use case
    # would already have left the shared weather rows behind. A load that is
    # refused writes nothing at all.
    ensure_can_write(role)

    # The fixture lands here, after the organization and the previous load are
    # known: a request that ends in 404 must not write anything. It lands
    # before the parcel does, because the parcel's write path defers a
    # provider fetch for a cell that has no rows yet (farms
    # `get_or_create_cell`), and a scenario's weather is the recording, not the
    # provider's answer.
    await load_weather_fixture(fixture=fixture, day=day, weather=weather)

    if previous is not None and not replace:
        return await _reuse(
            scenario=scenario,
            plot_id=previous.plot_id,
            org_id=org_id,
            day=day,
            crop_id=crop_id,
            user_id=user_id,
            plots=plots,
            crops=crops,
            cycles=cycles,
            nodes=nodes,
            memberships=memberships,
            provisioner=provisioner,
        )

    if previous is not None:
        await _retire(
            plot_id=previous.plot_id,
            org_id=org_id,
            user_id=user_id,
            plots=plots,
            cycles=cycles,
            nodes=nodes,
            memberships=memberships,
        )

    farm_id = await _farm_for(
        scenario=scenario,
        org_id=org_id,
        user_id=user_id,
        lat=lat,
        lon=lon,
        farms=farms,
        memberships=memberships,
    )
    plot = await create_plot(
        user_id=user_id,
        farm_id=farm_id,
        name=plot_name,
        boundary_wkt=boundary_wkt(lat=lat, lon=lon, area_ha=scenario.plot.area_ha),
        irrigation_system=IrrigationSystem(scenario.plot.irrigation_system),
        irrigation_efficiency=None,
        system_flow_lph=None,
        farms=farms,
        plots=plots,
        memberships=memberships,
    )
    await put_soil_profile(
        user_id=user_id,
        plot_id=plot.id,
        texture=scenario.plot.soil,
        ph=None,
        organic_matter_pct=None,
        field_capacity_pct=scenario.soil.field_capacity_pct,
        wilting_point_pct=scenario.soil.wilting_point_pct,
        root_depth_cm=scenario_root_depth_cm(scenario),
        plots=plots,
        soil_profiles=soil_profiles,
        memberships=memberships,
    )
    cycle_id = await _cycle_for(
        scenario=scenario,
        plot_id=plot.id,
        crop_id=crop_id,
        day=day,
        user_id=user_id,
        plots=plots,
        crops=crops,
        cycles=cycles,
        memberships=memberships,
    )
    claimed = await _nodes_for(
        scenario=scenario,
        plot_id=plot.id,
        org_id=org_id,
        user_id=user_id,
        nodes=nodes,
        plots=plots,
        memberships=memberships,
        provisioner=provisioner,
        rotate=False,
    )
    return ScenarioLoad(
        scenario=scenario.name,
        org_id=org_id,
        plot_ids=(plot.id,),
        node_ids=tuple(node.node_id for node in claimed),
        cycle_ids=(cycle_id,),
        nodes=claimed,
    )


async def _reuse(
    *,
    scenario: Scenario,
    plot_id: UUID,
    org_id: UUID,
    day: date,
    crop_id: int,
    user_id: UUID,
    plots: PlotRepository,
    crops: CropRepository,
    cycles: CropCycleRepository,
    nodes: NodeRepository,
    memberships: MembershipRepository,
    provisioner: ScenarioNodeProvisioner,
) -> ScenarioLoad:
    """What a second load of the same (scenario, day-anchor) finds."""
    plot = await plots.get_for_orgs(plot_id, [org_id])
    if plot is None:
        raise ScenarioNotFoundError(scenario.name)
    cycle_id = await _cycle_for(
        scenario=scenario,
        plot_id=plot.id,
        crop_id=crop_id,
        day=day,
        user_id=user_id,
        plots=plots,
        crops=crops,
        cycles=cycles,
        memberships=memberships,
    )
    claimed = await _nodes_for(
        scenario=scenario,
        plot_id=plot.id,
        org_id=org_id,
        user_id=user_id,
        nodes=nodes,
        plots=plots,
        memberships=memberships,
        provisioner=provisioner,
        rotate=True,
    )
    return ScenarioLoad(
        scenario=scenario.name,
        org_id=org_id,
        plot_ids=(plot.id,),
        node_ids=tuple(node.node_id for node in claimed),
        cycle_ids=(cycle_id,),
        nodes=claimed,
    )


async def _retire(
    *,
    plot_id: UUID,
    org_id: UUID,
    user_id: UUID,
    plots: PlotRepository,
    cycles: CropCycleRepository,
    nodes: NodeRepository,
    memberships: MembershipRepository,
) -> None:
    """What a `replace` load retires: the previous cycle and its nodes.

    A replaced load must not leave a second balance behind: the balance reads
    the plot's active cycle, and the node-offline rule reads the plot's nodes,
    so the old cycle is closed and the old nodes retired before the new ones
    are claimed.
    """
    active = await cycles.get_active_for_plot(plot_id)
    if active is not None:
        await update_cycle(
            user_id=user_id,
            cycle_id=active.id,
            changes={"status": CropCycleStatus.LOST},
            cycles=cycles,
            plots=plots,
            memberships=memberships,
        )
    for node in await nodes.list_for_org(org_id, plot_id=plot_id):
        if node.status is NodeStatus.RETIRED:
            continue
        await update_node(
            user_id=user_id,
            node_id=node.id,
            changes={"status": NodeStatus.RETIRED},
            nodes=nodes,
            plots=plots,
            memberships=memberships,
        )
