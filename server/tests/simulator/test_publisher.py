"""E4 T8: publishing uplinks/status through the `UplinkPublisher` port, and
the aiomqtt-backed implementation's topic/QoS/retain shape
(docs/04-api.md#contrato-mqtt)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pytest

from techcamp.simulator.publisher import MqttUplinkPublisher

pytestmark = pytest.mark.anyio

_NODE_ID = UUID("00000000-0000-0000-0000-000000000001")


@dataclass
class _RecordingMqttClient:
    """The test double for `aiomqtt.Client`: only `publish` is exercised."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    async def publish(self, topic: str, payload: bytes, qos: int = 0, retain: bool = False) -> None:
        self.calls.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})


async def test_publish_uplink_sends_to_the_up_topic_at_qos_1_without_retain() -> None:
    mqtt_client = _RecordingMqttClient()
    publisher = MqttUplinkPublisher(mqtt_client)  # type: ignore[arg-type]
    payload = {"v": 1, "seq": 1, "ts": 100, "fw": "sim-1.0.0", "m": {"sm_10": 2000.0}}

    await publisher.publish_uplink(_NODE_ID, payload)

    assert mqtt_client.calls == [
        {
            "topic": f"tc/v1/{_NODE_ID}/up",
            "payload": json.dumps(payload).encode(),
            "qos": 1,
            "retain": False,
        }
    ]


async def test_publish_status_sends_to_the_status_topic_at_qos_1_retained() -> None:
    mqtt_client = _RecordingMqttClient()
    publisher = MqttUplinkPublisher(mqtt_client)  # type: ignore[arg-type]

    await publisher.publish_status(_NODE_ID, "online")

    assert mqtt_client.calls == [
        {"topic": f"tc/v1/{_NODE_ID}/status", "payload": b"online", "qos": 1, "retain": True}
    ]
