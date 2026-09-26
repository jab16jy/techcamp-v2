"""FastAPI dependencies wiring telemetry adapters into requests."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from techcamp.shared.db import SessionDep
from techcamp.telemetry.adapters.repositories import (
    SqlAlchemyCalibrationRepository,
    SqlAlchemyNodeRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemySensorRepository,
)


async def get_node_repository(session: SessionDep) -> SqlAlchemyNodeRepository:
    return SqlAlchemyNodeRepository(session)


async def get_sensor_repository(session: SessionDep) -> SqlAlchemySensorRepository:
    return SqlAlchemySensorRepository(session)


async def get_calibration_repository(session: SessionDep) -> SqlAlchemyCalibrationRepository:
    return SqlAlchemyCalibrationRepository(session)


async def get_reading_repository(session: SessionDep) -> SqlAlchemyReadingRepository:
    return SqlAlchemyReadingRepository(session)


NodeRepoDep = Annotated[SqlAlchemyNodeRepository, Depends(get_node_repository)]
SensorRepoDep = Annotated[SqlAlchemySensorRepository, Depends(get_sensor_repository)]
CalibrationRepoDep = Annotated[SqlAlchemyCalibrationRepository, Depends(get_calibration_repository)]
ReadingRepoDep = Annotated[SqlAlchemyReadingRepository, Depends(get_reading_repository)]
