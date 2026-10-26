"""E16 T3: the scenario loader route (docs/04-api.md:263; D-T16.2).

`POST /dev/scenarios/{name}:load` prepares the base a scenario needs and
nothing else. Three things about it are worth stating out loud, because each
one is a rule the route can be wrong about:

- it is **idempotent per (scenario, day-anchor)**: a second load of the same
  scenario on the same day answers the same ids instead of a second parcel;
- it is **organization-scoped**: a load that belongs to another organization
  answers 404, never 403 (docs/04-api.md:16);
- it **writes through the modules' own use cases**: the farm, the parcel, the
  cycle and the nodes are the same rows the API writes for a farmer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from importlib import import_module
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import CropCycleRow, FarmRow, PlotRow, SoilProfileRow
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.adapters.security.token_issuer import issue_token
from techcamp.identity.domain.models import Role
from techcamp.irrigation.adapters.jobs import eligible_plot_ids
from techcamp.main import app
from techcamp.shared.credentials import verify_password
from techcamp.shared.dates import local_today
from techcamp.shared.ids import uuid7
from techcamp.simulator.adapters.api import dev_scenarios
from techcamp.simulator.application.load_scenario import ScenarioLoad
from techcamp.simulator.scenario_library import FixtureSchemaError, WeatherFixture
from techcamp.simulator.scenario_schema import Scenario, ScenarioSchemaError
from techcamp.telemetry.adapters.orm import NodeRow, SensorRow
from techcamp.telemetry.domain.errors import InsufficientRoleError as TelemetryInsufficientRoleError

pytestmark = pytest.mark.anyio

loader = import_module("techcamp.simulator.application.load_scenario")
"""The loader's module, and not the `load_scenario` the package re-exports:
the test patches the module's own names."""


@dataclass(frozen=True, slots=True)
class Org:
    org_id: UUID
    user_id: UUID
    token: str


async def _org(session: AsyncSession, *, phone: str) -> Org:
    """One organization with one owner, the way a seminar stack starts."""
    org_id, user_id = uuid7(), uuid7()
    session.add(AppUserRow(id=user_id, phone=phone, full_name="Owner"))
    session.add(OrganizationRow(id=org_id, name=f"Org {org_id.hex[:6]}", kind="individual"))
    await session.commit()
    session.add(MembershipRow(org_id=org_id, user_id=user_id, role="owner"))
    await session.commit()
    return Org(org_id=org_id, user_id=user_id, token=issue_token(str(user_id)))


async def _member(session: AsyncSession, *, org_id: UUID, phone: str, role: Role) -> str:
    """Another member of an organization, with the role the test needs."""
    user_id = uuid7()
    session.add(AppUserRow(id=user_id, phone=phone, full_name=role.value.title()))
    await session.commit()
    session.add(MembershipRow(org_id=org_id, user_id=user_id, role=role.value))
    await session.commit()
    return issue_token(str(user_id))


def _client() -> TestClient:
    return TestClient(app, base_url="http://testserver/api/v1")


def _auth(org: Org) -> dict[str, str]:
    return {"Authorization": f"Bearer {org.token}"}


async def test_the_load_prepares_the_base_and_answers_the_ids(
    db_session: AsyncSession,
) -> None:
    """202 with the ids the simulator publishes to, and the base data behind
    them: the farm, the parcel, the cycle and the claimed nodes."""
    org = await _org(db_session, phone="+573000000001")

    response = _client().post("/dev/scenarios/el-nino:load", json={}, headers=_auth(org))

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["scenario"] == "el-nino"
    assert len(body["plot_ids"]) == 1
    assert len(body["node_ids"]) == 2

    plot = (
        await db_session.execute(select(PlotRow).where(PlotRow.id == UUID(body["plot_ids"][0])))
    ).scalar_one()
    assert plot.org_id == org.org_id
    assert plot.irrigation_system == "drip"

    farm = (
        await db_session.execute(select(FarmRow).where(FarmRow.id == plot.farm_id))
    ).scalar_one()
    assert farm.org_id == org.org_id

    cycle = (
        await db_session.execute(select(CropCycleRow).where(CropCycleRow.plot_id == plot.id))
    ).scalar_one()
    assert cycle.status == "active"

    nodes = (
        (await db_session.execute(select(NodeRow).where(NodeRow.plot_id == plot.id)))
        .scalars()
        .all()
    )
    assert len(nodes) == 2
    assert all(node.org_id == org.org_id for node in nodes)
    assert all(node.claimed_at is not None for node in nodes)


async def test_the_node_carries_the_sensors_the_scenario_publishes(
    db_session: AsyncSession,
) -> None:
    """The node publishes the scenario's channels, so it carries a sensor per
    trajectory (docs/06-diseno-detallado.md:432-434)."""
    org = await _org(db_session, phone="+573000000002")

    response = _client().post("/dev/scenarios/el-nino:load", json={}, headers=_auth(org))
    node_id = UUID(response.json()["node_ids"][0])

    sensors = (
        (await db_session.execute(select(SensorRow).where(SensorRow.node_id == node_id)))
        .scalars()
        .all()
    )

    assert {sensor.channel_key for sensor in sensors} == {
        "soil_moisture_30cm",
        "air_temp",
        "air_rh",
    }
    soil = next(
        sensor
        for sensor in sensors
        if sensor.channel_key == "soil_moisture_30cm"  # gitleaks:allow
    )
    assert soil.metric == "soil_moisture"
    assert soil.depth_cm == 30


async def test_the_node_carries_the_interval_the_scenario_declares(
    db_session: AsyncSession,
) -> None:
    """D-T16.13: node health's silence margin is 3 x the node's interval_s, so
    the node row must carry the cadence the scenario actually reports at — a
    node written down as 900 s while its trajectory runs at 300 s would wait a
    45-minute margin for a 15-minute silence."""
    org = await _org(db_session, phone="+573000000003")

    response = _client().post("/dev/scenarios/node-failure:load", json={}, headers=_auth(org))
    node_id = UUID(response.json()["node_ids"][0])

    node = (await db_session.execute(select(NodeRow).where(NodeRow.id == node_id))).scalar_one()
    assert node.interval_s == 300, "node-failure declares interval_s: 300 (D-T16.13)"


async def test_the_soil_carries_the_scenario_water_limits(
    db_session: AsyncSession,
) -> None:
    """The scenario's soil block is the authority of its arithmetic
    (docs/06-diseno-detallado.md:427), so the parcel's profile carries it and
    not FAO-56's table value."""
    org = await _org(db_session, phone="+573000000003")

    response = _client().post("/dev/scenarios/el-nino:load", json={}, headers=_auth(org))
    plot_id = UUID(response.json()["plot_ids"][0])

    profile = (
        await db_session.execute(select(SoilProfileRow).where(SoilProfileRow.plot_id == plot_id))
    ).scalar_one()

    assert float(profile.field_capacity_pct) == 23.0
    assert float(profile.wilting_point_pct) == 9.0
    assert float(profile.root_depth_cm) == 60.0


async def test_a_second_load_of_the_same_day_answers_the_same_ids(
    db_session: AsyncSession,
) -> None:
    """Idempotent per (scenario, day-anchor): the second load finds what the
    first one left instead of creating a second parcel."""
    org = await _org(db_session, phone="+573000000004")
    client = _client()

    first = client.post("/dev/scenarios/el-nino:load", json={}, headers=_auth(org))
    second = client.post("/dev/scenarios/el-nino:load", json={}, headers=_auth(org))

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["plot_ids"] == first.json()["plot_ids"]
    assert second.json()["node_ids"] == first.json()["node_ids"]

    plots = (await db_session.execute(select(PlotRow))).scalars().all()
    assert len(plots) == 1


async def test_a_replace_recreates_the_base_data(db_session: AsyncSession) -> None:
    """`replace=true` recreates the load: a new parcel, a new cycle and new
    nodes, and the previous ones leave no balance behind."""
    org = await _org(db_session, phone="+573000000005")
    client = _client()

    first = client.post("/dev/scenarios/el-nino:load", json={}, headers=_auth(org))
    replaced = client.post(
        "/dev/scenarios/el-nino:load", json={"replace": True}, headers=_auth(org)
    )

    assert replaced.status_code == 202
    assert replaced.json()["plot_ids"] != first.json()["plot_ids"]
    assert replaced.json()["node_ids"] != first.json()["node_ids"]

    old_plot_id = UUID(first.json()["plot_ids"][0])
    old_cycle = (
        await db_session.execute(select(CropCycleRow).where(CropCycleRow.plot_id == old_plot_id))
    ).scalar_one()
    assert old_cycle.status != "active"

    old_nodes = (
        (await db_session.execute(select(NodeRow).where(NodeRow.plot_id == old_plot_id)))
        .scalars()
        .all()
    )
    assert all(node.status == "retired" for node in old_nodes)


async def test_a_day_that_has_not_happened_yet_is_unprocessable(
    db_session: AsyncSession,
) -> None:
    """Like `/dev/jobs`: a future day is 422, not a scenario nobody can load."""
    org = await _org(db_session, phone="+573000000006")
    tomorrow = local_today() + timedelta(days=1)

    response = _client().post(
        "/dev/scenarios/el-nino:load", json={"day": tomorrow.isoformat()}, headers=_auth(org)
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_an_unknown_scenario_is_not_found(db_session: AsyncSession) -> None:
    org = await _org(db_session, phone="+573000000007")

    response = _client().post("/dev/scenarios/no-existe:load", json={}, headers=_auth(org))

    assert response.status_code == 404


async def test_a_load_that_belongs_to_another_organization_is_not_found(
    db_session: AsyncSession,
) -> None:
    """The load is a resource of the organization that owns it, so another
    organization answers 404 — never 403, which would reveal that it exists
    (docs/04-api.md:16)."""
    first = await _org(db_session, phone="+573000000008")
    second = await _org(db_session, phone="+573000000009")
    client = _client()

    loaded = client.post("/dev/scenarios/el-nino:load", json={}, headers=_auth(first))
    assert loaded.status_code == 202

    response = client.post("/dev/scenarios/el-nino:load", json={}, headers=_auth(second))

    assert response.status_code == 404


async def test_a_caller_without_a_session_is_unauthorized(db_session: AsyncSession) -> None:
    response = _client().post("/dev/scenarios/el-nino:load", json={})

    assert response.status_code == 401


async def test_the_load_enqueues_nothing(db_session: AsyncSession) -> None:
    """docs/04-api.md:263: the route prepares base data and nothing else. The
    backfill is the CLI's, so the load leaves no job behind — the parcel's own
    write path defers a provider fetch for a cell with no rows, and the
    fixture lands first precisely so that it does not."""
    org = await _org(db_session, phone="+573000000012")

    response = _client().post("/dev/scenarios/el-nino:load", json={}, headers=_auth(org))
    assert response.status_code == 202

    jobs = (await db_session.execute(text("SELECT count(*) FROM procrastinate_jobs"))).scalar_one()
    assert jobs == 0


async def test_a_caller_without_an_organization_is_not_found(db_session: AsyncSession) -> None:
    """The load belongs to an organization; a caller that belongs to none has
    no organization to load into, so there is nothing to find."""
    user_id = uuid7()
    db_session.add(AppUserRow(id=user_id, phone="+573000000013", full_name="Sin organización"))
    await db_session.commit()
    token = issue_token(str(user_id))

    response = _client().post(
        "/dev/scenarios/el-nino:load", json={}, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 404


async def test_a_caller_with_several_organizations_is_unprocessable(
    db_session: AsyncSession,
) -> None:
    """The body names no organization (docs/04-api.md:263), so a caller that
    belongs to several of them has no way to say which one the scenario is for:
    the loader says so instead of picking one."""
    user_id = uuid7()
    db_session.add(AppUserRow(id=user_id, phone="+573000000014", full_name="Dos organizaciones"))
    for index in range(2):
        org_id = uuid7()
        db_session.add(OrganizationRow(id=org_id, name=f"Org {index}", kind="individual"))
        await db_session.commit()
        db_session.add(MembershipRow(org_id=org_id, user_id=user_id, role="owner"))
    await db_session.commit()
    token = issue_token(str(user_id))

    response = _client().post(
        "/dev/scenarios/el-nino:load", json={}, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 422


async def test_a_reload_rotates_the_node_credential(db_session: AsyncSession) -> None:
    """The claim's password is only readable once, so a reload that reuses the
    node has to hand a credential that works now — and a caller that published
    with an earlier response has to use the latest one."""
    org = await _org(db_session, phone="+573000000015")
    client = _client()

    first = client.post("/dev/scenarios/el-nino:load", json={}, headers=_auth(org))
    second = client.post("/dev/scenarios/el-nino:load", json={}, headers=_auth(org))

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json()["node_ids"] == first.json()["node_ids"]
    password = second.json()["node_credentials"][0]["mqtt_password"]
    assert password, "the rotated credential is a password, not an empty string"
    assert password != (first.json()["node_credentials"][0]["mqtt_password"])
    node = (
        await db_session.execute(
            select(NodeRow).where(NodeRow.id == UUID(second.json()["node_ids"][0]))
        )
    ).scalar_one()
    assert verify_password(password, node.credential_hash), (
        "the rotated password is the one the node's stored hash was written for"
    )


async def test_a_rainfed_replace_leaves_one_parcel_in_the_balance_scope(
    db_session: AsyncSession,
) -> None:
    """A replaced rainfed parcel leaves the daily balance's scope (#333).

    The loader closes the old cycle and retires the old nodes, but the plot
    stays: the scenario is the same, and the parcel is its base data. The
    balance used to take every rainfed plot, so the replaced one stayed in its
    scope and received a `delay_sowing` recommendation every day - advice about
    a parcel nobody farms any more, on the demo's screen.

    The balance takes the plots with an active cycle now, so only the parcel of
    the current load is in its scope.
    """
    org = await _org(db_session, phone="+573000000016")
    client = _client()

    first = client.post("/dev/scenarios/dry-spell-rainfed:load", json={}, headers=_auth(org))
    replaced = client.post(
        "/dev/scenarios/dry-spell-rainfed:load", json={"replace": True}, headers=_auth(org)
    )
    replaced_again = client.post(
        "/dev/scenarios/dry-spell-rainfed:load", json={"replace": True}, headers=_auth(org)
    )

    assert first.status_code == 202
    assert replaced.status_code == 202
    assert replaced_again.status_code == 202
    old_plot_id = UUID(first.json()["plot_ids"][0])
    current_plot_id = UUID(replaced_again.json()["plot_ids"][0])
    still_there = (
        await db_session.execute(select(PlotRow).where(PlotRow.id == old_plot_id))
    ).scalar_one_or_none()
    assert still_there is not None, "the replaced rainfed parcel is still in the database"

    eligible = await eligible_plot_ids(db_session)

    assert eligible == [current_plot_id], (
        "#333: only the parcel of the current load is in the balance's scope"
    )
    assert old_plot_id not in eligible


async def test_a_read_only_caller_is_refused_without_writing_anything(
    db_session: AsyncSession,
) -> None:
    """A role that may not write is refused before the load writes anything.
    The weather fixture is the first thing the loader writes, so a refusal
    that came after it would leave the shared reference rows behind."""
    org = await _org(db_session, phone="+573000000017")
    token = await _member(db_session, org_id=org.org_id, phone="+573000000018", role=Role.PRODUCER)

    response = _client().post(
        "/dev/scenarios/el-nino:load", json={}, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/problem+json")
    weather_rows = (
        await db_session.execute(text("SELECT count(*) FROM weather_daily"))
    ).scalar_one()
    assert weather_rows == 0, "the refused load wrote no weather"
    plots = (await db_session.execute(select(PlotRow))).scalars().all()
    assert plots == [], "the refused load wrote no parcel"


async def test_a_read_only_caller_is_refused_on_the_reload(db_session: AsyncSession) -> None:
    """The reload rotates the node's credential, which is a telemetry write, so
    the same read-only role is refused there too — with the documented 403
    instead of a stack trace."""
    org = await _org(db_session, phone="+573000000019")
    client = _client()
    loaded = client.post("/dev/scenarios/el-nino:load", json={}, headers=_auth(org))
    assert loaded.status_code == 202
    token = await _member(db_session, org_id=org.org_id, phone="+573000000020", role=Role.VIEWER)

    response = client.post(
        "/dev/scenarios/el-nino:load", json={}, headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_the_route_translates_the_telemetry_role_error(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Telemetry keeps its own `InsufficientRoleError` (`domain` never imports
    another module's `domain`), so the route has to translate that one too: a
    role that may not write is a 403, never a stack trace."""
    org = await _org(db_session, phone="+573000000021")

    async def refuse(**kwargs: object) -> ScenarioLoad:
        raise TelemetryInsufficientRoleError(Role.PRODUCER)

    monkeypatch.setattr(dev_scenarios, "load_scenario", refuse)

    response = _client().post("/dev/scenarios/el-nino:load", json={}, headers=_auth(org))

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_a_broken_scenario_contract_answers_a_problem(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scenario's own contract not holding is the library's problem, not the
    caller's, but it still answers with a problem document instead of a bare
    traceback."""
    org = await _org(db_session, phone="+573000000022")

    def broken(name: str) -> Scenario:
        raise ScenarioSchemaError("the recorded fixture's pin does not match")

    monkeypatch.setattr(dev_scenarios, "load_scenario_definition", broken)

    response = _client().post("/dev/scenarios/el-nino:load", json={}, headers=_auth(org))

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_a_missing_fixture_answers_a_broken_contract(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scenario is in the library but the file it pins is not: that is a
    broken contract (500), not a scenario nobody can load (404)."""
    org = await _org(db_session, phone="+573000000023")

    def missing(name: str) -> Scenario:
        raise FileNotFoundError("the fixture the scenario pins is not there")

    monkeypatch.setattr(dev_scenarios, "load_scenario_definition", missing)

    response = _client().post("/dev/scenarios/el-nino:load", json={}, headers=_auth(org))

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_an_unreadable_fixture_answers_a_broken_contract(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fixture the library cannot read is the same broken contract, and it
    answers the same way."""
    org = await _org(db_session, phone="+573000000024")

    def unreadable(name: str) -> WeatherFixture:
        raise FixtureSchemaError("the fixture is not the recorded one")

    monkeypatch.setattr(dev_scenarios, "load_fixture", unreadable)

    response = _client().post("/dev/scenarios/el-nino:load", json={}, headers=_auth(org))

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_a_scenario_file_that_cannot_be_read_answers_a_problem(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file the library cannot open is the library's problem, not the
    caller's, but it still answers with a problem document."""
    org = await _org(db_session, phone="+573000000025")

    def unreadable(name: str) -> Scenario:
        raise PermissionError("the scenario file cannot be read")

    monkeypatch.setattr(dev_scenarios, "load_scenario_definition", unreadable)

    response = _client().post("/dev/scenarios/el-nino:load", json={}, headers=_auth(org))

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_the_pre_flight_consults_the_telemetry_rule_too(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The load writes farms' rows and telemetry's, and each module states its
    own write roles. The pre-flight asks both, so a role one of them rejects
    is refused before anything is written — not after the weather landed."""
    org = await _org(db_session, phone="+573000000026")

    def refuse(role: Role) -> None:
        raise TelemetryInsufficientRoleError(role)

    monkeypatch.setattr(loader, "ensure_can_write_telemetry", refuse)

    response = _client().post("/dev/scenarios/el-nino:load", json={}, headers=_auth(org))

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/problem+json")
    weather_rows = (
        await db_session.execute(text("SELECT count(*) FROM weather_daily"))
    ).scalar_one()
    assert weather_rows == 0, "the refused load wrote no weather"
