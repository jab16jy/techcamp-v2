"""FastAPI dependencies wiring farms adapters into requests."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from techcamp.farms.adapters.repositories import (
    SqlAlchemyCropRepository,
    SqlAlchemyFarmRepository,
    SqlAlchemyPlotRepository,
    SqlAlchemySoilProfileRepository,
)
from techcamp.shared.db import SessionDep


async def get_farm_repository(session: SessionDep) -> SqlAlchemyFarmRepository:
    return SqlAlchemyFarmRepository(session)


async def get_plot_repository(session: SessionDep) -> SqlAlchemyPlotRepository:
    return SqlAlchemyPlotRepository(session)


async def get_crop_repository(session: SessionDep) -> SqlAlchemyCropRepository:
    return SqlAlchemyCropRepository(session)


async def get_soil_profile_repository(session: SessionDep) -> SqlAlchemySoilProfileRepository:
    return SqlAlchemySoilProfileRepository(session)


FarmRepoDep = Annotated[SqlAlchemyFarmRepository, Depends(get_farm_repository)]
PlotRepoDep = Annotated[SqlAlchemyPlotRepository, Depends(get_plot_repository)]
CropRepoDep = Annotated[SqlAlchemyCropRepository, Depends(get_crop_repository)]
SoilProfileRepoDep = Annotated[
    SqlAlchemySoilProfileRepository, Depends(get_soil_profile_repository)
]
