"""Postgres repositories for farms (docs/09-cuellos-de-botella.md#seguridad).

Every query filters by `org_id`, so a farm or plot from another organization
is simply not found (docs/04-api.md: cross-org access responds 404, never
403). No application-layer port exists yet: T1 has no use case that depends
on repository behavior through an abstraction (ponytail: a port only for
external I/O or two real implementations, docs/adr per `farms` T1 task).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Row, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.farms.domain.models import Farm, IrrigationSystem, Plot


def _farm_from_row(row: Row[Any]) -> Farm:
    return Farm(
        id=row.id,
        org_id=row.org_id,
        name=row.name,
        municipality_code=row.municipality_code,
        location=row.location,
        technician_id=row.technician_id,
    )


def _plot_from_row(row: Row[Any]) -> Plot:
    return Plot(
        id=row.id,
        org_id=row.org_id,
        farm_id=row.farm_id,
        name=row.name,
        boundary=row.boundary,
        area_ha=float(row.area_ha),
        weather_cell_id=row.weather_cell_id,
        irrigation_system=IrrigationSystem(row.irrigation_system),
        irrigation_efficiency=(
            float(row.irrigation_efficiency) if row.irrigation_efficiency is not None else None
        ),
        system_flow_lph=(float(row.system_flow_lph) if row.system_flow_lph is not None else None),
    )


_FARM_COLUMNS = (
    FarmRow.id,
    FarmRow.org_id,
    FarmRow.name,
    FarmRow.municipality_code,
    func.ST_AsText(FarmRow.location).label("location"),
    FarmRow.technician_id,
)

_PLOT_COLUMNS = (
    PlotRow.id,
    PlotRow.org_id,
    PlotRow.farm_id,
    PlotRow.name,
    func.ST_AsText(PlotRow.boundary).label("boundary"),
    PlotRow.area_ha,
    PlotRow.weather_cell_id,
    PlotRow.irrigation_system,
    PlotRow.irrigation_efficiency,
    PlotRow.system_flow_lph,
)


class SqlAlchemyFarmRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, farm_id: UUID, org_id: UUID) -> Farm | None:
        result = await self._session.execute(
            select(*_FARM_COLUMNS).where(FarmRow.id == farm_id, FarmRow.org_id == org_id)
        )
        row = result.one_or_none()
        return _farm_from_row(row) if row is not None else None

    async def list_for_org(self, org_id: UUID) -> list[Farm]:
        result = await self._session.execute(select(*_FARM_COLUMNS).where(FarmRow.org_id == org_id))
        return [_farm_from_row(row) for row in result]


class SqlAlchemyPlotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, plot_id: UUID, org_id: UUID) -> Plot | None:
        result = await self._session.execute(
            select(*_PLOT_COLUMNS).where(PlotRow.id == plot_id, PlotRow.org_id == org_id)
        )
        row = result.one_or_none()
        return _plot_from_row(row) if row is not None else None

    async def list_for_farm(self, farm_id: UUID, org_id: UUID) -> list[Plot]:
        result = await self._session.execute(
            select(*_PLOT_COLUMNS).where(PlotRow.farm_id == farm_id, PlotRow.org_id == org_id)
        )
        return [_plot_from_row(row) for row in result]
