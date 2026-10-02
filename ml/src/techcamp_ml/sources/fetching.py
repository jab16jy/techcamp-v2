"""The one place that talks to the network.

datos.gov.co answers 503 under load, so a 5xx or a transport error is retried with
a backoff instead of being cached as a source copy. Every successful response goes
through `cache.save_raw`, which is what makes the fetch resumable: a chunk already in
the cache is never requested again.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

import httpx

from techcamp_ml.sources.cache import save_raw
from techcamp_ml.sources.layout import Layout

BASE_INTERVAL_SECONDS = 2.0
SECONDS_PER_COORDINATE = 0.25
"""The gap between requests grows with how many coordinates a request carries.

Open-Meteo weighs a query by its variables, locations and domains, and the archive
weighs it further by the length of the requested range; its free tier is 10,000/day
per IP with minute and hourly buckets on top ("Rate Limiting" in its docs). Whether
a given batch fits a bucket is the host's arithmetic, not ours, so the gap is
proportional to the batch and every 429 is honoured through `retry_delay` instead of
being guessed at. 0.25 s per coordinate is well under any published per-minute
figure."""

DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_RETRIES = 5
RETRYABLE_STATUSES = (429, 500, 502, 503, 504)
"""A throttled host is retried, never cached as a source copy: the free Open-Meteo
tier answers 429 with a `Retry-After` window, and datos.gov.co answers 503 under load."""


class FetchError(RuntimeError):
    """A request that never produced a usable response."""


_last_request_at = 0.0


def fetch(
    layout: Layout,
    source: str,
    name: str,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    weight: int = 0,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    retries: int = DEFAULT_RETRIES,
) -> bytes:
    """Download one response into the raw cache and return its bytes.

    `weight` is the number of coordinates the request asks for: the throttle counts
    coordinates, because that is what the host throttles on.
    """
    payload = _get(url, params=params, weight=weight, timeout=timeout, retries=retries)
    save_raw(layout, source, name, url, payload)
    return payload


def interval_seconds(weight: int = 0) -> float:
    """The floor between two requests carrying `weight` coordinates."""
    return BASE_INTERVAL_SECONDS + SECONDS_PER_COORDINATE * weight


def retry_delay(response: httpx.Response, *, attempt: int) -> float:
    """Seconds to wait before retrying: what the host asks for, else a backoff.

    A 429 without `Retry-After` waits the minute Open-Meteo names in its message.
    """
    requested = response.headers.get("Retry-After")
    if requested is not None and requested.strip().isdigit():
        return float(requested)
    if response.status_code == 429:
        return 60.0
    return float(2**attempt) if response.status_code in RETRYABLE_STATUSES else 0.0


def _get(
    url: str,
    *,
    params: Mapping[str, Any] | None,
    weight: int,
    timeout: float,
    retries: int,
) -> bytes:
    global _last_request_at  # noqa: PLW0603 - the throttle is process-wide by design
    interval = interval_seconds(weight)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for attempt in range(retries):
            wait = interval - (time.monotonic() - _last_request_at)
            if wait > 0:
                time.sleep(wait)
            _last_request_at = time.monotonic()
            try:
                response = client.get(url, params=params)
            except httpx.HTTPError as error:
                if attempt == retries - 1:
                    raise FetchError(f"{url} failed after {retries} attempts: {error}") from error
                time.sleep(2**attempt)
                continue
            if response.status_code == httpx.codes.OK:
                return response.content
            if response.status_code in RETRYABLE_STATUSES and attempt < retries - 1:
                time.sleep(retry_delay(response, attempt=attempt))
                continue
            raise FetchError(f"{url} returned {response.status_code}: {response.text[:200]}")
    raise FetchError(f"{url} failed after {retries} attempts")


def chunks[T](items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    """Split a request's worth of coordinates into batches the free tier accepts."""
    if size < 1:
        raise ValueError(f"batch size must be positive, got {size}")
    for start in range(0, len(items), size):
        yield items[start : start + size]
