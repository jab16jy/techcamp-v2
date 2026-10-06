"""Open-Meteo archive adapter tests (T6a).

An injected `httpx.MockTransport` stands in for the network (ADR-0021), the
same way `weather`'s own adapter tests do. What is verified here is the three
things the M2 features depend on and cannot recover from later: the source is
pinned (`models=era5`, `timezone=America/Bogota`), a missing value stays
missing, and the resilience shape of docs/06 §6 (10 s timeout, 3 retries with
backoff and jitter, circuit breaker) holds.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest

from techcamp.risk.adapters.open_meteo_archive import (
    OPEN_METEO_ARCHIVE_DAILY_VARS,
    OpenMeteoArchiveAdapter,
    get_risk_archive_adapter,
    seminar_archive_adapter,
)
from techcamp.risk.domain.errors import (
    ArchiveCircuitBreakerOpenError,
    ArchiveUnavailableError,
)

pytestmark = pytest.mark.anyio

_START = date(2026, 9, 1)
_END = date(2026, 9, 7)

_ARCHIVE_RESPONSE: dict[str, Any] = {
    "latitude": 11.0,
    "longitude": -74.0,
    "timezone": "America/Bogota",
    "daily_units": {
        "time": "iso8601",
        "precipitation_sum": "mm",
        "soil_moisture_0_to_7cm_mean": "m³/m³",
    },
    "daily": {
        # The first day is the extra one this adapter asks for, and ERA5 leaves
        # it `null` on the window's edge.
        "time": [
            "2026-08-31",
            "2026-09-01",
            "2026-09-02",
            "2026-09-03",
            "2026-09-04",
            "2026-09-05",
            "2026-09-06",
            "2026-09-07",
        ],
        "precipitation_sum": [None, 22.9, 53.9, 25.3, 22.4, 15.3, 23.5, 11.0],
        "soil_moisture_0_to_7cm_mean": [None, 0.499, 0.493, 0.5, 0.492, 0.488, 0.492, 0.488],
    },
}


def _adapter(handler: Any, **kwargs: Any) -> OpenMeteoArchiveAdapter:
    return OpenMeteoArchiveAdapter(transport=httpx.MockTransport(handler), **kwargs)


async def _no_sleep(seconds: float) -> None:
    return None


async def test_the_request_pins_era5_and_the_bogota_day() -> None:
    """`models=era5` is fixed and `best_match` is never sent: the source of the
    served features must be the source of the trained ones (docs/08 §M2 "Clima",
    D-T0.1)."""
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_ARCHIVE_RESPONSE)

    await _adapter(_handler).fetch_daily(10.9, -74.1, start_day=_START, end_day=_END)

    params = captured[0].url.params
    assert params["models"] == "era5"
    assert "best_match" not in params
    assert params["timezone"] == "America/Bogota"
    assert params["daily"] == ",".join(OPEN_METEO_ARCHIVE_DAILY_VARS)
    assert "soil_moisture_0_to_7cm_mean" in params["daily"]


async def test_one_extra_day_is_requested_before_the_range() -> None:
    """The extra day is what absorbs a `null` first element: without it, the
    range's first day would be the one ERA5 leaves empty."""
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_ARCHIVE_RESPONSE)

    rows = await _adapter(_handler).fetch_daily(10.9, -74.1, start_day=_START, end_day=_END)

    assert captured[0].url.params["start_date"] == "2026-08-31"
    assert captured[0].url.params["end_date"] == "2026-09-07"
    # The extra day is dropped, and the range's first day kept its value.
    assert len(rows) == 7
    assert rows[0].day == _START
    assert rows[0].precipitation_mm == 22.9


async def test_a_missing_measure_stays_missing() -> None:
    """`null` is a third state: a 0.0 mm would be a claim about the weather that
    ERA5 does not make, and every rainfall feature of the month would be a 0."""
    body = {
        "daily": {
            "time": ["2026-08-31", "2026-09-01", "2026-09-02"],
            "precipitation_sum": [None, 22.9, None],
            "soil_moisture_0_to_7cm_mean": [0.5, None, 0.5],
        }
    }

    rows = await _adapter(lambda request: httpx.Response(200, json=body)).fetch_daily(
        10.9, -74.1, start_day=_START, end_day=date(2026, 9, 2)
    )

    assert [row.precipitation_mm for row in rows] == [22.9, None]
    assert rows[0].soil_moisture_m3_m3 is None
    assert rows[0].precipitation_mm != 0.0


async def test_a_range_that_ends_before_it_starts_is_refused() -> None:
    """An inverted window is a caller bug, not a provider error: no request is
    made and the job cannot write a prediction out of one."""
    calls = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_ARCHIVE_RESPONSE)

    with pytest.raises(ValueError):
        await _adapter(_handler).fetch_daily(10.9, -74.1, start_day=_END, end_day=_START)
    assert calls == 0


async def test_a_body_that_is_not_json_is_an_unavailable_source() -> None:
    """A 200 whose body is not JSON (a proxy's HTML error page, a truncated
    response) is a source this adapter cannot read, not a source that answered.
    It must raise the same contract error as any other unreadable answer, and it
    must count as a failure: recording the success first would leave the breaker
    blind to a provider that answers 200 with something else (R3-json-decode-escapes-contract,
    #239)."""
    calls = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text="<html>502 Bad Gateway</html>")

    adapter = _adapter(
        _handler, sleep=_no_sleep, circuit_failure_threshold=1, circuit_cooldown_seconds=60.0
    )

    with pytest.raises(ArchiveUnavailableError):
        await adapter.fetch_daily(10.9, -74.1, start_day=_START, end_day=_END)

    # The failure was counted, so the next call is refused from the breaker's
    # own state instead of paying the provider another round.
    assert adapter.is_circuit_open is True
    with pytest.raises(ArchiveCircuitBreakerOpenError):
        await adapter.fetch_daily(10.9, -74.1, start_day=_START, end_day=_END)
    assert calls == 1


async def test_an_elevation_body_that_is_not_json_is_an_unavailable_source() -> None:
    """The elevation call reads through the same request path, so an unreadable
    body there is the same contract error rather than a raw decode failure."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    with pytest.raises(ArchiveUnavailableError):
        await _adapter(_handler).fetch_elevations([(10.9, -74.1)])


async def test_a_malformed_body_is_an_unavailable_source() -> None:
    """A 200 without a `daily.time` array is not a dry month: it is a source that
    cannot be read, and the cell keeps serving the prediction it has."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"daily": {"precipitation_sum": [1.0]}})

    with pytest.raises(ArchiveUnavailableError):
        await _adapter(_handler).fetch_daily(10.9, -74.1, start_day=_START, end_day=_END)


async def test_each_call_goes_to_the_host_that_serves_it() -> None:
    """The ERA5 archive is not on the forecast host (`api.open-meteo.com/v1/archive`
    answers 404) and the elevation is not on the archive host
    (`archive-api.open-meteo.com/v1/elevation` answers 404, verified
    2026-10-02): one base URL for both would make one of the two M2 inputs
    permanently unavailable."""
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path.endswith("/elevation"):
            return httpx.Response(200, json={"elevation": [615.0]})
        return httpx.Response(200, json=_ARCHIVE_RESPONSE)

    adapter = _adapter(_handler)
    await adapter.fetch_daily(10.9, -74.1, start_day=_START, end_day=_END)
    await adapter.fetch_elevations([(10.9, -74.1)])

    assert captured[0].url.host == "archive-api.open-meteo.com"
    assert captured[0].url.path == "/v1/archive"
    assert captured[1].url.host == "api.open-meteo.com"
    assert captured[1].url.path == "/v1/elevation"


async def test_elevations_come_back_in_the_order_the_points_were_sent() -> None:
    """The five points of one cell (centre + 4 neighbours, docs/08 §M2
    "Elevación y pendiente") fit in one call, and the slope reads them by
    position."""
    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"elevation": [615.0, 481.0, 684.0, 704.0, 330.0]})

    points = [
        (10.9, -74.1),
        (10.9, -74.09086),
        (10.9, -74.10914),
        (10.90914, -74.1),
        (10.89086, -74.1),
    ]

    elevations = await _adapter(_handler).fetch_elevations(points)

    assert elevations == [615.0, 481.0, 684.0, 704.0, 330.0]
    assert len(captured) == 1
    params = captured[0].url.params
    assert params["latitude"] == "10.9,10.9,10.9,10.90914,10.89086"
    assert params["longitude"] == "-74.1,-74.09086,-74.10914,-74.1,-74.1"


async def test_a_point_without_an_elevation_stays_unknown() -> None:
    """A fabricated neighbour elevation would produce a fabricated slope, so the
    provider's `null` is kept."""
    rows = await _adapter(
        lambda request: httpx.Response(200, json={"elevation": [615.0, None, 684.0]})
    ).fetch_elevations([(10.9, -74.1), (10.9, -74.09086), (10.9, -74.10914)])

    assert rows == [615.0, None, 684.0]
    assert rows[1] != 0.0


async def test_an_elevations_response_of_the_wrong_length_is_refused() -> None:
    """A short answer would shift every neighbour after it into the wrong
    position, so it is malformed, not a shorter list."""

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"elevation": [615.0]})

    with pytest.raises(ArchiveUnavailableError):
        await _adapter(_handler).fetch_elevations([(10.9, -74.1), (10.9, -74.09086)])


async def test_no_points_means_no_call() -> None:
    calls = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"elevation": []})

    assert await _adapter(_handler).fetch_elevations([]) == []
    assert calls == 0


async def test_a_server_error_is_retried_three_times_before_it_fails() -> None:
    """docs/06 §6: 3 retries with exponential backoff and jitter behind the
    breaker. The retries are spent, not silently dropped."""
    calls = 0
    slept: list[float] = []

    async def _sleep(seconds: float) -> None:
        slept.append(seconds)

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, text="unavailable")

    with pytest.raises(ArchiveUnavailableError):
        await _adapter(_handler, sleep=_sleep).fetch_daily(
            10.9, -74.1, start_day=_START, end_day=_END
        )

    assert calls == 4
    assert slept == pytest.approx([0.5, 1.0, 2.0], abs=0.2)
    assert len(slept) == 3


async def test_a_transient_failure_recovers_on_a_retry() -> None:
    calls = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, json=_ARCHIVE_RESPONSE)

    rows = await _adapter(_handler, sleep=_no_sleep).fetch_daily(
        10.9, -74.1, start_day=_START, end_day=_END
    )

    assert len(rows) == 7
    assert calls == 3


async def test_a_client_error_is_not_retried() -> None:
    """A `start_date` outside the archive's coverage answers 400 and would fail
    the same way three more times, so the retries are not spent on it."""
    calls = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"reason": "out of allowed range"})

    with pytest.raises(ArchiveUnavailableError):
        await _adapter(_handler, sleep=_no_sleep).fetch_daily(
            10.9, -74.1, start_day=_START, end_day=_END
        )
    assert calls == 1


async def test_the_breaker_opens_and_stops_calling_the_provider() -> None:
    """The shared breaker is what keeps a provider outage from being paid for by
    every cell of the run: after the threshold, the calls fail fast without
    reaching the network."""

    calls = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, text="down")

    adapter = _adapter(
        _handler, sleep=_no_sleep, circuit_failure_threshold=2, circuit_cooldown_seconds=60.0
    )

    for _ in range(2):
        with pytest.raises(ArchiveUnavailableError):
            await adapter.fetch_daily(10.9, -74.1, start_day=_START, end_day=_END)
    assert adapter.is_circuit_open is True
    calls_before_the_open_circuit = calls

    with pytest.raises(ArchiveCircuitBreakerOpenError):
        await adapter.fetch_daily(10.9, -74.1, start_day=_START, end_day=_END)

    # The breaker answered from its own state: the provider was not called again.
    assert calls == calls_before_the_open_circuit
    assert calls_before_the_open_circuit == 8  # 2 calls x (1 + 3 retries)


async def test_a_successful_call_resets_the_failure_count() -> None:
    """The breaker counts *consecutive* failures: an outage that recovers must
    not open the circuit on the next blip."""
    calls = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        # The first fetch spends its 1 + 3 attempts failing; the second answers.
        return httpx.Response(500 if calls <= 4 else 200, json=_ARCHIVE_RESPONSE)

    adapter = _adapter(
        _handler, sleep=_no_sleep, circuit_failure_threshold=4, circuit_cooldown_seconds=60.0
    )
    with pytest.raises(ArchiveUnavailableError):
        await adapter.fetch_daily(10.9, -74.1, start_day=_START, end_day=_END)
    assert adapter.is_circuit_open is False

    rows = await adapter.fetch_daily(10.9, -74.1, start_day=_START, end_day=_END)

    assert len(rows) == 7
    assert adapter.is_circuit_open is False


async def test_the_seminar_profile_answers_without_the_network() -> None:
    """ADR-0021: en el perfil seminario las respuestas están grabadas. The demo
    must produce a week of ERA5 values with no internet, for any range."""
    adapter = seminar_archive_adapter()
    start_day = date(2026, 9, 1)

    rows = await adapter.fetch_daily(10.9, -74.1, start_day=start_day, end_day=date(2026, 9, 7))

    assert len(rows) == 7
    assert rows[0].day == start_day
    assert rows[0].precipitation_mm is not None
    # An empty answer would be a dry month, and the demo's flood path is the
    # point of the fixture.
    assert sum(row.precipitation_mm or 0.0 for row in rows) > 0


async def test_the_seminar_profile_answers_elevations_from_the_recording() -> None:
    elevations = await seminar_archive_adapter().fetch_elevations(
        [(10.9, -74.1), (10.9, -74.09086), (10.9, -74.10914), (10.90914, -74.1), (10.89086, -74.1)]
    )

    assert len(elevations) == 5
    assert all(elevation is not None for elevation in elevations)
    # A point beyond the recording is unknown, not invented.
    beyond = await seminar_archive_adapter().fetch_elevations([(0.0, 0.0)] * 6)
    assert beyond[5] is None


def test_the_profile_picks_the_free_endpoint_in_the_seminar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0021: Open-Meteo gratuito es no comercial y cubre el seminario;
    producción necesita su plan de pago."""
    monkeypatch.setenv("TECHCAMP_PROFILE", "seminar")

    adapter = get_risk_archive_adapter()

    assert isinstance(adapter, OpenMeteoArchiveAdapter)
    assert adapter._archive_url == "https://archive-api.open-meteo.com/v1/archive"  # noqa: SLF001
    assert adapter._elevation_url == "https://api.open-meteo.com/v1/elevation"  # noqa: SLF001
    assert adapter._api_key is None  # noqa: SLF001


def test_the_profile_picks_the_commercial_endpoint_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TECHCAMP_PROFILE", "production")
    monkeypatch.setenv("OPEN_METEO_API_KEY", "prod-key-123")

    adapter = get_risk_archive_adapter()

    assert adapter._archive_url == "https://customer-api.open-meteo.com/v1/archive"  # noqa: SLF001
    assert adapter._elevation_url == "https://customer-api.open-meteo.com/v1/elevation"  # noqa: SLF001
    assert adapter._api_key == "prod-key-123"  # noqa: SLF001
