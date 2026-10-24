"""The dev-only writes the scenario loader needs (E16 T3).

Two of them, both outside the use cases the API owns:

- **The unclaimed node and its sensors.** Docs have no public API to create an
  unclaimed node — the same gap `simulator/provision.py` documents — so the
  loader inserts the rows the test fixtures use, with the scenario's own
  sensors, and then claims the node through the real use case.
- **The lookup of a previous load.** The farms' repositories are
  organization-scoped by design, and the loader has to see a load that belongs
  to *another* organization in order to answer 404 instead of creating a
  second copy of the same scenario. It is a read, never a write.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Composition root, for the same reason `simulator/provision.py` imports them:
# `NodeRow` carries foreign keys into the farms and identity tables, and
# SQLAlchemy resolves those against `Base.metadata` when it orders the flush.
from techcamp.farms.adapters import orm as _farms_orm  # noqa: F401
from techcamp.farms.adapters.orm import PlotRow
from techcamp.identity.adapters import orm as _identity_orm  # noqa: F401
from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.orm import NodeRow, SensorRow

from ..application.load_scenario import ScenarioPlotRef, ScenarioSensor

DEFAULT_INTERVAL_S = 900
"""docs/06 §10's scenario yaml example: `interval_s: 900`."""


class SqlAlchemyScenarioNodeProvisioner:
    """Inserts one unclaimed node carrying the scenario's sensors."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def provision(self, *, sensors: Sequence[ScenarioSensor]) -> str:
        """One transaction for the node and its sensors: a node with no sensor
        is claimable but can never publish a reading, which reads as a broken
        node in the seminar instead of a failed provisioning."""
        node_id = uuid7()
        claim_code = f"SIM-{secrets.token_hex(4).upper()}"
        self._session.add(
            NodeRow(
                id=node_id,
                org_id=None,
                plot_id=None,
                transport="wifi",
                dev_eui=None,
                claim_code=claim_code,
                credential_hash="unclaimed",
                firmware=None,
                interval_s=DEFAULT_INTERVAL_S,
                claimed_at=None,
                last_seen_at=None,
                status="provisioned",
            )
        )
        for sensor in sensors:
            self._session.add(
                SensorRow(
                    node_id=node_id,
                    channel_key=sensor.channel_key,
                    metric=sensor.metric,
                    depth_cm=sensor.depth_cm,
                    unit=sensor.unit,
                )
            )
        await self._session.commit()
        return claim_code


class SqlAlchemyScenarioLoadRegistry:
    """The (scenario, day-anchor) registry: the plot's own name.

    The newest match wins, and the newest is the highest id: the ids are
    UUIDv7 (docs/03-modelo-datos.md), which sort by creation time, so a
    `replace` load — which creates a new plot under the same name — is the one
    a later load finds.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_plot(self, name: str) -> ScenarioPlotRef | None:
        row = (
            await self._session.execute(
                select(PlotRow).where(PlotRow.name == name).order_by(PlotRow.id.desc()).limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return ScenarioPlotRef(plot_id=row.id, org_id=row.org_id)
