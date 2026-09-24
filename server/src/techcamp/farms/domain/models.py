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
    # Paper 56, Chapter 8, Table 19 "Typical soil water characteristics for
    # different soil types" (fao.org/4/x0490e/x0490e0c.htm). docs/03 asks for
    # the class's mean value: the midpoint of each θFC and θWP range (m3/m3),
    # as a percentage. Keys are USDA texture classes.
    "sand": (12.0, 4.5),  # θFC 0.07-0.17, θWP 0.02-0.07
    "loamy_sand": (15.0, 6.5),  # 0.11-0.19, 0.03-0.10
    "sandy_loam": (23.0, 11.0),  # 0.18-0.28, 0.06-0.16
    "loam": (25.0, 12.0),  # 0.20-0.30, 0.07-0.17
    "silt_loam": (29.0, 15.0),  # 0.22-0.36, 0.09-0.21
    "silt": (32.0, 17.0),  # 0.28-0.36, 0.12-0.22
    "silty_clay_loam": (33.5, 20.5),  # 0.30-0.37, 0.17-0.24
    "silty_clay": (36.0, 23.0),  # 0.30-0.42, 0.17-0.29
    "clay": (36.0, 22.0),  # 0.32-0.40, 0.20-0.24
}


def apply_fao56_texture_fallback(
    texture: str | None, field_capacity_pct: float | None, wilting_point_pct: float | None
) -> tuple[float | None, float | None, SoilProfileSource | None]:
    """FAO-56 Table 19 texture fallback (docs/03-modelo-datos.md:445): when a
    soil profile has neither lab nor SoilGrids θFC/θWP, fill the texture
    class's mean values and mark `source = fao56_texture`. Given values pass
    through unchanged as `source = lab` (SoilGrids has its own endpoint,
    `POST /plots/{plot_id}/soil:autofill`, T5, uses `build_soil_profile_from_soilgrids`
    below instead of this function's `lab` branch).
    """
    if field_capacity_pct is not None and wilting_point_pct is not None:
        return field_capacity_pct, wilting_point_pct, SoilProfileSource.LAB
    means = FAO56_TEXTURE_WATER_LIMITS.get(texture.lower()) if texture else None
    if means is None:
        return None, None, None
    return means[0], means[1], SoilProfileSource.FAO56_TEXTURE


@dataclass(frozen=True, slots=True)
class SoilGridsSample:
    """A SoilGrids ISRIC v2.0 `properties/query` response, already converted
    from mapped integer storage units to conventional units (T5 decision:
    the adapter reads each property's own `unit_measure.d_factor` from the
    response rather than hardcoding a divisor, docs.isric.org/rest.isric.org
    verified 2026-09-23). Any field is `None` when SoilGrids has no
    prediction at that point/depth (a normal outcome, not a failure)."""

    ph: float | None
    organic_carbon_pct: float | None
    """Soil organic carbon, percentage by mass (`soc`, g/kg / 10)."""
    sand_pct: float | None
    silt_pct: float | None
    clay_pct: float | None
    field_capacity_pct: float | None
    """θFC at 33 kPa (`wv0033`), already a percentage."""
    wilting_point_pct: float | None
    """θWP at 1500 kPa (`wv1500`), already a percentage."""


ORGANIC_CARBON_TO_MATTER_FACTOR = 1.724
"""The van Bemmelen factor (organic matter ≈ 58% carbon by mass, so
OM% = OC% × 1.724), attributed to van Bemmelen (1890) and still the
conventional soil-science default despite known soil-to-soil error
(Minasny et al., 2020, "Precocious 19th century soil carbon science",
Geoderma Regional 22 — verified 2026-09-23). No better single factor is
documented for this project, so it's applied as-is, not invented."""


def classify_usda_texture(
    sand_pct: float | None, silt_pct: float | None, clay_pct: float | None
) -> str | None:
    """USDA soil texture triangle, reduced to axis-aligned bands (T5 decision,
    odd/tasks/techcamp-v2-e3-farms.md): this task could not fetch the
    triangle's definitive polygon boundary table (the NRCS Soil Texture
    Calculator page and the USDA Soil Survey Manual page both failed to load
    via WebFetch); these bands are a best-effort reproduction of the
    standard 12-class triangle (USDA Soil Survey Manual, 1993, Ch. 3, Fig.
    3-14), not verified against a fetched primary numeric source.
    ponytail: axis-aligned approximation, not a validated point-in-polygon
    triangle; the three classes with no FAO-56 Table 19 fallback row
    (clay_loam, silty_clay_loam's near neighbors excluded — sandy_clay_loam,
    sandy_clay) only affect the descriptive `texture` string, never θFC/θWP,
    since `apply_fao56_texture_fallback` already returns `None` for any
    class it doesn't recognize. Upgrade to a verified vertex table if
    texture-only reporting accuracy matters.
    """
    if sand_pct is None or silt_pct is None or clay_pct is None:
        return None
    sand, silt, clay = sand_pct, silt_pct, clay_pct
    if clay >= 40:
        if silt >= 40:
            return "silty_clay"
        if sand > 45:
            return "sandy_clay"
        return "clay"
    if clay >= 27:
        if sand <= 20:
            return "silty_clay_loam"
        if sand <= 45:
            return "clay_loam"
        return "sandy_clay_loam"
    if silt >= 80 and clay < 12:
        return "silt"
    if silt >= 50 and clay < 27:
        return "silt_loam"
    if sand >= 70 and clay < 15:
        return "sand" if sand >= 90 else "loamy_sand"
    if clay >= 7 and sand <= 52 and silt >= 28:
        return "loam"
    if sand > 52:
        return "sandy_loam"
    return "loam"


def build_soil_profile_from_soilgrids(plot_id: UUID, sample: SoilGridsSample) -> SoilProfile:
    """`POST /plots/{plot_id}/soil:autofill` (docs/04-api.md:50; RF-03).

    θFC/θWP come straight from SoilGrids (`source = soilgrids`) when both
    `wv0033` and `wv1500` are available at the query point; otherwise this
    falls back to the same FAO-56 Table 19 texture means `PUT /plots/{id}/soil`
    uses (`source = fao56_texture`, or `None` if the derived texture isn't
    one of the nine classes in `FAO56_TEXTURE_WATER_LIMITS`). `root_depth_cm`
    is never set here: SoilGrids has no such property, and it isn't a soil
    property at all but a crop/rooting choice (T5 decision).
    """
    texture = classify_usda_texture(sample.sand_pct, sample.silt_pct, sample.clay_pct)
    field_capacity_pct: float | None
    wilting_point_pct: float | None
    if sample.field_capacity_pct is not None and sample.wilting_point_pct is not None:
        field_capacity_pct = sample.field_capacity_pct
        wilting_point_pct = sample.wilting_point_pct
        source: SoilProfileSource | None = SoilProfileSource.SOILGRIDS
    else:
        field_capacity_pct, wilting_point_pct, source = apply_fao56_texture_fallback(
            texture, None, None
        )
    organic_matter_pct = (
        sample.organic_carbon_pct * ORGANIC_CARBON_TO_MATTER_FACTOR
        if sample.organic_carbon_pct is not None
        else None
    )
    return SoilProfile(
        plot_id=plot_id,
        source=source,
        ph=sample.ph,
        organic_matter_pct=organic_matter_pct,
        texture=texture,
        field_capacity_pct=field_capacity_pct,
        wilting_point_pct=wilting_point_pct,
        root_depth_cm=None,
    )
