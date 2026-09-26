"""Node, sensor and calibration domain entities (docs/03-modelo-datos.md:136-167; ADR-0004).

Pure data, no I/O. `reading` has no domain model yet: T1 builds no use case
that reads or writes it directly (ingest insert is T4, `raw|hour|day` queries
are T5) — only the hypertable schema and repositories T3/T4 will obviously
need.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class NodeTransport(StrEnum):
    WIFI = "wifi"
    CELLULAR = "cellular"
    LORAWAN = "lorawan"


class NodeStatus(StrEnum):
    PROVISIONED = "provisioned"
    ONLINE = "online"
    OFFLINE = "offline"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class Node:
    id: UUID
    org_id: UUID | None
    """Set once claimed; null while `provisioned` and unclaimed."""
    plot_id: UUID | None
    """Set once claimed; null while `provisioned` and unclaimed."""
    transport: NodeTransport
    dev_eui: str | None
    """Unique when present; LoRaWAN nodes have one, wifi/cellular nodes don't."""
    claim_code: str
    credential_hash: str
    firmware: str | None
    """Reported by the node itself (uplink `fw` field, docs/04-api.md); unset
    before its first uplink."""
    interval_s: int
    claimed_at: datetime | None
    """Set once the app claims the node onto a plot; null while `provisioned`."""
    last_seen_at: datetime | None
    status: NodeStatus


@dataclass(frozen=True, slots=True)
class Sensor:
    id: int
    node_id: UUID
    channel_key: str
    """Key in the uplink payload, e.g. `sm_10` (docs/03-modelo-datos.md:153)."""
    metric: str
    depth_cm: int | None
    """Null for sensors with no installation depth (e.g. temperature)."""
    unit: str


class CalibrationMethod(StrEnum):
    LINEAR = "linear"
    TWO_POINT = "two_point"
    POLYNOMIAL = "polynomial"


class CalibrationKind(StrEnum):
    LAB = "lab"
    FIELD = "field"


@dataclass(frozen=True, slots=True)
class Calibration:
    id: UUID
    sensor_id: int
    version: int
    method: CalibrationMethod
    kind: CalibrationKind
    params: dict[str, Any]
    """Method-specific coefficients (docs/03-modelo-datos.md:463-467), e.g.
    `{"scale": a, "offset": b}` for `linear`."""
    rmse_pct: float | None
    """Calibration error, when measured (docs/03-modelo-datos.md:165)."""
    valid_from: datetime
