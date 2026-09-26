"""SSE fan-out adapter (docs/04-api.md:180-189, ADR-0015, E4 T6).

`PlotEventsHub` is pure dispatch logic (no live Postgres `LISTEN` needed to
test it: `dispatch()` is called directly, the same shape asyncpg would pass
to the `add_listener` callback). `stream_plot_events` is the per-client SSE
generator; its keepalive interval is injected so the test never waits the
real 20 s (task instruction).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import asyncpg
import pytest

from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters import sse_hub
from techcamp.telemetry.adapters.api.stream import stream_plot_events
from techcamp.telemetry.adapters.sse_hub import PlotEventsHub

pytestmark = pytest.mark.anyio


class _FakeConnection:
    """A `asyncpg.Connection` double: records what the hub registers on it
    and lets a test fire the termination listener to simulate a dropped
    `LISTEN` connection.

    `close()` fires the termination callback, as asyncpg does on any close:
    the hub's own docstring says the listener fires "on any connection close,
    planned or not"."""

    def __init__(self) -> None:
        self.listener_added = False
        self.closed = False
        self._termination_cb: Callable[[object], None] | None = None

    async def add_listener(self, _channel: str, _callback: object) -> None:
        self.listener_added = True

    def add_termination_listener(self, callback: Callable[[object], None]) -> None:
        self._termination_cb = callback

    async def remove_listener(self, _channel: str, _callback: object) -> None:
        pass

    async def close(self) -> None:
        self.closed = True
        self.simulate_loss()

    def simulate_loss(self) -> None:
        assert self._termination_cb is not None
        self._termination_cb(self)


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 1.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.001)


class _FailingListenConnection(_FakeConnection):
    """`add_listener` fails the way asyncpg does when the server goes away
    right after the handshake (#38)."""

    def __init__(self) -> None:
        super().__init__()
        self.add_listener_started = False

    async def add_listener(self, _channel: str, _callback: object) -> None:
        self.add_listener_started = True
        raise asyncpg.PostgresConnectionError("connection was closed in the middle of an operation")


class _PendingListenConnection(_FakeConnection):
    """`add_listener` never returns until a test releases it, so `stop()` can
    cancel `_connect` between the connect and the assignment (#38)."""

    def __init__(self) -> None:
        super().__init__()
        self.add_listener_started = False
        self._released = asyncio.Event()

    async def add_listener(self, _channel: str, _callback: object) -> None:
        self.add_listener_started = True
        await self._released.wait()

    def release(self) -> None:
        self._released.set()


def _reading_payload(*, farm_id: object, plot_id: object) -> str:
    return json.dumps(
        {
            "type": "reading",
            "org_id": str(uuid7()),
            "farm_id": str(farm_id),
            "plot_id": str(plot_id),
            "metric": "soil_moisture",
            "value": 21.5,
            "at": "2026-03-01T12:00:00+00:00",
        }
    )


def _status_payload(*, farm_id: object, node_id: object) -> str:
    return json.dumps(
        {
            "type": "node.status",
            "org_id": str(uuid7()),
            "farm_id": str(farm_id),
            "node_id": str(node_id),
            "status": "offline",
            "at": "2026-03-01T12:00:00+00:00",
        }
    )


# -- PlotEventsHub: farm-filtered dispatch --


def test_hub_dispatches_only_to_matching_farm_subscribers() -> None:
    hub = PlotEventsHub()
    farm_a, farm_b = uuid7(), uuid7()
    client_a, queue_a = hub.subscribe(farm_a)
    client_b, queue_b = hub.subscribe(farm_b)

    hub.dispatch(_reading_payload(farm_id=farm_a, plot_id=uuid7()))

    assert queue_a.qsize() == 1
    assert queue_b.qsize() == 0
    del client_b  # only used to keep the subscription alive


def test_hub_dispatch_carries_documented_fields_only() -> None:
    hub = PlotEventsHub()
    farm_id, plot_id = uuid7(), uuid7()
    _client_id, queue = hub.subscribe(farm_id)

    hub.dispatch(_reading_payload(farm_id=farm_id, plot_id=plot_id))

    event = queue.get_nowait()
    assert event.event == "reading"
    assert event.data == {
        "plot_id": str(plot_id),
        "metric": "soil_moisture",
        "value": 21.5,
        "at": "2026-03-01T12:00:00+00:00",
    }
    assert event.id == 1


def test_hub_dispatch_node_status_carries_documented_fields_only() -> None:
    hub = PlotEventsHub()
    farm_id, node_id = uuid7(), uuid7()
    _client_id, queue = hub.subscribe(farm_id)

    hub.dispatch(_status_payload(farm_id=farm_id, node_id=node_id))

    event = queue.get_nowait()
    assert event.event == "node.status"
    assert event.data == {
        "node_id": str(node_id),
        "status": "offline",
        "at": "2026-03-01T12:00:00+00:00",
    }


def test_hub_event_id_is_monotonic_per_process() -> None:
    hub = PlotEventsHub()
    farm_id = uuid7()
    _client_id, queue = hub.subscribe(farm_id)

    hub.dispatch(_reading_payload(farm_id=farm_id, plot_id=uuid7()))
    hub.dispatch(_reading_payload(farm_id=farm_id, plot_id=uuid7()))

    assert queue.get_nowait().id == 1
    assert queue.get_nowait().id == 2


def test_hub_ignores_malformed_and_unknown_payloads() -> None:
    hub = PlotEventsHub()
    farm_id = uuid7()
    _client_id, queue = hub.subscribe(farm_id)

    hub.dispatch("not json")
    hub.dispatch(json.dumps({"type": "alert.opened", "farm_id": str(farm_id)}))

    assert queue.qsize() == 0


def test_hub_drops_non_object_json_payload() -> None:
    """A JSON array or scalar has no `.get` (AttributeError today) — dropped,
    not raised (R3-partial-payload-unhandled)."""
    hub = PlotEventsHub()
    farm_id = uuid7()
    _client_id, queue = hub.subscribe(farm_id)

    hub.dispatch(json.dumps(["not", "an", "object"]))

    assert queue.qsize() == 0


def test_hub_drops_payload_missing_required_fields() -> None:
    """A `reading` missing `plot_id`/`metric`/`value`/`at` raises `KeyError`
    today — dropped, not raised (R3-partial-payload-unhandled)."""
    hub = PlotEventsHub()
    farm_id = uuid7()
    _client_id, queue = hub.subscribe(farm_id)

    hub.dispatch(json.dumps({"type": "reading", "farm_id": str(farm_id)}))

    assert queue.qsize() == 0


def test_hub_drops_payload_with_invalid_farm_id() -> None:
    """A non-UUID `farm_id` raises `ValueError` today — dropped, not raised
    (R3-partial-payload-unhandled)."""
    hub = PlotEventsHub()
    farm_id = uuid7()
    _client_id, queue = hub.subscribe(farm_id)

    hub.dispatch(
        json.dumps(
            {
                "type": "reading",
                "farm_id": "not-a-uuid",
                "plot_id": str(uuid7()),
                "metric": "soil_moisture",
                "value": 1.0,
                "at": "2026-03-01T12:00:00+00:00",
            }
        )
    )

    assert queue.qsize() == 0


def test_hub_drops_slow_client_without_blocking_others() -> None:
    hub = PlotEventsHub()
    farm_id = uuid7()
    slow_id, slow_queue = hub.subscribe(farm_id, maxsize=1)
    _fast_id, fast_queue = hub.subscribe(farm_id, maxsize=10)

    hub.dispatch(_reading_payload(farm_id=farm_id, plot_id=uuid7()))
    hub.dispatch(_reading_payload(farm_id=farm_id, plot_id=uuid7()))

    assert not hub.is_subscribed(slow_id)
    assert slow_queue.qsize() == 1  # the first message that fit, never blocked
    assert fast_queue.qsize() == 2  # the other client is unaffected


def test_hub_unsubscribe_is_idempotent() -> None:
    hub = PlotEventsHub()
    client_id, _queue = hub.subscribe(uuid7())

    hub.unsubscribe(client_id)
    hub.unsubscribe(client_id)  # must not raise

    assert not hub.is_subscribed(client_id)


# -- stream_plot_events: per-client SSE generator --


async def test_stream_plot_events_yields_queued_event() -> None:
    hub = PlotEventsHub()
    farm_id, plot_id = uuid7(), uuid7()

    gen = stream_plot_events(hub, farm_id, keepalive_interval=5.0)
    # The generator subscribes when its body first runs, so the event has to be
    # published after that, never before.
    first = asyncio.ensure_future(anext(gen))
    await _wait_until(lambda: hub.subscriber_count == 1)
    hub.dispatch(_reading_payload(farm_id=farm_id, plot_id=plot_id))

    chunk = await asyncio.wait_for(first, timeout=1.0)
    assert chunk == (
        b"id: 1\nevent: reading\n"
        b'data: {"plot_id": "'
        + str(plot_id).encode()
        + b'", "metric": "soil_moisture", "value": 21.5, "at": "2026-03-01T12:00:00+00:00"}\n\n'
    )
    await gen.aclose()


async def test_stream_plot_events_yields_keepalive_when_idle() -> None:
    hub = PlotEventsHub()

    gen = stream_plot_events(hub, uuid7(), keepalive_interval=0.05)
    chunk = await asyncio.wait_for(anext(gen), timeout=1.0)

    assert chunk == b":keepalive\n\n"
    await gen.aclose()


async def test_stream_plot_events_does_not_subscribe_before_its_body_runs() -> None:
    """An async generator body hasn't executed until the first `anext`, so a
    body that never starts must not leave a subscription behind (#38)."""
    hub = PlotEventsHub()

    gen = stream_plot_events(hub, uuid7(), keepalive_interval=5.0)

    assert hub.subscriber_count == 0
    await gen.aclose()


async def test_stream_plot_events_releases_its_subscription_when_the_client_leaves() -> None:
    hub = PlotEventsHub()
    gen = stream_plot_events(hub, uuid7(), keepalive_interval=0.05)
    await anext(gen)  # one keepalive, so the body is running and subscribed

    assert hub.subscriber_count == 1
    await gen.aclose()
    assert hub.subscriber_count == 0


async def test_stream_plot_events_ends_on_none_sentinel() -> None:
    """The hub pushes `None` to end a stream on connection loss or `stop()`
    (R3-listener-no-reconnect, R3-lifespan-coupling); the generator must end
    instead of yielding it as an event."""
    hub = PlotEventsHub()
    gen = stream_plot_events(hub, uuid7(), keepalive_interval=5.0)
    first = asyncio.ensure_future(anext(gen))
    await _wait_until(lambda: hub.subscriber_count == 1)

    await hub.stop()  # ends every open stream

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(first, timeout=1.0)


# -- PlotEventsHub: connection loss, reconnect, lifespan (E4 T6b) --


async def test_hub_start_never_raises_when_connect_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`start()` must not block API boot on Postgres being reachable
    (R3-lifespan-coupling): it retries in the background instead."""

    async def _always_fails(*, dsn: str) -> _FakeConnection:
        raise OSError("connection refused")

    monkeypatch.setattr(sse_hub.asyncpg, "connect", _always_fails)
    hub = PlotEventsHub(backoff_initial=0.001, backoff_max=0.002)

    await hub.start()  # must return promptly, not raise

    await hub.stop()


async def test_hub_reconnects_after_connect_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    fake = _FakeConnection()

    async def _fake_connect(*, dsn: str) -> _FakeConnection:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OSError("connection refused")
        return fake

    monkeypatch.setattr(sse_hub.asyncpg, "connect", _fake_connect)
    hub = PlotEventsHub(backoff_initial=0.001, backoff_max=0.002)

    await hub.start()
    await _wait_until(lambda: fake.listener_added)

    assert attempts == 3
    await hub.stop()


async def test_hub_ends_client_streams_on_connection_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeConnection()

    async def _fake_connect(*, dsn: str) -> _FakeConnection:
        return fake

    monkeypatch.setattr(sse_hub.asyncpg, "connect", _fake_connect)
    hub = PlotEventsHub(backoff_initial=0.001, backoff_max=0.002)
    await hub.start()
    await _wait_until(lambda: fake.listener_added)

    _client_id, queue = hub.subscribe(uuid7())

    fake.simulate_loss()

    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event is None

    await hub.stop()


async def test_hub_reconnects_after_connection_loss(monkeypatch: pytest.MonkeyPatch) -> None:
    connects = 0
    fakes = [_FakeConnection(), _FakeConnection()]

    async def _fake_connect(*, dsn: str) -> _FakeConnection:
        nonlocal connects
        fake = fakes[connects]
        connects += 1
        return fake

    monkeypatch.setattr(sse_hub.asyncpg, "connect", _fake_connect)
    hub = PlotEventsHub(backoff_initial=0.001, backoff_max=0.002)
    await hub.start()
    await _wait_until(lambda: fakes[0].listener_added)

    fakes[0].simulate_loss()
    await _wait_until(lambda: fakes[1].listener_added)

    assert connects == 2
    await hub.stop()


async def test_hub_stop_ends_every_open_stream() -> None:
    """`stop()` must wake streams blocked in `queue.get()`, not just close
    the `LISTEN` connection (R3-lifespan-coupling)."""
    hub = PlotEventsHub()
    _client_id, queue = hub.subscribe(uuid7())

    await hub.stop()

    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event is None


async def test_hub_reconnects_after_postgres_error_connect_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retry only caught `OSError`, so a `PostgresError` subclass — a
    server still starting up, an auth failure, too many connections — killed
    the task and the hub never listened again: clients kept getting keepalives
    with no events (#38)."""
    attempts = 0
    fake = _FakeConnection()

    async def _fake_connect(*, dsn: str) -> _FakeConnection:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise asyncpg.CannotConnectNowError("the database system is starting up")
        return fake

    monkeypatch.setattr(sse_hub.asyncpg, "connect", _fake_connect)
    hub = PlotEventsHub(backoff_initial=0.001, backoff_max=0.002)

    await hub.start()
    await _wait_until(lambda: fake.listener_added)

    assert attempts == 3
    await hub.stop()


async def test_hub_closes_connection_when_add_listener_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure after a successful connect used to leak the connection and
    kill the retry loop; it must be closed and the retry must continue (#38)."""
    half_open = _FailingListenConnection()
    recovered = _FakeConnection()
    connects = 0

    async def _fake_connect(*, dsn: str) -> _FakeConnection:
        nonlocal connects
        connects += 1
        return half_open if connects == 1 else recovered

    monkeypatch.setattr(sse_hub.asyncpg, "connect", _fake_connect)
    hub = PlotEventsHub(backoff_initial=0.001, backoff_max=0.002)

    await hub.start()
    await _wait_until(lambda: recovered.listener_added)

    assert half_open.closed, "the half-open connection must be closed, not leaked"
    await hub.stop()


async def test_hub_keeps_client_streams_alive_across_a_failed_connect_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing the half-open connection must not fire `_on_terminated`: that
    would end every live stream (clients reconnecting for nothing) and start a
    second reconnect loop beside the one already retrying."""
    half_open = _FailingListenConnection()
    recovered = _FakeConnection()
    connects = 0

    async def _fake_connect(*, dsn: str) -> _FakeConnection:
        nonlocal connects
        connects += 1
        return half_open if connects == 1 else recovered

    monkeypatch.setattr(sse_hub.asyncpg, "connect", _fake_connect)
    hub = PlotEventsHub(backoff_initial=0.001, backoff_max=0.002)
    _client_id, queue = hub.subscribe(uuid7())

    await hub.start()
    await _wait_until(lambda: recovered.listener_added)

    assert queue.qsize() == 0, "a failed connect attempt must not end a live stream"
    assert connects == 2, "a failed connect attempt must not start a second reconnect loop"
    await hub.stop()


async def test_hub_closes_connection_when_stop_cancels_before_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`stop()` cancels the task while `_connect` is between the connect and
    the assignment of `self._connection`, so `stop()` itself can't reach that
    connection (#38)."""
    pending = _PendingListenConnection()

    async def _fake_connect(*, dsn: str) -> _PendingListenConnection:
        return pending

    monkeypatch.setattr(sse_hub.asyncpg, "connect", _fake_connect)
    hub = PlotEventsHub(backoff_initial=0.001, backoff_max=0.002)

    await hub.start()
    await _wait_until(lambda: pending.add_listener_started)
    await hub.stop()

    assert pending.closed
