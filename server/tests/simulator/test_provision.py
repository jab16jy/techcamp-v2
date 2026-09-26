"""E4 T8: dev-only provisioning of an unclaimed node for the simulator.

Docs have no public API to create an unclaimed node (only
`POST /dev/scenarios/{name}:load`, which is E16's scenario library, docs/04-
api.md:177 and docs/06 §10). Smallest safe choice (see the task report's
Gaps): insert the same `NodeRow`/`SensorRow` the existing test fixtures use
(server/tests/telemetry/test_api.py::_make_unclaimed_node) directly through
the DB session, rather than inventing a new HTTP endpoint."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.simulator.provision import provision_unclaimed_node
from techcamp.telemetry.adapters.orm import SensorRow
from techcamp.telemetry.adapters.repositories import SqlAlchemyNodeRepository

pytestmark = pytest.mark.anyio


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
