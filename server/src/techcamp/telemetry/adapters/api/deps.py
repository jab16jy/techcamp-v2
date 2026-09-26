"""FastAPI dependencies wiring telemetry adapters into requests."""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import Depends, Request

from techcamp.shared.db import SessionDep
from techcamp.telemetry.adapters.repositories import (
    SqlAlchemyCalibrationRepository,
    SqlAlchemyNodeRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemySensorRepository,
)
from techcamp.telemetry.adapters.sse_hub import PlotEventsHub


async def get_node_repository(session: SessionDep) -> SqlAlchemyNodeRepository:
    return SqlAlchemyNodeRepository(session)


async def get_sensor_repository(session: SessionDep) -> SqlAlchemySensorRepository:
    return SqlAlchemySensorRepository(session)


async def get_calibration_repository(session: SessionDep) -> SqlAlchemyCalibrationRepository:
    return SqlAlchemyCalibrationRepository(session)


async def get_reading_repository(session: SessionDep) -> SqlAlchemyReadingRepository:
    return SqlAlchemyReadingRepository(session)


def get_plot_events_hub(request: Request) -> PlotEventsHub:
    """The one hub opened for the process lifetime in `main.py`'s lifespan.

    `Request.app.state` is a dynamic attribute bag (typed `Any`); the cast
    is safe because `main.py`'s lifespan is the only thing that sets it.
    """
    return cast(PlotEventsHub, request.app.state.plot_events_hub)


NodeRepoDep = Annotated[SqlAlchemyNodeRepository, Depends(get_node_repository)]
SensorRepoDep = Annotated[SqlAlchemySensorRepository, Depends(get_sensor_repository)]
CalibrationRepoDep = Annotated[SqlAlchemyCalibrationRepository, Depends(get_calibration_repository)]
ReadingRepoDep = Annotated[SqlAlchemyReadingRepository, Depends(get_reading_repository)]
PlotEventsHubDep = Annotated[PlotEventsHub, Depends(get_plot_events_hub)]
