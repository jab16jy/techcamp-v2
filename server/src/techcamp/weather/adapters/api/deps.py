"""FastAPI dependencies wiring weather adapters into requests."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from techcamp.weather.adapters.open_meteo import OpenMeteoAdapter, get_weather_forecast_adapter


def get_weather_forecast_port() -> OpenMeteoAdapter:
    """ADR-0021: seminar profile calls free Open-Meteo API; production uses
    the commercial customer endpoint with API key."""
    return get_weather_forecast_adapter()


WeatherForecastPortDep = Annotated[OpenMeteoAdapter, Depends(get_weather_forecast_port)]
