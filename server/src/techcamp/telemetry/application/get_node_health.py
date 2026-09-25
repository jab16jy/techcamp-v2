"""`GET /nodes/{node_id}/health` (docs/04-api.md:84).

`battery_v`/`rssi` are node diagnostics carried in the uplink's `m` channels
(docs/04-api.md:210, keys `bat`/`rssi`), but nothing before T4 (the ingestor)
turns them into `sensor`/`reading` rows — no calibrated channel exists for
them at the T1 schema level. Flagged gap (task instructions allow it):
returned as `null` here rather than inventing a source or building part of
T5's readings query early.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from techcamp.identity.application.ports import MembershipRepository
from techcamp.telemetry.application.manage_nodes import resolve_node_access
from techcamp.telemetry.application.ports import NodeRepository

_COMPLETENESS_WINDOW = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class NodeHealth:
    last_seen_at: datetime | None
    battery_v: float | None
    rssi: int | None
    completeness_24h: float | None
    """docs/11-metricas.md: `lecturas recibidas / esperadas por nodo y día`,
    capped at 1.0. `None` when `interval_s` can't derive an expectation."""


async def get_node_health(
    *, user_id: UUID, node_id: UUID, nodes: NodeRepository, memberships: MembershipRepository
) -> NodeHealth:
    node, _role = await resolve_node_access(
        user_id=user_id, node_id=node_id, nodes=nodes, memberships=memberships
    )
    assert node.org_id is not None  # resolve_node_access only returns claimed nodes
    completeness = None
    if node.interval_s > 0:
        since = datetime.now(UTC) - _COMPLETENESS_WINDOW
        received = await nodes.count_readings_since(node.id, node.org_id, since)
        expected = _COMPLETENESS_WINDOW.total_seconds() / node.interval_s
        completeness = min(received / expected, 1.0)
    return NodeHealth(
        last_seen_at=node.last_seen_at, battery_v=None, rssi=None, completeness_24h=completeness
    )
