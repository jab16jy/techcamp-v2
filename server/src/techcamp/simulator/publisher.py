"""MQTT publishing for the node simulator, the same topics/QoS/retain a real
node uses (docs/04-api.md#contrato-mqtt, ADR-0004)."""

from __future__ import annotations

import json
from typing import Any, Protocol
from uuid import UUID


class MqttClient(Protocol):
    """The slice of `aiomqtt.Client` this module needs — a port so tests can
    inject a double instead of a real broker connection (ADR-0002: external
    I/O needs a test double; no Mosquitto is assumed running for unit tests)."""

    async def publish(
        self, topic: str, payload: bytes, qos: int = 0, retain: bool = False
    ) -> object: ...


class UplinkPublisher(Protocol):
    async def publish_uplink(self, node_id: UUID, payload: dict[str, Any]) -> None: ...

    async def publish_status(self, node_id: UUID, status: str) -> None: ...


class MqttUplinkPublisher:
    """Real implementation over an already-connected `aiomqtt.Client`
    (docs/04-api.md's MQTT contract table: `up` is QoS 1, not retained;
    `status` is QoS 1, retained, doubling as the Last Will)."""

    def __init__(self, client: MqttClient) -> None:
        self._client = client

    async def publish_uplink(self, node_id: UUID, payload: dict[str, Any]) -> None:
        await self._client.publish(
            f"tc/v1/{node_id}/up", json.dumps(payload).encode(), qos=1, retain=False
        )

    async def publish_status(self, node_id: UUID, status: str) -> None:
        await self._client.publish(f"tc/v1/{node_id}/status", status.encode(), qos=1, retain=True)
