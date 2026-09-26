"""`adapters/ingestor.py`'s pure pieces: the size/time batcher and MQTT topic
parsing (docs/06-diseno-detallado.md §1: "acumula hasta 500 mensajes o 1 s").
No broker, no real clock — a fake clock makes the 1s threshold instant."""

from __future__ import annotations

from uuid import UUID

import pytest

from techcamp.telemetry.adapters.ingestor import Batcher, _flush_with_retry, node_id_from_topic

pytestmark = pytest.mark.anyio


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_batcher_flushes_when_max_size_is_reached() -> None:
    clock = _FakeClock()
    batcher: Batcher[int] = Batcher(max_size=3, max_interval=100.0, clock=clock)

    assert batcher.add(1) is None
    assert batcher.add(2) is None
    assert batcher.add(3) == [1, 2, 3]
    assert batcher.add(4) is None  # a fresh batch starts after the drain


def test_batcher_does_not_flush_before_the_time_threshold() -> None:
    clock = _FakeClock()
    batcher: Batcher[int] = Batcher(max_size=500, max_interval=1.0, clock=clock)

    batcher.add(1)
    clock.now = 0.5
    assert batcher.due() is None


def test_batcher_flushes_by_time_even_with_a_partial_batch() -> None:
    clock = _FakeClock()
    batcher: Batcher[int] = Batcher(max_size=500, max_interval=1.0, clock=clock)

    batcher.add(1)
    batcher.add(2)
    clock.now = 1.0
    assert batcher.due() == [1, 2]


def test_batcher_due_is_none_on_an_empty_batcher() -> None:
    clock = _FakeClock()
    batcher: Batcher[int] = Batcher(max_size=500, max_interval=1.0, clock=clock)
    clock.now = 100.0
    assert batcher.due() is None


def test_batcher_resets_the_timer_after_a_time_flush() -> None:
    clock = _FakeClock()
    batcher: Batcher[int] = Batcher(max_size=500, max_interval=1.0, clock=clock)

    batcher.add(1)
    clock.now = 1.0
    batcher.due()
    batcher.add(2)
    clock.now = 1.5  # only 0.5s since the new item was added
    assert batcher.due() is None


def test_node_id_from_topic_parses_the_up_topic() -> None:
    node_id = UUID("00000000-0000-0000-0000-000000000001")
    assert node_id_from_topic(f"tc/v1/{node_id}/up") == node_id


def test_node_id_from_topic_parses_the_status_topic() -> None:
    node_id = UUID("00000000-0000-0000-0000-000000000001")
    assert node_id_from_topic(f"tc/v1/{node_id}/status") == node_id


def test_node_id_from_topic_rejects_a_malformed_topic() -> None:
    assert node_id_from_topic("tc/v1/not-a-uuid/up") is None
    assert node_id_from_topic("garbage") is None
    assert node_id_from_topic("tc/v1/00000000-0000-0000-0000-000000000001") is None


def test_batcher_requeue_drops_the_oldest_items_when_over_capacity() -> None:
    batcher: Batcher[int] = Batcher(max_size=3, max_interval=100.0)
    dropped = batcher.requeue([1, 2, 3, 4])
    assert dropped == 1
    assert batcher.add(5) == [2, 3, 4, 5]


class _FlushOnceThenSucceeds:
    def __init__(self) -> None:
        self.calls: list[list[int]] = []

    async def __call__(self, batch: list[int]) -> None:
        self.calls.append(batch)
        if len(self.calls) == 1:
            raise RuntimeError("db unavailable")


async def test_flush_with_retry_requeues_the_batch_on_failure_for_the_next_flush() -> None:
    batcher: Batcher[int] = Batcher(max_size=3, max_interval=100.0)
    flush = _FlushOnceThenSucceeds()

    await _flush_with_retry([1, 2], batcher, flush, "uplink")

    assert flush.calls == [[1, 2]]
    # the failed batch was put back: it drains again once a third item arrives
    assert batcher.add(3) == [1, 2, 3]
