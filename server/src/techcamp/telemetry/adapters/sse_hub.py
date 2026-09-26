"""`LISTEN plot_events` fan-out for the SSE stream (docs/04-api.md:180-189,
ADR-0015, docs/06-diseno-detallado.md §1).

One `PlotEventsHub` per `api` process, opened for the process lifetime in
`main.py`'s lifespan. It keeps a single raw asyncpg `LISTEN` connection and
fans messages out to bounded per-client queues filtered by `farm_id`; a
client whose queue fills up (too slow to drain) is dropped instead of
blocking the others or the listener.

Connecting happens in the background (R3-lifespan-coupling): `start()`
never blocks or raises even if Postgres is unreachable at boot, so the API
still boots; a lost or failed connection is retried with bounded backoff
(R3-listener-no-reconnect). A connection loss ends every open client stream
(a `None` sentinel on its queue) so `EventSource` clients reconnect instead
of looking alive while silent.
"""

from __future__ import annotations

import asyncio
import contextlib
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
_BACKOFF_INITIAL_S = 1.0
_BACKOFF_MAX_S = 30.0


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
    def __init__(
        self, *, backoff_initial: float = _BACKOFF_INITIAL_S, backoff_max: float = _BACKOFF_MAX_S
    ) -> None:
        self._connection: asyncpg.Connection[Any] | None = None
        self._subscribers: dict[UUID, tuple[UUID, asyncio.Queue[StreamEvent | None]]] = {}
        self._next_id = count(1)
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._reconnect_task: asyncio.Task[None] | None = None
        self._stopping = False

    async def start(self) -> None:
        """Kick off the connect/reconnect loop in the background. Never
        blocks or raises: API boot must not depend on Postgres already being
        reachable (R3-lifespan-coupling)."""
        self._stopping = False
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def stop(self) -> None:
        self._stopping = True
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task
            self._reconnect_task = None
        if self._connection is not None:
            await self._connection.remove_listener("plot_events", self._on_notify)
            await self._connection.close()
            self._connection = None
        self._end_all_subscribers()

    def subscribe(
        self, farm_id: UUID, *, maxsize: int = _DEFAULT_QUEUE_MAXSIZE
    ) -> tuple[UUID, asyncio.Queue[StreamEvent | None]]:
        client_id = uuid4()
        queue: asyncio.Queue[StreamEvent | None] = asyncio.Queue(maxsize=maxsize)
        self._subscribers[client_id] = (farm_id, queue)
        return client_id, queue

    def unsubscribe(self, client_id: UUID) -> None:
        self._subscribers.pop(client_id, None)

    def is_subscribed(self, client_id: UUID) -> bool:
        return client_id in self._subscribers

    @property
    def is_listening(self) -> bool:
        """Whether `LISTEN plot_events` is registered on a live connection.
        `start()` connects in the background, so boot says nothing about
        readiness: a `NOTIFY` published before this flips is lost (#38)."""
        return self._connection is not None

    @property
    def subscriber_count(self) -> int:
        """How many client streams are subscribed right now (#38: a client
        that never starts its body must not hold a subscription)."""
        return len(self._subscribers)

    async def _reconnect_loop(self) -> None:
        """Connect, retrying with bounded exponential backoff until it
        succeeds or `stop()` cancels this task."""
        delay = self._backoff_initial
        while not self._stopping:
            try:
                await self._connect()
                return
            except (OSError, asyncpg.PostgresError):
                # Postgres refuses connections for reasons that are not I/O
                # errors: the server is still starting up, credentials were
                # rotated, too many connections. Catching only `OSError` let
                # those kill the task, and the hub stayed silent forever while
                # clients kept getting keepalives (#38).
                logger.warning("plot_events: LISTEN connect failed, retrying in %.1fs", delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._backoff_max)

    async def _connect(self) -> None:
        connection = await asyncpg.connect(dsn=_dsn())
        try:
            await connection.add_listener("plot_events", self._on_notify)
        except BaseException:
            # `add_listener` failed, or `stop()` cancelled us between the
            # connect and the assignment below: `self._connection` never gets
            # this connection, so nobody else can close it (#38). The
            # termination listener is registered after, so closing it here
            # can't fire `_on_terminated` — that would end every live stream
            # and start a second reconnect loop beside the one already
            # retrying.
            with contextlib.suppress(Exception):
                await connection.close()
            raise
        connection.add_termination_listener(self._on_terminated)
        self._connection = connection

    def _on_terminated(self, _connection: object) -> None:
        """Fires on any connection close, planned or not. `stop()` sets
        `_stopping` before closing, so a deliberate shutdown never
        re-triggers a reconnect here."""
        if self._stopping:
            return
        self._connection = None
        self._end_all_subscribers()
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    def _end_all_subscribers(self) -> None:
        """Push the `None` "stream ended" sentinel to every open client so
        clients blocked in `queue.get()` wake up instead of looking alive
        while silent."""
        for _client_id, (_farm_id, queue) in list(self._subscribers.items()):
            self._push(queue, None)

    def _push(self, queue: asyncio.Queue[StreamEvent | None], item: StreamEvent | None) -> None:
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            # The sentinel must get through even to a full queue: drop the
            # oldest buffered event to make room for it.
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
            queue.put_nowait(item)

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

    def _to_stream_event(self, raw: Any) -> StreamEvent | None:
        try:
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
                # until E7: the notifier never publishes them yet, and any
                # other `type` is unknown and dropped.
                logger.warning("plot_events: unhandled event type %r, dropping", kind)
                return None
            return StreamEvent(
                id=next(self._next_id), event=kind, data=data, farm_id=UUID(str(raw["farm_id"]))
            )
        except (AttributeError, KeyError, ValueError, TypeError):
            # Non-object JSON (`.get`), missing fields (`raw[...]`) or a
            # non-UUID `farm_id` (R3-partial-payload-unhandled): drop rather
            # than raise into the asyncpg listener callback.
            logger.warning("plot_events: malformed payload, dropping", exc_info=True)
            return None
