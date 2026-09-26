"""FastAPI dependencies wiring weather adapters into requests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends

from techcamp.shared.db import SessionDep
from techcamp.weather.adapters.open_meteo import OpenMeteoAdapter, get_weather_forecast_adapter
from techcamp.weather.adapters.repositories import SqlAlchemyWeatherRepository


def get_weather_forecast_port() -> OpenMeteoAdapter:
    """ADR-0021: seminar profile calls free Open-Meteo API; production uses
    the commercial customer endpoint with API key."""
    return get_weather_forecast_adapter()


async def get_weather_repository(session: SessionDep) -> SqlAlchemyWeatherRepository:
    return SqlAlchemyWeatherRepository(session)


def get_now() -> datetime:
    """The current UTC clock dependency, overridable in tests."""
    return datetime.now(UTC)


WeatherForecastPortDep = Annotated[OpenMeteoAdapter, Depends(get_weather_forecast_port)]
WeatherRepoDep = Annotated[SqlAlchemyWeatherRepository, Depends(get_weather_repository)]
NowDep = Annotated[datetime, Depends(get_now)]
