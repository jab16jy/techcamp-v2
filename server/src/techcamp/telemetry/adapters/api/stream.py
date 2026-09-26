"""Per-client SSE body for `GET /stream` (docs/04-api.md:180-189, ADR-0015).

`keepalive_interval` is a parameter, not a hardcoded constant, so tests can
inject a short one instead of waiting the real 20 s. A `None` item on the
queue is the hub's "stream ended" sentinel (a lost `LISTEN` connection or
`stop()`, R3-listener-no-reconnect/R3-lifespan-coupling): the generator ends
so the client's `EventSource` reconnects instead of looking alive while
silent.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from uuid import UUID

from techcamp.telemetry.adapters.sse_hub import PlotEventsHub, StreamEvent

KEEPALIVE_INTERVAL_S = 20.0
_KEEPALIVE_LINE = b":keepalive\n\n"


def _format_event(event: StreamEvent) -> bytes:
    return (f"id: {event.id}\nevent: {event.event}\ndata: {json.dumps(event.data)}\n\n").encode()


async def stream_plot_events(
    hub: PlotEventsHub,
    farm_id: UUID,
    *,
    keepalive_interval: float = KEEPALIVE_INTERVAL_S,
) -> AsyncIterator[bytes]:
    """Subscribe here, not in the route: an async generator body only runs
    once the response starts being sent, so a body that never starts (client
    gone, proxy dropped the response) must not leave a subscription behind
    (#38). Nothing unsubscribes except the `finally` below."""
    client_id, queue = hub.subscribe(farm_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=keepalive_interval)
            except TimeoutError:
                yield _KEEPALIVE_LINE
                continue
            if event is None:
                return
            yield _format_event(event)
    finally:
        hub.unsubscribe(client_id)
