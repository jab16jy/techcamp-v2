"""Seminar-only scenario loader (docs/04-api.md:263; D-T16.2). Registered only
when the profile is seminar.

`POST /dev/scenarios/{name}:load { day?, replace? }` prepares the base data a
scenario needs and nothing else: the farm, the plot, the soil, the cycle and
the nodes through the modules' own use cases, and the scenario's weather
fixture in `weather_daily` through the weather repository. The backfill is the
CLI's, over MQTT (docs/06-diseno-detallado.md:424-426) — this route neither
enqueues nor publishes.

The `day` is the scenario's day 0. It defaults to the day that puts the
scenario's forecast window on today: the fixture's observed rows are the days
the balance already consolidated, and its forecast rows are the seven days the
recommendation reads, so the anchor is today minus the observed days. A day
that has not happened yet is refused with 422, like `/dev/jobs`
(docs/04-api.md:263).
"""

from __future__ import annotations

import datetime
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from techcamp.farms.adapters.api.deps import (
    CropCycleRepoDep,
    CropRepoDep,
    FarmRepoDep,
    PlotRepoDep,
    SoilProfileRepoDep,
)
from techcamp.farms.domain.errors import InsufficientRoleError
from techcamp.identity.adapters.api.deps import CurrentUserId, MembershipRepoDep
from techcamp.identity.domain.errors import NotAMemberError
from techcamp.shared.dates import local_today
from techcamp.shared.db import SessionDep
from techcamp.shared.errors import ProblemError
from techcamp.telemetry.adapters.api.deps import NodeRepoDep
from techcamp.telemetry.domain.errors import InsufficientRoleError as TelemetryInsufficientRoleError
from techcamp.weather.adapters.api.deps import WeatherRepoDep

from ...application.load_scenario import (
    ScenarioAmbiguousOrgError,
    ScenarioDataInAnotherOrgError,
    ScenarioLoad,
    ScenarioNotFoundError,
    ScenarioOrgNotFoundError,
    load_scenario,
)
from ...scenario_library import SCENARIOS_DIR, load_fixture
from ...scenario_schema import ScenarioSchemaError
from ...scenario_schema import load_scenario as load_scenario_definition
from ..scenario_data import SqlAlchemyScenarioLoadRegistry, SqlAlchemyScenarioNodeProvisioner

router = APIRouter(prefix="/dev/scenarios", tags=["dev-scenarios"])

_FUTURE_DAY_DETAIL = (
    "Only today or past days can be loaded: the scenario's days start at the anchor, and a "
    "day that has not happened yet has no weather to consolidate."
)


class ScenarioLoadRequest(BaseModel):
    """`{ day?, replace? }`: the scenario's day 0, and whether to recreate what
    a previous load left (docs/04-api.md:263). A `date` field, so a malformed
    value is FastAPI's 422 rather than a failure inside the loader."""

    day: datetime.date | None = None
    replace: bool = False


class NodeCredential(BaseModel):
    """One node's MQTT credential.

    The claim returns the password once and only its hash is stored
    (docs/06-diseno-detallado.md §2), so the load is the only place the
    simulator can read it: it hands the credential back, the way the claim
    endpoint does.
    """

    node_id: UUID
    mqtt_username: str
    mqtt_password: str


class ScenarioLoadResponse(BaseModel):
    """What the load prepared: the ids the simulator publishes to.

    `cycle_ids` and `node_credentials` are additions to the contract's
    `{ scenario, plot_ids, node_ids }` (docs/04-api.md:263): fields are only
    added to a versioned route (docs/04-api.md's "Versionado"), and both are
    needed — the first to see the cycle a `replace` recreated, the second to
    publish over MQTT.
    """

    scenario: str
    plot_ids: list[UUID]
    node_ids: list[UUID]
    cycle_ids: list[UUID]
    node_credentials: list[NodeCredential]


def _default_anchor(observed_days: int) -> datetime.date:
    """The scenario's day 0 when the caller names none.

    The fixture's forecast rows are the seven days the recommendation reads
    (docs/06-diseno-detallado.md:246), so the anchor is the day before them:
    today minus the observed days.
    """
    return local_today() - datetime.timedelta(days=observed_days)


def _scenario_is_in_the_library(name: str) -> bool:
    """Whether the scenario itself is in the library.

    Loading a scenario reads the YAML and the fixture it pins, and a missing
    file is one of the two: the scenario is not there (404) or the scenario is
    there and a file it needs is not (a broken contract, 500). The YAML's own
    existence tells them apart.
    """
    return (SCENARIOS_DIR / f"{name}.yaml").exists()


@router.post("/{name}:load", response_model=ScenarioLoadResponse, status_code=202)
async def load_scenario_route(
    name: str,
    payload: ScenarioLoadRequest,
    user_id: CurrentUserId,
    session: SessionDep,
    farms: FarmRepoDep,
    plots: PlotRepoDep,
    crops: CropRepoDep,
    cycles: CropCycleRepoDep,
    soil_profiles: SoilProfileRepoDep,
    nodes: NodeRepoDep,
    memberships: MembershipRepoDep,
    weather: WeatherRepoDep,
) -> ScenarioLoadResponse:
    """Prepare the base data one scenario needs, idempotently."""
    try:
        scenario = load_scenario_definition(name)
    except FileNotFoundError as error:
        if _scenario_is_in_the_library(name):
            # The scenario is in the library but a file it needs is not: the
            # contract is broken, not the caller's request, so it is not a
            # "no scenario named X".
            raise ProblemError(
                status=500,
                title="Scenario contract is broken",
                detail=f"The scenario {name!r} does not satisfy its own contract.",
            ) from error
        raise ProblemError(
            status=404, title="Scenario not found", detail=f"No scenario named {name!r}."
        ) from error
    except ScenarioSchemaError as error:
        # The scenario's own contract does not hold — its recorded fixture's pin
        # does not match the file, or a block is off the schema. That is the
        # library's problem, not the caller's, but it still answers with a
        # problem document instead of a bare traceback.
        raise ProblemError(
            status=500,
            title="Scenario contract is broken",
            detail=f"The scenario {name!r} does not satisfy its own contract.",
        ) from error
    except OSError as error:
        # A file the library cannot open — a permission the operator has to
        # fix — is the library's problem too, and it answers the same way
        # instead of escaping as a traceback.
        raise ProblemError(
            status=500,
            title="Scenario cannot be read",
            detail=f"The scenario {name!r} could not be read from the library.",
        ) from error
    try:
        fixture = load_fixture(scenario.weather_fixture.removesuffix(".json"))
    except (OSError, ValueError) as error:
        # A fixture that cannot be read or does not satisfy the contract is the
        # same broken contract: `FixtureSchemaError` is a `ValueError`, and a
        # file that is not the recorded JSON is one too.
        raise ProblemError(
            status=500,
            title="Scenario contract is broken",
            detail=f"The fixture of {name!r} does not satisfy the recorded contract.",
        ) from error

    day = payload.day if payload.day is not None else _default_anchor(len(fixture.observed))
    if day > local_today():
        raise ProblemError(status=422, title="Unprocessable day", detail=_FUTURE_DAY_DETAIL)

    crop_id = next(
        (crop.id for crop in await crops.list_all() if crop.code == scenario.plot.crop), None
    )
    if crop_id is None:
        raise ProblemError(
            status=422,
            title="Unknown crop",
            detail=f"The catalog has no crop named {scenario.plot.crop!r}.",
        )

    try:
        loaded = await load_scenario(
            name=name,
            day=day,
            replace=payload.replace,
            user_id=user_id,
            crop_id=crop_id,
            lat=fixture.lat,
            lon=fixture.lon,
            fixture=fixture,
            farms=farms,
            plots=plots,
            crops=crops,
            cycles=cycles,
            soil_profiles=soil_profiles,
            nodes=nodes,
            memberships=memberships,
            weather=weather,
            provisioner=SqlAlchemyScenarioNodeProvisioner(session),
            registry=SqlAlchemyScenarioLoadRegistry(session),
        )
    except ScenarioNotFoundError as error:
        raise ProblemError(
            status=404, title="Scenario not found", detail=f"No scenario named {name!r}."
        ) from error
    except ScenarioOrgNotFoundError as error:
        raise ProblemError(
            status=404,
            title="Organization not found",
            detail="The caller belongs to no organization, so there is nothing to load into.",
        ) from error
    except ScenarioAmbiguousOrgError as error:
        raise ProblemError(
            status=422,
            title="Ambiguous organization",
            detail=(
                "The caller belongs to several organizations and the body names none, so the "
                "scenario has no organization to be loaded into."
            ),
        ) from error
    except ScenarioDataInAnotherOrgError as error:
        raise ProblemError(
            status=404,
            title="Scenario not found",
            detail="That scenario was loaded in another organization.",
        ) from error
    except NotAMemberError as error:
        raise ProblemError(status=404, title="Organization not found") from error
    except (InsufficientRoleError, TelemetryInsufficientRoleError) as error:
        # Two modules, two role errors: `domain` never imports another module's
        # `domain`, so telemetry keeps its own `InsufficientRoleError`. Both
        # mean the same thing to the caller — the role may not write — and
        # both are the documented 403.
        raise ProblemError(status=403, title="Role cannot load scenarios") from error

    return _response(loaded)


def _response(loaded: ScenarioLoad) -> ScenarioLoadResponse:
    return ScenarioLoadResponse(
        scenario=loaded.scenario,
        plot_ids=list(loaded.plot_ids),
        node_ids=list(loaded.node_ids),
        cycle_ids=list(loaded.cycle_ids),
        node_credentials=[
            NodeCredential(
                node_id=node.node_id,
                mqtt_username=node.mqtt_username,
                mqtt_password=node.mqtt_password,
            )
            for node in loaded.nodes
        ],
    )
