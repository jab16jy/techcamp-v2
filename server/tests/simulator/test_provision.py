"""E4 T8: dev-only provisioning of an unclaimed node for the simulator.

Docs have no public API to create an unclaimed node (only
`POST /dev/scenarios/{name}:load`, which is E16's scenario library, docs/04-
api.md:177 and docs/06 §10). Smallest safe choice (see the task report's
Gaps): insert the same `NodeRow`/`SensorRow` the existing test fixtures use
(server/tests/telemetry/test_api.py::_make_unclaimed_node) directly through
the DB session, rather than inventing a new HTTP endpoint."""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.simulator.provision import provision_unclaimed_node
from techcamp.telemetry.adapters.orm import NodeRow, SensorRow
from techcamp.telemetry.adapters.repositories import SqlAlchemyNodeRepository

pytestmark = pytest.mark.anyio

_PROVISION_IN_A_PLAIN_PROCESS = """
import asyncio

from techcamp.shared.db import Base, async_session_factory
from techcamp.simulator.provision import provision_unclaimed_node


async def main() -> None:
    # `NodeRow` points at `organization` and `plot`, and SQLAlchemy resolves
    # foreign keys against `Base.metadata` when it orders the flush, so every
    # ORM module that owns those tables has to be registered first.
    missing = {"organization", "plot"} - set(Base.metadata.tables)
    assert not missing, f"unregistered FK targets: {sorted(missing)}"
    async with async_session_factory() as session:
        print(await provision_unclaimed_node(session))


asyncio.run(main())
"""


async def test_provision_works_in_a_plain_process_and_not_only_under_pytest(
    db_session: AsyncSession,
) -> None:
    """D4: `--provision` raised `NoReferencedTableError` outside pytest, because
    only Alembic's `migrations/env.py` imported the farms/identity ORM modules
    that register the foreign-key targets. The test suite migrated the schema
    before every test body, so that import had always happened for it. A
    subprocess is the only honest reproduction: in-process, Alembic's metadata
    is already loaded no matter what `provision` imports.

    `db_session` is never used in the body: its teardown is the cleanup. The
    subprocess commits its own org, farm, plot and an unclaimed node with its
    own engine, and the whole-table assertion of the next test counts them
    unless the fixture truncates after this one."""
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", _PROVISION_IN_A_PLAIN_PROCESS],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert re.fullmatch(r"SIM-[0-9A-F]{8}", result.stdout.strip())


async def test_provision_unclaimed_node_creates_a_claimable_node_with_one_sensor(
    db_session: AsyncSession,
) -> None:
    claim_code = await provision_unclaimed_node(db_session)

    node = await SqlAlchemyNodeRepository(db_session).get_by_claim_code(claim_code)
    assert node is not None
    assert node.org_id is None
    assert node.plot_id is None

    sensors = (
        (await db_session.execute(select(SensorRow).where(SensorRow.node_id == node.id)))
        .scalars()
        .all()
    )
    assert [s.channel_key for s in sensors] == ["sm_10"]
    assert sensors[0].unit == "%"


async def test_provision_unclaimed_node_generates_a_unique_claim_code_each_call(
    db_session: AsyncSession,
) -> None:
    first = await provision_unclaimed_node(db_session)
    second = await provision_unclaimed_node(db_session)

    assert first != second


async def test_provision_unclaimed_node_leaves_no_node_when_the_sensor_insert_fails(
    db_session: AsyncSession,
) -> None:
    """#40: node and sensor were committed separately, so a failed sensor insert
    left behind a claimable node with no sensors — `claim_node` then returns a
    node the simulator cannot publish anything for. One transaction means the
    node rolls back with its sensor.

    What is owed here is that THIS failed provision left no node behind, not
    that the table happens to be empty, so the assertion is scoped to a
    baseline (#139, T3). `test_provision_works_in_a_plain_process...` commits a
    node of its own through a subprocess, and the truncate that would clear it
    only runs in a `db_session` teardown — that test has no `db_session`, so in
    a random order its node is still in the table when this one runs. A global
    `nodes == []` made the result depend on the order the module happened to
    draw, and it failed under `--randomly-seed=101`."""

    def _fail_on_sensor(session: object, flush_context: object, instances: object) -> None:
        if any(isinstance(obj, SensorRow) for obj in session.new):  # type: ignore[attr-defined]
            raise RuntimeError("sensor insert failed")

    before = set((await db_session.execute(select(NodeRow.id))).scalars().all())

    event.listen(db_session.sync_session, "before_flush", _fail_on_sensor)
    try:
        with pytest.raises(RuntimeError, match="sensor insert failed"):
            await provision_unclaimed_node(db_session)
    finally:
        event.remove(db_session.sync_session, "before_flush", _fail_on_sensor)
    await db_session.rollback()

    after = set((await db_session.execute(select(NodeRow.id))).scalars().all())
    assert after == before


async def test_provision_scenario_creates_the_full_org_farm_plot_cycle_soil_and_nodes(
    db_session: AsyncSession,
) -> None:
    """D-T16.5 (docs/06:409): `sim run --scenario` absorbs creation of org,
    finca, parcela, ciclo y nodos; the nodes come back as claim codes for the
    existing HTTP claim path, not as pre-claimed rows."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from techcamp.farms.adapters.orm import (
        CropCycleRow,
        CropRow,
        FarmRow,
        PlotRow,
        SoilProfileRow,
    )
    from techcamp.identity.adapters.orm import AppUserRow, MembershipRow
    from techcamp.shared.dates import local_today
    from techcamp.simulator.provision import provision_scenario
    from techcamp.simulator.scenario_schema import load_scenario

    now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    provision = await provision_scenario(db_session, load_scenario("el-nino"), now=now)

    membership = (await db_session.execute(select(MembershipRow))).scalar_one()
    assert membership.role == "owner"
    user = (
        await db_session.execute(select(AppUserRow).where(AppUserRow.id == membership.user_id))
    ).scalar_one()
    assert user.phone == provision.phone
    farm = (await db_session.execute(select(FarmRow))).scalar_one()
    assert farm.org_id == membership.org_id
    plot = (await db_session.execute(select(PlotRow))).scalar_one()
    assert plot.farm_id == farm.id
    assert plot.org_id == membership.org_id
    assert plot.irrigation_system == "drip"
    assert str(provision.plot_id) == str(plot.id)

    cycle = (await db_session.execute(select(CropCycleRow))).scalar_one()
    assert cycle.plot_id == plot.id
    assert cycle.status == "active"
    crop = (
        await db_session.execute(select(CropRow).where(CropRow.id == cycle.crop_id))
    ).scalar_one()
    assert crop.code == "maize"
    assert cycle.sown_on == local_today(now) - timedelta(days=48), (
        "#340: scenario A sows 48 days before its anchor"
    )

    soil = (await db_session.execute(select(SoilProfileRow))).scalar_one()
    assert soil.plot_id == plot.id
    assert float(soil.field_capacity_pct) == 23.0
    assert float(soil.wilting_point_pct) == 9.0

    nodes = (
        (await db_session.execute(select(NodeRow).where(NodeRow.plot_id.is_(None)))).scalars().all()
    )
    assert len(nodes) == 2
    assert all(node.claim_code is not None for node in nodes)
    assert {node.claim_code for node in nodes} == {n.claim_code for n in provision.nodes}
    sensors = (await db_session.execute(select(SensorRow).order_by(SensorRow.id))).scalars().all()
    assert len(sensors) == 6  # 2 nodes x (sm_30, at, rh)
    soil_sensor = next(s for s in sensors if s.metric == "soil_moisture")
    assert soil_sensor.channel_key == "sm_30"
    assert soil_sensor.depth_cm == 30


async def test_provision_scenario_supports_the_node_less_rainfed_plot(
    db_session: AsyncSession,
) -> None:
    """docs/06:457 (scenario E): `count: 0` provisions the plot and its cycle
    only — the balance runs on weather alone with `K = 0`."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from techcamp.farms.adapters.orm import PlotRow
    from techcamp.simulator.provision import provision_scenario
    from techcamp.simulator.scenario_schema import load_scenario

    provision = await provision_scenario(
        db_session, load_scenario("dry-spell-rainfed"), now=datetime(2026, 10, 24, tzinfo=UTC)
    )

    assert provision.nodes == ()
    plot = (
        await db_session.execute(select(PlotRow).where(PlotRow.id == provision.plot_id))
    ).scalar_one()
    assert plot.irrigation_system == "none"


async def test_provision_scenario_is_atomic_when_a_sensor_insert_fails(
    db_session: AsyncSession,
) -> None:
    """The #40 lesson of `provision_unclaimed_node`, one level up: a failed
    sensor insert must leave no organization behind either, or a re-run piles
    half-built orgs into the seminar stack."""
    from datetime import UTC, datetime

    from techcamp.identity.adapters.orm import OrganizationRow
    from techcamp.simulator.provision import provision_scenario
    from techcamp.simulator.scenario_schema import load_scenario

    def _fail_on_sensor(session: object, flush_context: object, instances: object) -> None:
        if any(isinstance(obj, SensorRow) for obj in session.new):  # type: ignore[attr-defined]
            raise RuntimeError("sensor insert failed")

    before = set((await db_session.execute(select(OrganizationRow.id))).scalars().all())

    event.listen(db_session.sync_session, "before_flush", _fail_on_sensor)
    try:
        with pytest.raises(RuntimeError, match="sensor insert failed"):
            await provision_scenario(
                db_session,
                load_scenario("el-nino"),
                now=datetime(2026, 10, 24, tzinfo=UTC),
            )
    finally:
        event.remove(db_session.sync_session, "before_flush", _fail_on_sensor)
    await db_session.rollback()

    after = set((await db_session.execute(select(OrganizationRow.id))).scalars().all())
    assert after == before
