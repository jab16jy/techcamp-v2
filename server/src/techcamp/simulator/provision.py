"""Dev-only provisioning of an unclaimed node for the simulator.

Docs have no public API to create an unclaimed node (only `POST
/dev/scenarios/{name}:load`, E16's scenario library, docs/04-api.md:177 and
docs/06 §10). Smallest safe choice (task report Gaps): insert the same
`NodeRow`/`SensorRow` the existing test fixtures use directly through the DB
session (server/tests/telemetry/test_api.py::_make_unclaimed_node), rather
than inventing a new HTTP endpoint. This module is only ever used by the
simulator CLI's `--provision` flag, in the seminar profile."""

from __future__ import annotations

import secrets

from sqlalchemy.ext.asyncio import AsyncSession

# Composition root, for the same reason `migrations/env.py` imports them: `NodeRow`
# carries foreign keys into the farms and identity tables, and SQLAlchemy resolves
# those against `Base.metadata` when it orders the flush. Importing only this
# module's ORM left the targets unregistered, so `--provision` raised
# `NoReferencedTableError` in a plain process — the test suite only passed because
# Alembic's environment had already imported the same modules.
from techcamp.farms.adapters import orm as _farms_orm  # noqa: F401
from techcamp.identity.adapters import orm as _identity_orm  # noqa: F401
from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.orm import NodeRow, SensorRow

DEFAULT_INTERVAL_S = 900
"""docs/06 §10 scenario yaml example: `interval_s: 900`."""


async def provision_unclaimed_node(session: AsyncSession) -> str:
    """Inserts one unclaimed node with a single soil-moisture sensor
    (`sm_10`, `%`, matching docs/03-modelo-datos.md:463-467's own two-point
    example) and returns its claim code."""
    node_id = uuid7()
    claim_code = f"SIM-{secrets.token_hex(4).upper()}"
    session.add(
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
    await session.commit()
    session.add(
        SensorRow(
            node_id=node_id, channel_key="sm_10", metric="soil_moisture", depth_cm=10, unit="%"
        )
    )
    await session.commit()
    return claim_code
