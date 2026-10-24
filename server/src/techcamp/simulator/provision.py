"""Dev-only provisioning for the simulator.

Docs have no public API to create an unclaimed node (only `POST
/dev/scenarios/{name}:load`, E16's scenario library, docs/04-api.md:177 and
docs/06 §10). Smallest safe choice (task report Gaps): insert the same rows the
existing test fixtures use directly through the DB session
(server/tests/telemetry/test_api.py::_make_unclaimed_node), rather than
inventing a new HTTP endpoint. Both functions here are only ever used by the
simulator CLI, in the seminar profile.

`provision_scenario` (E16 T2, D-T16.5, docs/06:409) extends the same shortcut
one level up for `sim run --scenario`: it creates the org, member, farm, plot,
crop cycle, soil profile and unclaimed nodes the scenario declares, in one
transaction, and hands back claim codes — claiming still goes through the real
HTTP claim path, so the run exercises the same flow a physical rollout would.
The #214 `minio-init` stall stays a filed pre-step: the prerequisite is a
healthy stack (`just db-up`), not this module."""

from __future__ import annotations

import math
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Composition root, for the same reason `migrations/env.py` imports them: `NodeRow`
# carries foreign keys into the farms and identity tables, and SQLAlchemy resolves
# those against `Base.metadata` when it orders the flush. Importing only this
# module's ORM left the targets unregistered, so `--provision` raised
# `NoReferencedTableError` in a plain process — the test suite only passed because
# Alembic's environment had already imported the same modules.
from techcamp.farms.adapters import orm as _farms_orm  # noqa: F401
from techcamp.farms.adapters.orm import CropCycleRow, CropRow, FarmRow, PlotRow, SoilProfileRow
from techcamp.identity.adapters import orm as _identity_orm  # noqa: F401
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.shared.dates import local_today
from techcamp.shared.ids import uuid7
from techcamp.simulator.scenario_schema import Scenario
from techcamp.telemetry.adapters.orm import NodeRow, SensorRow

DEFAULT_INTERVAL_S = 900
"""docs/06 §10 scenario yaml example: `interval_s: 900`."""

_PLOT_CENTER = (10.9, -74.1)
"""The Colombian Caribbean cell the seminar demos (docs/06 §10's fixtures are
`*-caribe.json`); the plot is a square around it."""

_M_PER_DEG_LAT = 111_320.0
"""Spherical approximation: the demo plot only needs a plausible area, not a
geodesic survey."""


@dataclass(frozen=True, slots=True)
class ProvisionedNode:
    """One unclaimed node the scenario created, identified by its 1-based
    ordinal in the scenario's `nodes.count` (the same ordinal that names its
    faults and its per-node calibration)."""

    ordinal: int
    claim_code: str


@dataclass(frozen=True, slots=True)
class ScenarioProvision:
    """What `sim run --scenario` needs back to claim and publish: the member
    phone to log in with (dev OTP), the plot to claim onto, and the nodes."""

    phone: str
    plot_id: UUID
    nodes: tuple[ProvisionedNode, ...]


def _claim_code() -> str:
    return f"SIM-{secrets.token_hex(4).upper()}"


def _square_boundary(area_ha: float) -> str:
    """A closed WKT square whose side matches the scenario's declared area, so
    the plot's computed `area_ha` (docs/03:98) reads what the YAML promised.
    A floor of 10 m keeps the polygon valid for a `0` area the schema allows."""
    side_m = max(math.sqrt(max(area_ha, 0.0) * 10_000.0), 10.0)
    half_lat = side_m / 2.0 / _M_PER_DEG_LAT
    half_lon = half_lat / math.cos(math.radians(_PLOT_CENTER[0]))
    lat, lon = _PLOT_CENTER
    corners = [
        (lon - half_lon, lat - half_lat),
        (lon + half_lon, lat - half_lat),
        (lon + half_lon, lat + half_lat),
        (lon - half_lon, lat + half_lat),
        (lon - half_lon, lat - half_lat),
    ]
    return "POLYGON((" + ", ".join(f"{x:.7f} {y:.7f}" for x, y in corners) + "))"


async def provision_unclaimed_node(session: AsyncSession) -> str:
    """Inserts one unclaimed node with a single soil-moisture sensor
    (`sm_10`, `%`, matching docs/03-modelo-datos.md:463-467's own two-point
    example) and returns its claim code.

    One transaction for both rows: a node with no sensor is claimable but can
    never publish a reading, which reads as a broken node in the seminar
    instead of a failed provisioning."""
    node_id = uuid7()
    claim_code = _claim_code()
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
    session.add(
        SensorRow(
            node_id=node_id, channel_key="sm_10", metric="soil_moisture", depth_cm=10, unit="%"
        )
    )
    await session.commit()
    return claim_code


async def provision_scenario(
    session: AsyncSession, scenario: Scenario, *, now: datetime
) -> ScenarioProvision:
    """Creates everything the scenario declares (docs/06:409, D-T16.5): org
    with one owner member (the dev-OTP login identity), farm, plot with the
    scenario's irrigation system and area, its crop cycle anchored
    `sown_days_ago` local days back, the soil profile the balance's arithmetic
    reads (θFC/θWP, docs/06:427), and one unclaimed node per `nodes.count`
    carrying the scenario's channels — soil at `soil_moisture_depth_cm` plus
    the air pair (docs/04:304, docs/06:432-434).

    One transaction for all of it, for the same reason as
    `provision_unclaimed_node`: a half-built org is worse than a failed run,
    because the re-run piles another one into the seminar stack."""
    org_id, user_id = uuid7(), uuid7()
    phone = f"+57310{secrets.randbelow(10_000_000):07d}"
    farm_id = uuid7()
    plot_id = uuid7()
    depth = scenario.nodes.soil_moisture_depth_cm

    # Staged flushes, one transaction: identity, farms and telemetry map their
    # tables without cross-module `relationship()`s, so the unit of work has no
    # mapper-level dependency to order `membership` after `organization`, or
    # `plot` after `farm` — the same reason `tests/simulator/helpers.py`
    # inserts in stages. `flush` is not `commit`: everything still lands or
    # nothing does.
    session.add(OrganizationRow(id=org_id, name=f"Simulador {scenario.name}", kind="individual"))
    session.add(AppUserRow(id=user_id, phone=phone))
    await session.flush()

    session.add(MembershipRow(org_id=org_id, user_id=user_id, role="owner"))
    session.add(
        FarmRow(
            id=farm_id,
            org_id=org_id,
            name=f"Finca {scenario.name}",
            municipality_code="47001",
            location=f"POINT({_PLOT_CENTER[1]} {_PLOT_CENTER[0]})",
        )
    )
    await session.flush()

    session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=_square_boundary(scenario.plot.area_ha),
            irrigation_system=scenario.plot.irrigation_system,
        )
    )
    await session.flush()
    crop_id = (
        await session.execute(select(CropRow.id).where(CropRow.code == scenario.plot.crop))
    ).scalar_one()
    session.add(
        CropCycleRow(
            id=uuid7(),
            plot_id=plot_id,
            crop_id=crop_id,
            sown_on=local_today(now) - timedelta(days=scenario.plot.sown_days_ago),
            expected_harvest_on=None,
            status="active",
        )
    )
    session.add(
        SoilProfileRow(
            plot_id=plot_id,
            source="fao56_texture",
            ph=None,
            organic_matter_pct=None,
            texture=scenario.plot.soil,
            field_capacity_pct=Decimal(str(scenario.soil.field_capacity_pct)),
            wilting_point_pct=Decimal(str(scenario.soil.wilting_point_pct)),
            root_depth_cm=None,
        )
    )
    await session.flush()

    nodes: list[ProvisionedNode] = []
    for ordinal in range(1, scenario.nodes.count + 1):
        assert depth is not None  # schema: count > 0 carries the depth
        node_id = uuid7()
        provisioned = ProvisionedNode(ordinal=ordinal, claim_code=_claim_code())
        nodes.append(provisioned)
        session.add(
            NodeRow(
                id=node_id,
                org_id=None,
                plot_id=None,
                transport="wifi",
                dev_eui=None,
                claim_code=provisioned.claim_code,
                credential_hash="unclaimed",
                firmware=None,
                interval_s=scenario.interval_s,
                claimed_at=None,
                last_seen_at=None,
                status="provisioned",
            )
        )
        session.add(
            SensorRow(
                node_id=node_id,
                channel_key=f"sm_{depth}",
                metric="soil_moisture",
                depth_cm=depth,
                unit="%",
            )
        )
        session.add(
            SensorRow(
                node_id=node_id, channel_key="at", metric="air_temp", depth_cm=None, unit="°C"
            )
        )
        session.add(
            SensorRow(node_id=node_id, channel_key="rh", metric="air_rh", depth_cm=None, unit="%")
        )

    await session.commit()
    return ScenarioProvision(phone=phone, plot_id=plot_id, nodes=tuple(nodes))
