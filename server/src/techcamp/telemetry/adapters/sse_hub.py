"""`LISTEN plot_events` fan-out for the SSE stream (docs/04-api.md:180-189,
ADR-0015, docs/06-diseno-detallado.md §1).

One `PlotEventsHub` per `api` process, opened for the process lifetime in
`main.py`'s lifespan. It keeps a single raw asyncpg `LISTEN` connection and
fans messages out to bounded per-client queues filtered by `farm_id`; a
client whose queue fills up (too slow to drain) is dropped instead of
blocking the others or the listener.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from itertools import count
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from techcamp.shared.config import database_url

logger = logging.getLogger(__name__)

# ponytail: fixed bound per client, not a configurable/adaptive size — this
# project's scale (docs/02-estimaciones.md) never needs tuning it; raise if
# a legitimate burst starts tripping it.
_DEFAULT_QUEUE_MAXSIZE = 100


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """One SSE event, already reduced to the fields docs/04-api.md documents
    for its type (`org_id`, internal-only, is dropped)."""

    id: int
    event: str
    data: dict[str, Any]
    farm_id: UUID


def _dsn() -> str:
    return database_url().replace("postgresql+asyncpg://", "postgresql://")


class PlotEventsHub:
    def __init__(self) -> None:
        self._connection: asyncpg.Connection[Any] | None = None
        self._subscribers: dict[UUID, tuple[UUID, asyncio.Queue[StreamEvent]]] = {}
        self._next_id = count(1)

    async def start(self) -> None:
        self._connection = await asyncpg.connect(dsn=_dsn())
        await self._connection.add_listener("plot_events", self._on_notify)

    async def stop(self) -> None:
        if self._connection is not None:
            await self._connection.remove_listener("plot_events", self._on_notify)
            await self._connection.close()
            self._connection = None

    def subscribe(
        self, farm_id: UUID, *, maxsize: int = _DEFAULT_QUEUE_MAXSIZE
    ) -> tuple[UUID, asyncio.Queue[StreamEvent]]:
        client_id = uuid4()
        queue: asyncio.Queue[StreamEvent] = asyncio.Queue(maxsize=maxsize)
        self._subscribers[client_id] = (farm_id, queue)
        return client_id, queue

    def unsubscribe(self, client_id: UUID) -> None:
        self._subscribers.pop(client_id, None)

    def is_subscribed(self, client_id: UUID) -> bool:
        return client_id in self._subscribers

    def _on_notify(self, _connection: object, _pid: int, _channel: str, payload: str) -> None:
        self.dispatch(payload)

    def dispatch(self, payload: str) -> None:
        """The `NOTIFY plot_events` handler; also called directly by tests
        with a fake payload, without a live `LISTEN` connection."""
        try:
            raw = json.loads(payload)
        except ValueError:
            logger.warning("plot_events: malformed payload, dropping")
            return
        event = self._to_stream_event(raw)
        if event is None:
            return
        for client_id, (farm_id, queue) in list(self._subscribers.items()):
            if farm_id != event.farm_id:
                continue
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("plot_events: dropping slow client %s", client_id)
                self.unsubscribe(client_id)

    def _to_stream_event(self, raw: dict[str, Any]) -> StreamEvent | None:
        kind = raw.get("type")
        if kind == "reading":
            data = {
                "plot_id": raw["plot_id"],
                "metric": raw["metric"],
                "value": raw["value"],
                "at": raw["at"],
            }
        elif kind == "node.status":
            data = {
                "node_id": raw["node_id"],
                "status": raw["status"],
                "at": raw["at"],
            }
        else:
            # `alert.opened`/`alert.updated` (docs/04-api.md) are a no-op
            # until E7: the notifier never publishes them yet, and any other
            # `type` is unknown and dropped.
            logger.warning("plot_events: unhandled event type %r, dropping", kind)
            return None
        return StreamEvent(
            id=next(self._next_id), event=kind, data=data, farm_id=UUID(str(raw["farm_id"]))
        )
