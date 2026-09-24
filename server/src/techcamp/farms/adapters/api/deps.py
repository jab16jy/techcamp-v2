"""FastAPI dependencies wiring farms adapters into requests."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from techcamp.farms.adapters.repositories import SqlAlchemyFarmRepository, SqlAlchemyPlotRepository
from techcamp.shared.db import SessionDep


async def get_farm_repository(session: SessionDep) -> SqlAlchemyFarmRepository:
    return SqlAlchemyFarmRepository(session)


async def get_plot_repository(session: SessionDep) -> SqlAlchemyPlotRepository:
    return SqlAlchemyPlotRepository(session)


FarmRepoDep = Annotated[SqlAlchemyFarmRepository, Depends(get_farm_repository)]
PlotRepoDep = Annotated[SqlAlchemyPlotRepository, Depends(get_plot_repository)]
