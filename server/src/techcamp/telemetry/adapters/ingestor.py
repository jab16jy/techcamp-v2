"""The `ingestor` process (docs/05-arquitectura.md:53-83): an MQTT 5 shared
subscription (`$share/ingestors/tc/v1/+/{up,status}`), so several replicas
split the load without double-processing a message. A thin aiomqtt loop
around the pure orchestration in `telemetry/application/ingest_uplinks.py`
(ADR-0002: no SQL or domain logic here, only I/O wiring).

Entrypoint: `python -m techcamp.telemetry.adapters.ingestor` (mirrors how
`api` starts `uvicorn techcamp.main:app`, docs/05: "misma imagen, distinto
comando").
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

import aiomqtt

from techcamp.farms.adapters.repositories import SqlAlchemyPlotRepository
from techcamp.shared.db import async_session_factory
from techcamp.telemetry.adapters.repositories import (
    SqlAlchemyCalibrationRepository,
    SqlAlchemyNodeRepository,
    SqlAlchemyPlotEventsNotifier,
    SqlAlchemyReadingRepository,
    SqlAlchemySensorRepository,
)
from techcamp.telemetry.application.ingest_uplinks import (
    RawStatusMessage,
    RawUplink,
    ingest_status_messages,
    ingest_uplinks,
)

logger = logging.getLogger(__name__)

UPLINK_TOPIC_FILTER = "$share/ingestors/tc/v1/+/up"
STATUS_TOPIC_FILTER = "$share/ingestors/tc/v1/+/status"
RECONNECT_INTERVAL_S = 5.0
POLL_TIMEOUT_S = 1.0


def mqtt_host() -> str:
    return os.environ.get("MQTT_HOST", "localhost")


def mqtt_port() -> int:
    return int(os.environ.get("MQTT_PORT", "1883"))


class Batcher[T]:
    """Accumulates items until `max_size` or `max_interval` seconds pass,
    whichever comes first (docs/06-diseno-detallado.md §1: "acumula hasta
    500 mensajes o 1 s").

    ponytail: a list plus a monotonic clock, not a background timer task —
    the caller already polls once per loop tick (`run()`'s `POLL_TIMEOUT_S`
    wait), so `due()` piggybacks on that instead of its own asyncio task.
    """

    def __init__(
        self,
        *,
        max_size: int = 500,
        max_interval: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_size = max_size
        self._max_interval = max_interval
        self._clock = clock
        self._items: list[T] = []
        self._opened_at: float | None = None

    def add(self, item: T) -> list[T] | None:
        self._items.append(item)
        if self._opened_at is None:
            self._opened_at = self._clock()
        if len(self._items) >= self._max_size:
            return self._drain()
        return None

    def due(self) -> list[T] | None:
        if self._opened_at is not None and self._clock() - self._opened_at >= self._max_interval:
            return self._drain()
        return None

    def _drain(self) -> list[T]:
        items, self._items = self._items, []
        self._opened_at = None
        return items

    def requeue(self, items: list[T]) -> int:
        """Puts a drained batch back after a failed flush, so the next flush
        retries it, without losing whatever arrived since the drain. Bounded
        by `max_size`: on overflow, the oldest items are dropped and their
        count returned so the caller can log it."""
        combined = items + self._items
        dropped = max(0, len(combined) - self._max_size)
        self._items = combined[dropped:]
        if self._opened_at is None and self._items:
            self._opened_at = self._clock()
        return dropped


def node_id_from_topic(topic: str) -> UUID | None:
    """`tc/v1/{node_id}/up` or `tc/v1/{node_id}/status` (docs/04-api.md:192-
    224). `None` for anything else, so the caller can discard it."""
    parts = topic.split("/")
    if len(parts) != 4 or parts[0] != "tc" or parts[1] != "v1":
        return None
    try:
        return UUID(parts[2])
    except ValueError:
        return None


async def _flush_uplinks(batch: list[RawUplink]) -> None:
    if not batch:
        return
    async with async_session_factory() as session:
        stats = await ingest_uplinks(
            batch,
            nodes=SqlAlchemyNodeRepository(session),
            sensors=SqlAlchemySensorRepository(session),
            calibrations=SqlAlchemyCalibrationRepository(session),
            readings=SqlAlchemyReadingRepository(session),
            plots=SqlAlchemyPlotRepository(session),
            events=SqlAlchemyPlotEventsNotifier(session),
        )
    logger.info("ingest: %d uplinks inserted, discarded=%s", stats.inserted, dict(stats.counts))


async def _flush_status(batch: list[RawStatusMessage]) -> None:
    if not batch:
        return
    async with async_session_factory() as session:
        stats = await ingest_status_messages(
            batch,
            nodes=SqlAlchemyNodeRepository(session),
            plots=SqlAlchemyPlotRepository(session),
            events=SqlAlchemyPlotEventsNotifier(session),
        )
    logger.info("ingest: %d status messages, discarded=%s", len(batch), dict(stats.counts))


async def _flush_with_retry[T](
    batch: list[T],
    batcher: Batcher[T],
    flush: Callable[[list[T]], Awaitable[None]],
    label: str,
) -> None:
    """A flush failure (Postgres down, a bad row, ...) must not kill `run()`
    and must not silently drop the already-drained batch (the broker already
    acked these QoS-1 messages): log it and put the items back for the next
    flush attempt."""
    try:
        await flush(batch)
    except Exception:
        logger.exception("ingest: %s flush failed, %d item(s) queued for retry", label, len(batch))
        dropped = batcher.requeue(batch)
        if dropped:
            logger.warning(
                "ingest: %s batcher over capacity, dropped %d oldest item(s)", label, dropped
            )


async def _handle_message(
    message: aiomqtt.Message,
    *,
    uplink_batcher: Batcher[RawUplink],
    status_batcher: Batcher[RawStatusMessage],
) -> None:
    topic = str(message.topic)
    node_id = node_id_from_topic(topic)
    if node_id is None:
        logger.warning("ingest: message on an unrecognized topic %s", topic)
        return
    received_at = datetime.now(UTC)
    payload = message.payload
    payload_bytes = payload if isinstance(payload, bytes) else bytes(str(payload), "utf-8")

    if topic.endswith("/up"):
        flushed = uplink_batcher.add(RawUplink(node_id, payload_bytes, received_at))
        if flushed is not None:
            await _flush_with_retry(flushed, uplink_batcher, _flush_uplinks, "uplink")
    elif topic.endswith("/status"):
        flushed_status = status_batcher.add(RawStatusMessage(node_id, payload_bytes, received_at))
        if flushed_status is not None:
            await _flush_with_retry(flushed_status, status_batcher, _flush_status, "status")


async def run() -> None:
    """The `ingestor` process entrypoint. Reconnects on `MqttError` (network
    blips, broker restarts), and a flush failure (e.g. Postgres down) never
    crashes the process either: `_flush_with_retry` keeps the batch for the
    next attempt instead of propagating."""
    uplink_batcher: Batcher[RawUplink] = Batcher()
    status_batcher: Batcher[RawStatusMessage] = Batcher()

    while True:
        try:
            async with aiomqtt.Client(
                mqtt_host(), mqtt_port(), protocol=aiomqtt.ProtocolVersion.V5
            ) as client:
                await client.subscribe(UPLINK_TOPIC_FILTER, qos=1)
                await client.subscribe(STATUS_TOPIC_FILTER, qos=1)
                messages = client.messages
                while True:
                    try:
                        message = await asyncio.wait_for(
                            messages.__anext__(), timeout=POLL_TIMEOUT_S
                        )
                    except TimeoutError:
                        pass
                    else:
                        await _handle_message(
                            message, uplink_batcher=uplink_batcher, status_batcher=status_batcher
                        )

                    due_uplinks = uplink_batcher.due()
                    if due_uplinks is not None:
                        await _flush_with_retry(
                            due_uplinks, uplink_batcher, _flush_uplinks, "uplink"
                        )
                    due_status = status_batcher.due()
                    if due_status is not None:
                        await _flush_with_retry(due_status, status_batcher, _flush_status, "status")
        except aiomqtt.MqttError:
            logger.warning(
                "ingestor: MQTT connection lost, reconnecting in %ss", RECONNECT_INTERVAL_S
            )
            await asyncio.sleep(RECONNECT_INTERVAL_S)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
