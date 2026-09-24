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


CROP_STAGES: tuple[str, ...] = ("initial", "development", "mid", "late")
"""FAO-56 growth-stage order (Table 11); `crop_stage` rows are always
returned in this order regardless of storage order."""


class KcSource(StrEnum):
    FAO56 = "fao56"
    LOCAL = "local"
    APPROXIMATE = "approximate"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class CropStage:
    stage: str
    """One of `CROP_STAGES`."""
    length_days: int
    kc: float
    depletion_fraction_p: float
    """FAO-56 Table 22 `p` (no-stress depletion fraction); the same value for
    every stage of a crop, since FAO-56 doesn't vary `p` by growth stage."""


@dataclass(frozen=True, slots=True)
class Crop:
    id: int
    code: str
    name_es: str
    kc_source: KcSource
    stages: tuple[CropStage, ...]
    """Empty when `kc_source` is `none` (docs/03-modelo-datos.md:446): no
    validated Kc blocks the irrigation depth recommendation."""


class SoilProfileSource(StrEnum):
    SOILGRIDS = "soilgrids"
    LAB = "lab"
    FAO56_TEXTURE = "fao56_texture"


@dataclass(frozen=True, slots=True)
class SoilProfile:
    plot_id: UUID
    source: SoilProfileSource | None
    """`None` when neither lab/SoilGrids values nor a recognized FAO-56
    texture class were available to fill θFC/θWP (T4 decision: docs/03
    doesn't mark `source` `NOT NULL`, and inventing a source for missing
    data would misrepresent it)."""
    ph: float | None
    organic_matter_pct: float | None
    texture: str | None
    field_capacity_pct: float | None
    """θFC, as a percentage (docs/03-modelo-datos.md:106)."""
    wilting_point_pct: float | None
    """θWP, as a percentage (docs/03-modelo-datos.md:107)."""
    root_depth_cm: float | None


FAO56_TEXTURE_WATER_LIMITS: dict[str, tuple[float, float]] = {
    # FAO-56 (Allen, Pereira, Raes & Smith, 1998), Irrigation and Drainage
    # Paper 56, fao.org, Chapter 8 "ETc under soil water stress conditions",
    # Table 19 "Soil water characteristics for different soil types":
    # field capacity (θFC) and wilting point (θWP) as volumetric water
    # content (m3/m3), reported here as a percentage.
    #
    # docs gap (flagged in odd/tasks/techcamp-v2-e3-farms.md T4): the FAO
    # HTML mirror (fao.org/4/x0490e/x0490e0e.htm) does not reproduce Table
    # 19 itself, only Example 36's three worked values, each explicitly
    # marked "From Table 19" in that same chapter. Those are the only three
    # classes verified against the primary source; the other FAO-56 Table
    # 19 classes (sand, sandy loam, loam, silt loam, silt clay loam, clay)
    # are deliberately omitted rather than invented — add them only once
    # independently verified against Table 19 directly.
    "loamy_sand": (15.0, 6.0),
    "silt": (32.0, 15.0),
    "silty_clay": (35.0, 23.0),
}


def apply_fao56_texture_fallback(
    texture: str | None, field_capacity_pct: float | None, wilting_point_pct: float | None
) -> tuple[float | None, float | None, SoilProfileSource | None]:
    """FAO-56 Table 19 texture fallback (docs/03-modelo-datos.md:445): when a
    soil profile has neither lab nor SoilGrids θFC/θWP, fill the texture
    class's mean values and mark `source = fao56_texture`. Given values pass
    through unchanged as `source = lab` (SoilGrids has its own endpoint,
    `POST /plots/{plot_id}/soil:autofill`, T5, out of scope here).
    """
    if field_capacity_pct is not None and wilting_point_pct is not None:
        return field_capacity_pct, wilting_point_pct, SoilProfileSource.LAB
    means = FAO56_TEXTURE_WATER_LIMITS.get(texture.lower()) if texture else None
    if means is None:
        return None, None, None
    return means[0], means[1], SoilProfileSource.FAO56_TEXTURE
