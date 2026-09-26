"""Open-Meteo weather forecast adapter tests (T3a).

An injected `httpx.MockTransport` stands in for the network (ADR-0021).
Tests verify parameter formation, response parsing with null values,
malformed body handling, error wrapping, and profile-based DI wiring.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest

from techcamp.weather.adapters.api.deps import get_weather_forecast_port
from techcamp.weather.adapters.open_meteo import (
    OPEN_METEO_DAILY_VARS,
    OpenMeteoAdapter,
    OpenMeteoUnavailableError,
)
from techcamp.weather.application.ports import DailyWeatherRow, WeatherForecastPort

pytestmark = pytest.mark.anyio


_FIXTURE_RESPONSE: dict[str, Any] = {
    "latitude": 10.9,
    "longitude": -74.1,
    "generationtime_ms": 0.5,
    "utc_offset_seconds": -18000,
    "timezone": "America/Bogota",
    "timezone_abbreviation": "-05",
    "daily": {
        "time": ["2026-09-25", "2026-09-26", "2026-09-27"],
        "et0_fao_evapotranspiration": [4.5, 4.8, 5.0],
        "precipitation_sum": [0.0, 12.5, 3.2],
        "temperature_2m_min": [24.1, 23.5, 24.0],
        "temperature_2m_max": [33.2, 31.0, 32.5],
        "relative_humidity_2m_mean": [75.0, 82.0, 78.5],
    },
    "daily_units": {
        "time": "iso8601",
        "et0_fao_evapotranspiration": "mm",
        "precipitation_sum": "mm",
        "temperature_2m_min": "°C",
        "temperature_2m_max": "°C",
        "relative_humidity_2m_mean": "%",
    },
}


async def test_fetch_daily_sends_correct_parameters() -> None:
    captured_request: httpx.Request | None = None

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(200, json=_FIXTURE_RESPONSE)

    adapter = OpenMeteoAdapter(
        transport=httpx.MockTransport(_handler),
        base_url="https://api.open-meteo.com/v1",
    )

    rows = await adapter.fetch_daily(lat=10.9, lon=-74.1, past_days=1, forecast_days=7)

    assert len(rows) == 3
    assert captured_request is not None
    assert captured_request.url.path == "/v1/forecast"
    params = captured_request.url.params
    assert params["latitude"] == "10.9"
    assert params["longitude"] == "-74.1"
    assert params["timezone"] == "auto"
    assert params["past_days"] == "1"
    assert params["forecast_days"] == "7"
    for var_name in OPEN_METEO_DAILY_VARS:
        assert var_name in params["daily"]


async def test_fetch_daily_parses_response_into_daily_weather_rows() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_FIXTURE_RESPONSE)

    adapter = OpenMeteoAdapter(transport=httpx.MockTransport(_handler))

    rows = await adapter.fetch_daily(lat=10.9, lon=-74.1)

    assert rows == [
        DailyWeatherRow(
            day=date(2026, 9, 25),
            et0_mm=pytest.approx(4.5),
            rain_mm=pytest.approx(0.0),
            tmin_c=pytest.approx(24.1),
            tmax_c=pytest.approx(33.2),
            rh_mean_pct=pytest.approx(75.0),
        ),
        DailyWeatherRow(
            day=date(2026, 9, 26),
            et0_mm=pytest.approx(4.8),
            rain_mm=pytest.approx(12.5),
            tmin_c=pytest.approx(23.5),
            tmax_c=pytest.approx(31.0),
            rh_mean_pct=pytest.approx(82.0),
        ),
        DailyWeatherRow(
            day=date(2026, 9, 27),
            et0_mm=pytest.approx(5.0),
            rain_mm=pytest.approx(3.2),
            tmin_c=pytest.approx(24.0),
            tmax_c=pytest.approx(32.5),
            rh_mean_pct=pytest.approx(78.5),
        ),
    ]


async def test_fetch_daily_handles_null_values_without_crashing() -> None:
    fixture_with_nulls = {
        "daily": {
            "time": ["2026-09-25", "2026-09-26"],
            "et0_fao_evapotranspiration": [None, 4.8],
            "precipitation_sum": [0.0, None],
            "temperature_2m_min": [None, None],
            "temperature_2m_max": [33.2, None],
            "relative_humidity_2m_mean": [None, 80.0],
        }
    }

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fixture_with_nulls)

    adapter = OpenMeteoAdapter(transport=httpx.MockTransport(_handler))

    rows = await adapter.fetch_daily(lat=10.9, lon=-74.1)

    assert rows == [
        DailyWeatherRow(
            day=date(2026, 9, 25),
            et0_mm=None,
            rain_mm=pytest.approx(0.0),
            tmin_c=None,
            tmax_c=pytest.approx(33.2),
            rh_mean_pct=None,
        ),
        DailyWeatherRow(
            day=date(2026, 9, 26),
            et0_mm=pytest.approx(4.8),
            rain_mm=None,
            tmin_c=None,
            tmax_c=None,
            rh_mean_pct=pytest.approx(80.0),
        ),
    ]


async def test_fetch_daily_clamps_forecast_days_at_max_16() -> None:
    captured_request: httpx.Request | None = None

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(200, json=_FIXTURE_RESPONSE)

    adapter = OpenMeteoAdapter(transport=httpx.MockTransport(_handler))

    await adapter.fetch_daily(lat=10.9, lon=-74.1, forecast_days=25)

    assert captured_request is not None
    assert captured_request.url.params["forecast_days"] == "16"


async def test_fetch_daily_rejects_negative_days() -> None:
    adapter = OpenMeteoAdapter()

    with pytest.raises(ValueError, match="forecast_days"):
        await adapter.fetch_daily(lat=10.9, lon=-74.1, forecast_days=-1)

    with pytest.raises(ValueError, match="past_days"):
        await adapter.fetch_daily(lat=10.9, lon=-74.1, past_days=-1)


async def test_fetch_daily_production_uses_commercial_endpoint_and_apikey() -> None:
    captured_request: httpx.Request | None = None

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(200, json=_FIXTURE_RESPONSE)

    adapter = OpenMeteoAdapter(
        transport=httpx.MockTransport(_handler),
        base_url="https://customer-api.open-meteo.com/v1",
        api_key="secret-api-key",
    )

    await adapter.fetch_daily(lat=10.9, lon=-74.1)

    assert captured_request is not None
    assert captured_request.url.host == "customer-api.open-meteo.com"
    assert captured_request.url.params["apikey"] == "secret-api-key"


async def test_fetch_daily_raises_with_upstream_status_on_non_200() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    adapter = OpenMeteoAdapter(transport=httpx.MockTransport(_handler))

    with pytest.raises(OpenMeteoUnavailableError) as exc_info:
        await adapter.fetch_daily(lat=10.9, lon=-74.1)

    assert exc_info.value.upstream_status == 503


async def test_fetch_daily_raises_with_no_upstream_status_on_connection_failure() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    adapter = OpenMeteoAdapter(transport=httpx.MockTransport(_handler))

    with pytest.raises(OpenMeteoUnavailableError) as exc_info:
        await adapter.fetch_daily(lat=10.9, lon=-74.1)

    assert exc_info.value.upstream_status is None


_MALFORMED_BODIES: dict[str, object] = {
    "missing daily": {"timezone": "America/Bogota"},
    "null daily": {"daily": None},
    "daily is list not dict": {"daily": [1, 2, 3]},
    "missing time in daily": {"daily": {"et0_fao_evapotranspiration": [1.0]}},
    "null time in daily": {"daily": {"time": None}},
    "time entry is not a date": {"daily": {"time": ["not-a-date"]}},
    "non-numeric et0": {
        "daily": {
            "time": ["2026-09-26"],
            "et0_fao_evapotranspiration": ["invalid-float"],
        }
    },
}


@pytest.mark.parametrize("body", _MALFORMED_BODIES.values(), ids=list(_MALFORMED_BODIES))
async def test_fetch_daily_raises_on_malformed_response_bodies(body: object) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    adapter = OpenMeteoAdapter(transport=httpx.MockTransport(_handler))

    with pytest.raises(OpenMeteoUnavailableError) as exc_info:
        await adapter.fetch_daily(lat=10.9, lon=-74.1)

    assert exc_info.value.upstream_status == 200
    assert exc_info.value.__cause__ is not None


def test_open_meteo_adapter_satisfies_weather_forecast_port_protocol() -> None:
    adapter = OpenMeteoAdapter()
    assert isinstance(adapter, WeatherForecastPort)


def test_di_profile_wiring_seminar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TECHCAMP_PROFILE", "seminar")
    adapter = get_weather_forecast_port()
    assert isinstance(adapter, OpenMeteoAdapter)
    assert adapter._base_url == "https://api.open-meteo.com/v1"
    assert adapter._api_key is None


def test_di_profile_wiring_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TECHCAMP_PROFILE", "production")
    monkeypatch.setenv("OPEN_METEO_API_KEY", "prod-key-123")
    adapter = get_weather_forecast_port()
    assert isinstance(adapter, OpenMeteoAdapter)
    assert adapter._base_url == "https://customer-api.open-meteo.com/v1"
    assert adapter._api_key == "prod-key-123"
