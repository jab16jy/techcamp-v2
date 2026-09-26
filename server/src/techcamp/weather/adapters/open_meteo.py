"""Open-Meteo forecast REST adapter (docs/06 §6, ADR-0009, ADR-0021, docs/09:15,51).

Fetches daily weather forecasts and past observations from Open-Meteo.
ADR-0021: Seminar profile calls the free Open-Meteo API; production uses
the commercial customer-api endpoint with an API key.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx

from techcamp.shared.config import is_seminar_profile, open_meteo_api_key
from techcamp.weather.application.ports import (
    DailyWeatherRow,
    WeatherUnavailableError,
)

logger = logging.getLogger(__name__)


OPEN_METEO_DAILY_VARS: tuple[str, ...] = (
    "et0_fao_evapotranspiration",
    "precipitation_sum",
    "temperature_2m_min",
    "temperature_2m_max",
    "relative_humidity_2m_mean",
)

_DEFAULT_FREE_BASE_URL = "https://api.open-meteo.com/v1"
_DEFAULT_COMMERCIAL_BASE_URL = "https://customer-api.open-meteo.com/v1"
_DEFAULT_TIMEOUT_SECONDS = 10.0
_DEFAULT_TIMEZONE = "auto"
_MAX_FORECAST_DAYS = 16


class OpenMeteoUnavailableError(WeatherUnavailableError):
    """Raised when Open-Meteo is unreachable, returns an error status,
    or returns an unparseable response."""


def _extract_float(lst: Any, idx: int) -> float | None:
    if not isinstance(lst, list) or idx >= len(lst):
        return None
    val = lst[idx]
    if val is None:
        return None
    return float(val)


def _parse_daily_response(response: httpx.Response) -> list[DailyWeatherRow]:
    """Parse the daily weather variables from Open-Meteo JSON response.

    Single parsing boundary: every malformed 200 body raises
    OpenMeteoUnavailableError, preserving the original cause.
    Null values within variable arrays are parsed as None without crashing.
    """
    try:
        data = response.json()
        daily = data.get("daily")
        if not isinstance(daily, dict):
            raise ValueError("Missing or invalid 'daily' field in response")

        times = daily.get("time")
        if not isinstance(times, list):
            raise ValueError("Missing or invalid 'time' array in 'daily'")

        et0_list = daily.get("et0_fao_evapotranspiration", [])
        rain_list = daily.get("precipitation_sum", [])
        tmin_list = daily.get("temperature_2m_min", [])
        tmax_list = daily.get("temperature_2m_max", [])
        rh_list = daily.get("relative_humidity_2m_mean", [])

        rows: list[DailyWeatherRow] = []
        for idx, day_str in enumerate(times):
            if not isinstance(day_str, str):
                raise ValueError(f"Invalid date item at index {idx}: {day_str!r}")
            day_val = date.fromisoformat(day_str)

            rows.append(
                DailyWeatherRow(
                    day=day_val,
                    et0_mm=_extract_float(et0_list, idx),
                    rain_mm=_extract_float(rain_list, idx),
                    tmin_c=_extract_float(tmin_list, idx),
                    tmax_c=_extract_float(tmax_list, idx),
                    rh_mean_pct=_extract_float(rh_list, idx),
                )
            )
        return rows
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        logger.warning("malformed Open-Meteo response body: %s", exc)
        raise OpenMeteoUnavailableError(
            detail=f"malformed Open-Meteo response: {exc}",
            upstream_status=response.status_code,
        ) from exc


class OpenMeteoAdapter:
    """Real Open-Meteo forecast API adapter implementing WeatherForecastPort.

    Pass transport to intercept HTTP calls with httpx.MockTransport in tests;
    omit for real network calls.
    """

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = _DEFAULT_FREE_BASE_URL,
        api_key: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        timezone: str = _DEFAULT_TIMEZONE,
    ) -> None:
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._timezone = timezone

    async def fetch_daily(
        self,
        lat: float,
        lon: float,
        *,
        past_days: int = 0,
        forecast_days: int = 7,
    ) -> list[DailyWeatherRow]:
        if forecast_days < 0:
            raise ValueError(f"forecast_days must be non-negative: {forecast_days}")
        if past_days < 0:
            raise ValueError(f"past_days must be non-negative: {past_days}")

        clamped_forecast_days = min(forecast_days, _MAX_FORECAST_DAYS)

        params: dict[str, Any] = {
            "latitude": lat,
            "longitude": lon,
            "daily": ",".join(OPEN_METEO_DAILY_VARS),
            "timezone": self._timezone,
            "forecast_days": clamped_forecast_days,
            "past_days": past_days,
        }
        if self._api_key:
            params["apikey"] = self._api_key

        async with httpx.AsyncClient(
            transport=self._transport,
            base_url=self._base_url,
            timeout=self._timeout,
        ) as client:
            try:
                response = await client.get("/forecast", params=params)
            except httpx.RequestError as exc:
                raise OpenMeteoUnavailableError(
                    detail=f"Open-Meteo connection error: {exc}",
                    upstream_status=None,
                ) from exc

        if response.status_code != 200:
            raise OpenMeteoUnavailableError(
                detail=f"Open-Meteo returned status {response.status_code}",
                upstream_status=response.status_code,
            )

        return _parse_daily_response(response)

    fetch_forecast = fetch_daily


def get_weather_forecast_adapter() -> OpenMeteoAdapter:
    """ADR-0021: seminar profile calls the free Open-Meteo API; production
    uses the commercial endpoint with an API key."""
    if is_seminar_profile():
        return OpenMeteoAdapter(base_url=_DEFAULT_FREE_BASE_URL)
    return OpenMeteoAdapter(
        base_url=_DEFAULT_COMMERCIAL_BASE_URL,
        api_key=open_meteo_api_key(),
    )
