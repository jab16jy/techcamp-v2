"""E4 T8: dev-only provisioning of an unclaimed node for the simulator.

Docs have no public API to create an unclaimed node (only
`POST /dev/scenarios/{name}:load`, which is E16's scenario library, docs/04-
api.md:177 and docs/06 §10). Smallest safe choice (see the task report's
Gaps): insert the same `NodeRow`/`SensorRow` the existing test fixtures use
(server/tests/telemetry/test_api.py::_make_unclaimed_node) directly through
the DB session, rather than inventing a new HTTP endpoint."""

from __future__ import annotations

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


def test_provision_works_in_a_plain_process_and_not_only_under_pytest() -> None:
    """D4: `--provision` raised `NoReferencedTableError` outside pytest, because
    only Alembic's `migrations/env.py` imported the farms/identity ORM modules
    that register the foreign-key targets. The test suite migrated the schema
    before every test body, so that import had always happened for it. A
    subprocess is the only honest reproduction: in-process, Alembic's metadata
    is already loaded no matter what `provision` imports."""
    result = subprocess.run(
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
