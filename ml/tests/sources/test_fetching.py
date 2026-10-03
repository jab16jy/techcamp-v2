"""The downloader: a throttled or failing host is retried, and never cached as a source.

Every request here is answered by an `httpx.MockTransport`, so the retry loop, the
backoff and the throttle are proven without touching the network (ADR-0021).
"""

from collections.abc import Iterator, Sequence
from pathlib import Path

import httpx
import pytest

from techcamp_ml.sources import fetching
from techcamp_ml.sources.cache import cached_files, read_manifest
from techcamp_ml.sources.fetching import FetchError, fetch, interval_seconds
from techcamp_ml.sources.layout import Layout

URL = "https://example.test/archive"


def _transport(
    outcomes: Sequence[httpx.Response | Exception],
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """A transport that answers `outcomes` in order, repeating the last one."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        outcome = outcomes[min(len(seen) - 1, len(outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return httpx.MockTransport(handler), seen


@pytest.fixture(autouse=True)
def slept(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[float]]:
    """Neither the throttle nor the backoff ever sleeps in a test; both are recorded."""
    slept: list[float] = []
    monkeypatch.setattr(fetching.time, "sleep", slept.append)
    monkeypatch.setattr(fetching, "_last_request_at", 0.0)
    yield slept


def test_a_transport_error_is_retried_and_the_second_body_lands(
    tmp_path: Path,
    slept: list[float],
) -> None:
    layout = Layout(tmp_path)
    transport, seen = _transport(
        [httpx.ConnectError("the socket died"), httpx.Response(200, content=b'{"ok": 1}')]
    )

    payload = fetch(layout, "weather", "archive_000.json", URL, transport=transport, retries=3)

    assert payload == b'{"ok": 1}'
    # Negative half: the failed attempt asked once, waited a backoff and then the
    # throttle before retrying, and cached nothing of its own.
    assert len(seen) == 2
    assert slept[0] == pytest.approx(1.0), "2**0 seconds between a failure and its retry"
    assert slept[1] == pytest.approx(interval_seconds(0), abs=0.01)
    assert cached_files(layout, "weather") == ["archive_000.json"]


def test_a_server_error_is_retried_up_to_the_limit_and_then_returns(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    transport, seen = _transport(
        [httpx.Response(503), httpx.Response(502), httpx.Response(200, content=b"{}")]
    )

    assert fetch(layout, "weather", "archive_000.json", URL, transport=transport) == b"{}"

    # Negative half: two failures are retried and the third answer is kept as it is.
    assert len(seen) == 3
    assert cached_files(layout, "weather") == ["archive_000.json"]


def test_a_throttled_host_is_retried_to_the_limit_and_never_cached(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    transport, seen = _transport([httpx.Response(429, headers={"Retry-After": "1"})])

    with pytest.raises(FetchError, match="429"):
        fetch(layout, "weather", "archive_000.json", URL, transport=transport, retries=4)

    # Negative half: a throttled answer is never mistaken for a source copy.
    assert len(seen) == 4
    assert cached_files(layout, "weather") == []
    assert len(read_manifest(layout)) == 0


def test_a_client_error_raises_at_once_and_is_not_cached(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    transport, seen = _transport([httpx.Response(404, text="no such dataset")])

    with pytest.raises(FetchError, match="404"):
        fetch(layout, "weather", "archive_000.json", URL, transport=transport)

    # Negative half: a 404 is an answer, not a failure to retry.
    assert len(seen) == 1
    assert cached_files(layout, "weather") == []


def test_a_request_waits_the_interval_its_coordinates_cost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    slept: list[float],
) -> None:
    layout = Layout(tmp_path)
    transport, seen = _transport([httpx.Response(200, content=b"{}")])
    # The clock says a request just went out, so this one has to wait its turn.
    monkeypatch.setattr(fetching, "_last_request_at", fetching.time.monotonic())

    fetch(layout, "weather", "archive_000.json", URL, transport=transport, weight=25)

    # Negative half: the wait is what the coordinates cost, not a fixed nap.
    assert slept[0] == pytest.approx(interval_seconds(25), abs=0.01)
    assert len(seen) == 1
