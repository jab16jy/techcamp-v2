"""Repository ports the application layer depends on (never on adapters).

Same reasoning as farms/application/ports.py: T3's write use cases need this
abstraction for ADR-0002's layering rule, not for a test double — writes go
through the same real-Postgres fixtures as reads (AGENTS.md testing policy).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from techcamp.telemetry.domain.models import (
    Calibration,
    CalibrationKind,
    CalibrationMethod,
    Node,
    NodeSeenUpdate,
    NodeStatus,
    NodeStatusEvent,
    ReadingEvent,
    ReadingRecord,
    Sensor,
)


class NodeRepository(Protocol):
    async def get(self, node_id: UUID, org_id: UUID) -> Node | None: ...

    async def get_for_orgs(self, node_id: UUID, org_ids: Sequence[UUID]) -> Node | None: ...

    async def get_by_claim_code(self, claim_code: str) -> Node | None: ...

    async def get_by_id(self, node_id: UUID) -> Node | None:
        """Any node regardless of org/claim state — the ingestor knows only
        `node_id` from the MQTT topic, before it knows which org (or whether
        one) claims it. Same before-org-is-known reasoning as
        `get_by_claim_code`."""
        ...

    async def list_for_org(
        self,
        org_id: UUID,
        *,
        plot_id: UUID | None = None,
        status: NodeStatus | None = None,
        limit: int = 50,
        cursor: UUID | None = None,
    ) -> list[Node]: ...

    async def claim(
        self,
        node_id: UUID,
        *,
        org_id: UUID,
        plot_id: UUID,
        credential_hash: str,
        claimed_at: datetime,
    ) -> Node | None: ...

    async def update(
        self, node_id: UUID, org_id: UUID, *, plot_id: UUID | None, status: NodeStatus
    ) -> Node: ...

    async def set_credential_hash(
        self, node_id: UUID, org_id: UUID, credential_hash: str
    ) -> None: ...

    async def count_readings_since(self, node_id: UUID, org_id: UUID, since: datetime) -> int: ...

    async def mark_seen_batch(self, updates: Sequence[NodeSeenUpdate]) -> None:
        """Sets `last_seen_at`/`status` for every node touched by one ingest
        flush (docs/06-diseno-detallado.md §1), one commit for the batch."""
        ...


class SensorRepository(Protocol):
    async def list_for_node(self, node_id: UUID, org_id: UUID) -> list[Sensor]: ...

    async def get_org_id(self, sensor_id: int) -> UUID | None:
        """The organization owning `sensor_id`'s node, or `None` when the
        sensor doesn't exist. `sensor` carries no `org_id` column itself
        (docs/03-modelo-datos.md), so this is the join T3's calibration
        access check needs."""
        ...


class CalibrationRepository(Protocol):
    async def get_latest_valid_at(
        self, sensor_id: int, org_id: UUID, at: datetime
    ) -> Calibration | None: ...

    async def next_version(self, sensor_id: int, org_id: UUID) -> int:
        """`MAX(version) + 1` for the sensor, scoped by org (docs/03-modelo-
        datos.md:461: calibration is versioned and never edited in place)."""
        ...

    async def add_version(
        self,
        *,
        org_id: UUID,
        sensor_id: int,
        version: int,
        method: CalibrationMethod,
        kind: CalibrationKind,
        params: dict[str, Any],
        rmse_pct: float | None,
        valid_from: datetime,
    ) -> Calibration | None: ...


class ReadingRepository(Protocol):
    async def insert_batch(self, records: Sequence[ReadingRecord]) -> int:
        """Bulk `INSERT ... ON CONFLICT (sensor_id, time) DO NOTHING`
        (docs/06-diseno-detallado.md §1): duplicate QoS-1 redeliveries are
        idempotent. Returns the number of rows actually inserted."""
        ...


class PlotEventsPort(Protocol):
    async def publish(
        self, *, readings: Sequence[ReadingEvent], statuses: Sequence[NodeStatusEvent]
    ) -> None:
        """`NOTIFY plot_events` with one minimal JSON payload per event
        (docs/04-api.md:180-189, ADR-0015: payload stays small), for T6's
        SSE fan-out. One commit for the whole flush."""
        ...
