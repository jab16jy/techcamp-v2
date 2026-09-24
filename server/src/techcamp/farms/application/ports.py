"""Repository ports the application layer depends on (never on adapters).

Required by ADR-0002's layering (`application` may only import `domain`).
Unlike T1's read-only repositories (farms/adapters/repositories.py), write
access needs an abstraction here for the layering rule itself, not for a
test double: writes go through the same real-Postgres fixtures as reads
(server/tests/conftest.py), per the testing policy in AGENTS.md.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from techcamp.farms.domain.models import Farm, IrrigationSystem, Plot, SoilProfile


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


class SoilProfileRepository(Protocol):
    async def put(self, profile: SoilProfile) -> SoilProfile: ...
