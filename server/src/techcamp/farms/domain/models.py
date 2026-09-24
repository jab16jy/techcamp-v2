"""Farm and plot domain entities and rules (docs/03-modelo-datos.md:85-135; ADR-0023).

Pure data and pure functions, no I/O. Geometry is kept as WKT text: the domain
does not know about PostGIS or GeoAlchemy2, only that a location or boundary
exists as a well-known-text value; adapters own the spatial conversions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from techcamp.farms.domain.errors import InsufficientRoleError, RainfedPlotHasIrrigationError
from techcamp.identity.domain.models import Role


class IrrigationSystem(StrEnum):
    NONE = "none"
    DRIP = "drip"
    SPRINKLER = "sprinkler"
    GRAVITY = "gravity"


DEFAULT_IRRIGATION_EFFICIENCY: dict[IrrigationSystem, float] = {
    IrrigationSystem.DRIP: 0.90,
    IrrigationSystem.SPRINKLER: 0.75,
    IrrigationSystem.GRAVITY: 0.60,
}
"""Default efficiency per system (docs/03-modelo-datos.md:455); rainfed has none."""


def default_efficiency_for(system: IrrigationSystem) -> float | None:
    """A newly created plot's efficiency defaults to its system's value.

    Rainfed (`none`) plots default to no efficiency (docs/03-modelo-datos.md:455).
    """
    return DEFAULT_IRRIGATION_EFFICIENCY.get(system)


def ensure_rainfed_has_no_irrigation(
    system: IrrigationSystem, efficiency: float | None, flow_lph: float | None
) -> None:
    """Reject an efficiency or flow on a rainfed plot (ADR-0023)."""
    if system is IrrigationSystem.NONE and (efficiency is not None or flow_lph is not None):
        raise RainfedPlotHasIrrigationError(efficiency=efficiency, flow_lph=flow_lph)


WRITE_ROLES: frozenset[Role] = frozenset({Role.OWNER, Role.TECHNICIAN})
"""Membership roles that may create or edit farms and plots.

docs/04-api.md is silent on which roles may write; T2 decision
(odd/tasks/techcamp-v2-e3-farms.md): owner and technician write, producer
and viewer read only.
"""


def ensure_can_write(role: Role) -> None:
    """Reject a write from a role outside `WRITE_ROLES`."""
    if role not in WRITE_ROLES:
        raise InsufficientRoleError(role)


@dataclass(frozen=True, slots=True)
class Farm:
    id: UUID
    org_id: UUID
    name: str
    municipality_code: str
    """DIVIPOLA code as plain text (no `municipality` table exists yet)."""
    location: str
    """WKT text, e.g. `POINT(-74.1 10.9)`, SRID 4326."""
    technician_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Plot:
    id: UUID
    org_id: UUID
    farm_id: UUID
    name: str
    boundary: str
    """WKT text, e.g. `POLYGON((...))`, SRID 4326."""
    area_ha: float
    """Computed by the database from `boundary` (`ST_Area(boundary::geography) / 10000`)."""
    weather_cell_id: int | None
    """No FK yet: `weather_cell` is owned by E5."""
    irrigation_system: IrrigationSystem
    irrigation_efficiency: float | None
    system_flow_lph: float | None
