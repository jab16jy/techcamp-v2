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

import pytest

from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.api.stream import stream_plot_events
from techcamp.telemetry.adapters.sse_hub import PlotEventsHub

pytestmark = pytest.mark.anyio


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
    client_id, queue = hub.subscribe(farm_id)
    hub.dispatch(_reading_payload(farm_id=farm_id, plot_id=plot_id))

    gen = stream_plot_events(hub, client_id, queue, keepalive_interval=5.0)
    chunk = await anext(gen)

    assert chunk == (
        b"id: 1\nevent: reading\n"
        b'data: {"plot_id": "'
        + str(plot_id).encode()
        + b'", "metric": "soil_moisture", "value": 21.5, "at": "2026-03-01T12:00:00+00:00"}\n\n'
    )
    hub.unsubscribe(client_id)
    await gen.aclose()


async def test_stream_plot_events_yields_keepalive_when_idle() -> None:
    hub = PlotEventsHub()
    client_id, queue = hub.subscribe(uuid7())

    gen = stream_plot_events(hub, client_id, queue, keepalive_interval=0.05)
    chunk = await asyncio.wait_for(anext(gen), timeout=1.0)

    assert chunk == b":keepalive\n\n"
    hub.unsubscribe(client_id)
    await gen.aclose()


async def test_stream_plot_events_stops_once_unsubscribed() -> None:
    hub = PlotEventsHub()
    client_id, queue = hub.subscribe(uuid7())
    hub.unsubscribe(client_id)

    gen = stream_plot_events(hub, client_id, queue, keepalive_interval=5.0)

    with pytest.raises(StopAsyncIteration):
        await anext(gen)
