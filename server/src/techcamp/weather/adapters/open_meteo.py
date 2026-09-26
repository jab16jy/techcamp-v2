"""Open-Meteo forecast REST adapter (docs/06 §6, ADR-0009, ADR-0021, docs/09:15,51).

Fetches daily weather forecasts and past observations from Open-Meteo.
ADR-0021: Seminar profile calls the free Open-Meteo API; production uses
the commercial customer-api endpoint with an API key.

Includes 10 s timeout, 3 retries with exponential backoff and jitter on
transport errors, 5xx responses, and 429 rate limits, and an in-adapter
circuit breaker.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import random
import time
from collections.abc import Awaitable, Callable
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
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_RETRY_BASE_DELAY = 0.5
_DEFAULT_CIRCUIT_FAILURE_THRESHOLD = 5
_DEFAULT_CIRCUIT_COOLDOWN_SECONDS = 60.0


class CircuitState(enum.StrEnum):
    """Lifecycle states of the in-adapter circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class OpenMeteoUnavailableError(WeatherUnavailableError):
    """Raised when Open-Meteo is unreachable, returns an error status,
    or returns an unparseable response."""


class OpenMeteoCircuitBreakerOpenError(OpenMeteoUnavailableError):
    """Raised when the Open-Meteo circuit breaker is OPEN, failing fast."""


class CircuitBreaker:
    """In-adapter circuit breaker.

    - CLOSED: normal operation. Transitions to OPEN after N consecutive failures.
    - OPEN: fails fast without making network calls. Transitions to HALF_OPEN
      after cooldown_seconds.
    - HALF_OPEN: allows one trial call. Transitions to CLOSED on success, or
      back to OPEN on failure.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = _DEFAULT_CIRCUIT_FAILURE_THRESHOLD,
        cooldown_seconds: float = _DEFAULT_CIRCUIT_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.last_failure_time: float = 0.0

    def allow_request(self) -> bool:
        now = self._clock()
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if now - self.last_failure_time >= self.cooldown_seconds:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        # HALF_OPEN allows a single trial request
        return True

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        self.last_failure_time = self._clock()
        if (
            self.state == CircuitState.HALF_OPEN
            or self.consecutive_failures >= self.failure_threshold
        ):
            self.state = CircuitState.OPEN


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

    Provides retry with exponential backoff and jitter on transport errors,
    5xx responses, and 429 status codes, backed by an in-adapter circuit breaker.
    """

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url: str = _DEFAULT_FREE_BASE_URL,
        api_key: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        timezone: str = _DEFAULT_TIMEZONE,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        retry_base_delay: float = _DEFAULT_RETRY_BASE_DELAY,
        circuit_failure_threshold: int = _DEFAULT_CIRCUIT_FAILURE_THRESHOLD,
        circuit_cooldown_seconds: float = _DEFAULT_CIRCUIT_COOLDOWN_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._timezone = timezone
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._sleep = sleep
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=circuit_failure_threshold,
            cooldown_seconds=circuit_cooldown_seconds,
            clock=clock,
        )

    @property
    def circuit_state(self) -> CircuitState:
        return self._circuit_breaker.state

    @property
    def is_circuit_open(self) -> bool:
        return self._circuit_breaker.state == CircuitState.OPEN

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

        if not self._circuit_breaker.allow_request():
            raise OpenMeteoCircuitBreakerOpenError(
                detail="Open-Meteo circuit breaker is OPEN",
                upstream_status=None,
            )

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

        attempts = 0
        while True:
            try:
                async with httpx.AsyncClient(
                    transport=self._transport,
                    base_url=self._base_url,
                    timeout=self._timeout,
                ) as client:
                    response = await client.get("/forecast", params=params)

                if response.status_code == 429 or response.status_code >= 500:
                    if attempts < self._max_retries:
                        attempts += 1
                        backoff = self._retry_base_delay * (2 ** (attempts - 1))
                        jitter = random.uniform(0, 0.1 * backoff)
                        await self._sleep(backoff + jitter)
                        continue
                    self._circuit_breaker.record_failure()
                    raise OpenMeteoUnavailableError(
                        detail=f"Open-Meteo returned status {response.status_code}",
                        upstream_status=response.status_code,
                    )

                if response.status_code != 200:
                    # 4xx client errors other than 429: do not retry
                    self._circuit_breaker.record_failure()
                    raise OpenMeteoUnavailableError(
                        detail=f"Open-Meteo returned status {response.status_code}",
                        upstream_status=response.status_code,
                    )

                result = _parse_daily_response(response)
                self._circuit_breaker.record_success()
                return result

            except httpx.RequestError as exc:
                if attempts < self._max_retries:
                    attempts += 1
                    backoff = self._retry_base_delay * (2 ** (attempts - 1))
                    jitter = random.uniform(0, 0.1 * backoff)
                    await self._sleep(backoff + jitter)
                    continue
                self._circuit_breaker.record_failure()
                raise OpenMeteoUnavailableError(
                    detail=f"Open-Meteo connection error: {exc}",
                    upstream_status=None,
                ) from exc
            except OpenMeteoUnavailableError:
                self._circuit_breaker.record_failure()
                raise

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
