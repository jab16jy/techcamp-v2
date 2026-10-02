"""Open-Meteo historical archive and elevation REST adapter (docs/06-diseno-detallado.md
§6, §8 "Datos de entrada"; docs/08-ml.md §M2 "Clima", "Elevación y pendiente",
D-T0.1, D-T0.6; ADR-0021).

The M2 inputs of one cell: the daily `precipitation_sum` and
`soil_moisture_0_to_7cm_mean` of the last months, and the elevation of its
centre and four neighbours. Serving reads the archive and not `weather_daily`,
because the forecast rows come from a different source than the training data
and the parity docs/08 §M2 "Features" demands is a parity of *source*
(docs/06 §8, D-T0.6).

The two calls do not live on the same host: the ERA5 archive is only served by
`archive-api.open-meteo.com` (`api.open-meteo.com/v1/archive` answers 404) and
the elevation by `api.open-meteo.com` (verified 2026-10-02), so both URLs are
constructor arguments and each call uses its own.

Three things this adapter refuses to leave to a caller:

* **`models=era5`, pinned.** `best_match` is not an option for a model that must
  reproduce its training features: it silently changes the source with the
  model Open-Meteo prefers that week. ERA5 is also the only one of the two that
  answers both measures — a `models=era5_land` request for the same parameters
  returns `null` precipitation (verified 2026-10-02), which would make every
  rainfall feature a 0 mm claim (docs/08 §M2 "Clima").
* **`timezone=America/Bogota`.** The features are calendar months, and the
  product's day is the local one (docs/10-dag.md §3), so the archive must not
  shift a day across a month boundary on UTC.
* **One extra day before the range.** The first day of an ERA5 window can come
  back `null`, so the requested range starts one day later than the response
  does; the extra day is dropped before the rows are returned.

The resilience is the one `weather/adapters/open_meteo.py` uses — 10 s timeout,
3 retries with exponential backoff and jitter on transport errors, 5xx and 429,
behind `shared/circuit_breaker.py` — kept here rather than imported because a
module may not reach another module's `adapters`
(docs/05-arquitectura.md §Solo la fachada pública), and the breaker, which is what
carries the state, is already shared.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import date, timedelta
from typing import Any

import httpx

from techcamp.risk.domain.errors import ArchiveCircuitBreakerOpenError, ArchiveUnavailableError
from techcamp.risk.domain.models import ArchiveDay
from techcamp.shared.circuit_breaker import (
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_FAILURE_THRESHOLD,
    CircuitBreaker,
    CircuitState,
)
from techcamp.shared.config import is_seminar_profile, open_meteo_api_key

logger = logging.getLogger(__name__)

OPEN_METEO_ARCHIVE_DAILY_VARS: tuple[str, ...] = (
    "precipitation_sum",
    "soil_moisture_0_to_7cm_mean",
)

_ARCHIVE_MODEL = "era5"
"""Pinned, never `best_match` (docs/08 §M2 "Clima", D-T0.1)."""

_TIMEZONE = "America/Bogota"
"""Not a parameter: the day of a prediction is the Bogota day (docs/10-dag.md §3)."""

_DEFAULT_FREE_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
"""The ERA5 archive is not on the forecast host: `api.open-meteo.com/v1/archive`
answers 404 for it (verified 2026-10-02)."""

_DEFAULT_FREE_ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"

_DEFAULT_COMMERCIAL_BASE_URL = "https://customer-api.open-meteo.com/v1"
"""ADR-0021: Open-Meteo gratuito es de uso no comercial y cubre el seminario;
producción necesita su plan de pago. The customer endpoint serves the same
paths with the same syntax, only the domain and the key differ
(open-meteo.com/en/pricing)."""

_ELEVATION_PATH = "/elevation"
_DEFAULT_TIMEOUT_SECONDS = 10.0
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_RETRY_BASE_DELAY = 0.5

ELEVATION_MAX_POINTS = 100
"""Coordinates Open-Meteo accepts in one elevation call
(open-meteo.com/en/docs/elevation-api)."""


def _extract_float(values: Any, index: int) -> float | None:
    """One value of a daily array, `None` when absent or `null`.

    ERA5 reports `null` for a day it has not aggregated; that is missing
    evidence and stays `None`, never `0.0`.
    """
    if not isinstance(values, list) or index >= len(values):
        return None
    value = values[index]
    return None if value is None else float(value)


def _parse_daily(body: Any) -> list[ArchiveDay]:
    """Every day of the response, in the order the provider sent them.

    One parsing boundary: a body without a `daily.time` array is
    `ArchiveUnavailableError`, not an empty series that would read as a dry
    month.
    """
    try:
        daily = body["daily"]
        times = daily["time"]
        if not isinstance(times, list):
            raise TypeError(f"'daily.time' is not a list: {times!r}")
        precipitation = daily.get("precipitation_sum")
        soil_moisture = daily.get("soil_moisture_0_to_7cm_mean")
        return [
            ArchiveDay(
                day=date.fromisoformat(day_str),
                precipitation_mm=_extract_float(precipitation, index),
                soil_moisture_m3_m3=_extract_float(soil_moisture, index),
            )
            for index, day_str in enumerate(times)
        ]
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("malformed Open-Meteo archive response: %s", exc)
        raise ArchiveUnavailableError(f"malformed Open-Meteo archive response: {exc}") from exc


def _parse_elevation(body: Any, expected: int) -> list[float | None]:
    """The elevation of each requested point, in the order they were sent.

    A response that does not answer for every point is malformed rather than
    short: the elevations feed `slope_degrees`, which reads the neighbours by
    position, and a silently shortened list would shift them.
    """
    try:
        elevations = body["elevation"]
        if not isinstance(elevations, list) or len(elevations) != expected:
            raise ValueError(f"expected {expected} elevations, got {elevations!r}")
        return [None if value is None else float(value) for value in elevations]
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("malformed Open-Meteo elevation response: %s", exc)
        raise ArchiveUnavailableError(f"malformed Open-Meteo elevation response: {exc}") from exc


class OpenMeteoArchiveAdapter:
    """Real Open-Meteo archive adapter for the M2 features.

    Pass `transport` to intercept the HTTP calls with `httpx.MockTransport` in
    tests and in the seminar profile; omit it for real network calls. One
    instance is shared per worker process, because the circuit breaker's
    counters live in the instance (the same reason `weather`'s adapter is one
    per process).
    """

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        archive_url: str = _DEFAULT_FREE_ARCHIVE_URL,
        elevation_url: str = _DEFAULT_FREE_ELEVATION_URL,
        api_key: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        retry_base_delay: float = _DEFAULT_RETRY_BASE_DELAY,
        circuit_failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        circuit_cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport
        self._archive_url = archive_url
        self._elevation_url = elevation_url
        self._api_key = api_key
        self._timeout = timeout
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
        self, lat: float, lon: float, *, start_day: date, end_day: date
    ) -> list[ArchiveDay]:
        """The archive days of `[start_day, end_day]` for one point.

        One extra day before the range is requested and dropped, so a `null`
        first element of the response does not cost the range its first day.
        A day the archive has no value for is returned with `None` measures
        rather than omitted: the caller tells "no rain reported" from "no day
        at all" only if the day is still there.
        """
        if end_day < start_day:
            raise ValueError(f"end_day {end_day} is before start_day {start_day}")

        params: dict[str, Any] = {
            "latitude": lat,
            "longitude": lon,
            "daily": ",".join(OPEN_METEO_ARCHIVE_DAILY_VARS),
            "start_date": (start_day - timedelta(days=1)).isoformat(),
            "end_date": end_day.isoformat(),
            "models": _ARCHIVE_MODEL,
            "timezone": _TIMEZONE,
        }
        rows = _parse_daily(await self._get(self._archive_url, params))
        return [row for row in rows if start_day <= row.day <= end_day]

    async def fetch_elevations(self, points: Sequence[tuple[float, float]]) -> list[float | None]:
        """The elevation (m) of each `(lat, lon)` point, in the same order.

        One cell needs five of them — the centre and its four neighbours for
        the slope — which fit in a single call; longer lists are chunked to the
        100 coordinates the elevation API accepts per request. A point the
        provider has no value for is `None`, not the centre's elevation: a slope
        computed from an invented neighbour is a slope of the wrong hill.
        """
        elevations: list[float | None] = []
        for start in range(0, len(points), ELEVATION_MAX_POINTS):
            chunk = points[start : start + ELEVATION_MAX_POINTS]
            params: dict[str, Any] = {
                "latitude": ",".join(str(lat) for lat, _ in chunk),
                "longitude": ",".join(str(lon) for _, lon in chunk),
            }
            elevations.extend(
                _parse_elevation(await self._get(self._elevation_url, params), len(chunk))
            )
        return elevations

    async def _get(self, url: str, params: dict[str, Any]) -> Any:
        """One GET with the shared retry and circuit-breaker shape
        (docs/06 §6).

        429 and 5xx are retried, other 4xx are not: a malformed request will
        fail the same way three more times, and the archive answers a
        `start_date` outside its coverage with exactly that (its window closes
        ~5 days behind, docs/08 §M2 "Clima").
        """
        if not self._circuit_breaker.allow_request():
            raise ArchiveCircuitBreakerOpenError("Open-Meteo archive circuit breaker is OPEN")

        if self._api_key:
            params = {**params, "apikey": self._api_key}

        attempts = 0
        while True:
            try:
                async with httpx.AsyncClient(
                    transport=self._transport, timeout=self._timeout
                ) as client:
                    response = await client.get(url, params=params)

                if response.status_code == 429 or response.status_code >= 500:
                    if attempts < self._max_retries:
                        attempts += 1
                        backoff = self._retry_base_delay * (2 ** (attempts - 1))
                        jitter = random.uniform(0, 0.1 * backoff)
                        await self._sleep(backoff + jitter)
                        continue
                    raise ArchiveUnavailableError(
                        f"Open-Meteo archive returned status {response.status_code}"
                    )

                if response.status_code != 200:
                    raise ArchiveUnavailableError(
                        f"Open-Meteo archive returned status {response.status_code}"
                    )

                try:
                    body = response.json()
                except ValueError as exc:
                    # `json.JSONDecodeError` is a `ValueError`, and a 200 that is
                    # not JSON (a proxy's HTML error page) is a source this
                    # adapter cannot read: the `except ArchiveUnavailableError`
                    # below records the failure and re-raises, which is why the
                    # success is only recorded once the body decoded (#239).
                    raise ArchiveUnavailableError(
                        f"Open-Meteo returned a body that is not JSON: {exc}"
                    ) from exc

                self._circuit_breaker.record_success()
                return body

            except httpx.RequestError as exc:
                if attempts < self._max_retries:
                    attempts += 1
                    backoff = self._retry_base_delay * (2 ** (attempts - 1))
                    jitter = random.uniform(0, 0.1 * backoff)
                    await self._sleep(backoff + jitter)
                    continue
                self._circuit_breaker.record_failure()
                raise ArchiveUnavailableError(
                    f"Open-Meteo archive connection error: {exc}"
                ) from exc
            except ArchiveUnavailableError:
                self._circuit_breaker.record_failure()
                raise


def get_risk_archive_adapter() -> OpenMeteoArchiveAdapter:
    """ADR-0021: the seminar profile calls the free Open-Meteo API; production
    uses the commercial endpoint with an API key."""
    if is_seminar_profile():
        return OpenMeteoArchiveAdapter()
    return OpenMeteoArchiveAdapter(
        archive_url=f"{_DEFAULT_COMMERCIAL_BASE_URL}/archive",
        elevation_url=f"{_DEFAULT_COMMERCIAL_BASE_URL}{_ELEVATION_PATH}",
        api_key=open_meteo_api_key(),
    )


_SEMINAR_RECORDED_WEEK: tuple[tuple[str, float, float], ...] = (
    ("2026-08-24", 22.9, 0.499),
    ("2026-08-25", 53.9, 0.493),
    ("2026-08-26", 25.3, 0.500),
    ("2026-08-27", 22.4, 0.492),
    ("2026-08-28", 15.3, 0.488),
    ("2026-08-29", 23.5, 0.492),
    ("2026-08-30", 11.0, 0.488),
    ("2026-08-31", 51.8, 0.503),
)
"""The ERA5 week recorded on 2026-10-02 for the seminar cell
(10.9, -74.1), verbatim from
`archive-api.open-meteo.com/v1/archive?daily=precipitation_sum,soil_moisture_0_to_7cm_mean&models=era5&timezone=America/Bogota`
(day, precipitation mm, soil moisture m³/m³). A wet Caribbean-lowland week,
which is what makes the demo's flood path interesting."""

_SEMINAR_RECORDED_ELEVATIONS: tuple[float, ...] = (615.0, 481.0, 684.0, 704.0, 330.0)
"""Centre, east, west, north and south ~1 km around it, recorded the same day
from `api.open-meteo.com/v1/elevation` (Copernicus GLO-90): a centre 200 m
above its northern neighbour and 285 m above its southern one, so the recorded
cell has a real slope for the demo."""


def seminar_archive_adapter() -> OpenMeteoArchiveAdapter:
    """ADR-0021: the seminar profile replays these recorded responses offline
    instead of calling Open-Meteo, like `farms`' SoilGrids fixture.

    The recorded week is replayed onto the days the request asks for, in order
    and cycling, because the demo must run for any `day` a facilitator picks
    (`POST /dev/jobs/risk:run`) with no internet: those are the recorded wet
    week's *values*, not a prediction of the days they are shown on. A point
    beyond the five recorded ones is answered `null`, because a fabricated
    neighbour elevation would produce a fabricated slope.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if request.url.path.endswith(_ELEVATION_PATH):
            latitudes = params.get("latitude", "").split(",")
            return httpx.Response(
                200,
                json={
                    "elevation": [
                        _SEMINAR_RECORDED_ELEVATIONS[index]
                        if index < len(_SEMINAR_RECORDED_ELEVATIONS)
                        else None
                        for index in range(len(latitudes))
                    ]
                },
            )

        start_day = date.fromisoformat(params["start_date"])
        end_day = date.fromisoformat(params["end_date"])
        days = (end_day - start_day).days + 1
        recorded = [
            _SEMINAR_RECORDED_WEEK[index % len(_SEMINAR_RECORDED_WEEK)] for index in range(days)
        ]
        return httpx.Response(
            200,
            json={
                "latitude": 11.0,
                "longitude": -74.0,
                "timezone": _TIMEZONE,
                "elevation": _SEMINAR_RECORDED_ELEVATIONS[0],
                "daily_units": {
                    "time": "iso8601",
                    "precipitation_sum": "mm",
                    "soil_moisture_0_to_7cm_mean": "m³/m³",
                },
                "daily": {
                    "time": [
                        (start_day + timedelta(days=index)).isoformat() for index in range(days)
                    ],
                    "precipitation_sum": [day[1] for day in recorded],
                    "soil_moisture_0_to_7cm_mean": [day[2] for day in recorded],
                },
            },
        )

    return OpenMeteoArchiveAdapter(transport=httpx.MockTransport(_handler))
