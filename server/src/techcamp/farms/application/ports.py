"""Repository ports the application layer depends on (never on adapters).

Required by ADR-0002's layering (`application` may only import `domain`).
Unlike T1's read-only repositories (farms/adapters/repositories.py), write
access needs an abstraction here for the layering rule itself, not for a
test double: writes go through the same real-Postgres fixtures as reads
(server/tests/conftest.py), per the testing policy in AGENTS.md.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol
from uuid import UUID

from techcamp.farms.domain.models import (
    Crop,
    CropCycle,
    CropCycleStatus,
    Farm,
    IrrigationSystem,
    Plot,
    SoilGridsSample,
    SoilProfile,
)


class FarmRepository(Protocol):
    async def get_for_orgs(self, farm_id: UUID, org_ids: Sequence[UUID]) -> Farm | None: ...

    async def list_for_org(
        self, org_id: UUID, *, limit: int = 50, cursor: UUID | None = None
    ) -> list[Farm]: ...

    async def create(
        self,
        *,
        farm_id: UUID,
        org_id: UUID,
        name: str,
        municipality_code: str,
        location_wkt: str,
        technician_id: UUID | None,
    ) -> Farm: ...

    async def update(
        self, farm_id: UUID, org_id: UUID, *, name: str, technician_id: UUID | None
    ) -> Farm: ...


class PlotRepository(Protocol):
    async def get_by_id(self, plot_id: UUID) -> Plot | None:
        """Get plot by id regardless of org (used by background jobs such as irrigation)."""
        ...

    async def get_for_orgs(self, plot_id: UUID, org_ids: Sequence[UUID]) -> Plot | None: ...

    async def list_for_farm(self, farm_id: UUID, org_id: UUID) -> list[Plot]: ...

    async def create(
        self,
        *,
        plot_id: UUID,
        org_id: UUID,
        farm_id: UUID,
        name: str,
        boundary_wkt: str,
        irrigation_system: IrrigationSystem,
        irrigation_efficiency: float | None,
        system_flow_lph: float | None,
    ) -> Plot: ...

    async def update(
        self,
        plot_id: UUID,
        org_id: UUID,
        *,
        name: str,
        boundary_wkt: str,
        irrigation_system: IrrigationSystem,
        irrigation_efficiency: float | None,
        system_flow_lph: float | None,
    ) -> Plot: ...

    async def get_centroid(self, plot_id: UUID, org_id: UUID) -> tuple[float, float]:
        """(lon, lat) of the plot's PostGIS centroid — the SoilGrids query
        point (T5 task instruction: `ST_Centroid` on the org-scoped plot)."""
        ...


class SoilProfileRepository(Protocol):
    async def get_for_plot(self, plot_id: UUID) -> SoilProfile | None:
        """Get the soil profile for a plot, or None if none has been configured."""
        ...

    async def put(self, profile: SoilProfile) -> SoilProfile: ...


class SoilGridsPort(Protocol):
    """External I/O port (ADR-0002: a port for external I/O needing a test
    double). Two real implementations of the single adapter class
    (`IsricSoilGridsAdapter`): a live network transport for production, an
    injected `httpx.MockTransport` for the seminar profile's recorded
    fixture and for this module's own tests (ADR-0021)."""

    async def fetch_sample(self, lon: float, lat: float) -> SoilGridsSample: ...


class CropRepository(Protocol):
    """T6: `create_cycle` needs one crop's stages (to derive
    `expected_harvest_on`) and to validate `crop_id`, neither served by the
    read-all-rows `list_all` the T3 catalog endpoint uses."""

    async def get(self, crop_id: int) -> Crop | None: ...


class CropCycleRepository(Protocol):
    async def create(
        self,
        *,
        cycle_id: UUID,
        plot_id: UUID,
        crop_id: int,
        sown_on: date,
        expected_harvest_on: date | None,
        status: CropCycleStatus,
    ) -> CropCycle: ...

    async def get_active_for_plot(self, plot_id: UUID) -> CropCycle | None:
        """The plot's active cycle, if any (T6: the application-layer
        precheck backing the DB partial unique index — see
        `manage_cycles.create_cycle`)."""
        ...

    async def get_for_orgs(self, cycle_id: UUID, org_ids: Sequence[UUID]) -> CropCycle | None:
        """Look up a cycle across every org the caller belongs to, joined
        through its plot (`crop_cycle` has no `org_id` column, docs/03)."""
        ...

    async def update(
        self, cycle_id: UUID, *, status: CropCycleStatus, expected_harvest_on: date | None
    ) -> CropCycle: ...
