"""FastAPI dependencies wiring weather adapters into requests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends

from techcamp.shared.db import SessionDep
from techcamp.weather.adapters.repositories import SqlAlchemyWeatherRepository


async def get_weather_repository(session: SessionDep) -> SqlAlchemyWeatherRepository:
    return SqlAlchemyWeatherRepository(session)


def get_now() -> datetime:
    """The current UTC clock dependency, overridable in tests."""
    return datetime.now(UTC)


WeatherRepoDep = Annotated[SqlAlchemyWeatherRepository, Depends(get_weather_repository)]
NowDep = Annotated[datetime, Depends(get_now)]
