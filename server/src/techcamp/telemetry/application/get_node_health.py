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
from techcamp.telemetry.domain.models import NodeStatus

_COMPLETENESS_WINDOW = timedelta(hours=24)
_MAX_NODES_PER_PLOT = 500


@dataclass(frozen=True, slots=True)
class NodeHealth:
    last_seen_at: datetime | None
    battery_v: float | None
    rssi: int | None
    completeness_24h: float | None
    """docs/11-metricas.md: `lecturas recibidas / esperadas por nodo y día`,
    capped at 1.0. `None` when `interval_s` can't derive an expectation."""


@dataclass(frozen=True, slots=True)
class PlotNodeHealth:
    node_id: UUID
    status: NodeStatus
    last_seen_at: datetime | None
    completeness_24h: float | None
    """docs/04 §Estado: { node_id, status, last_seen_at, completeness_24h } (D-T0.5)."""


async def compute_node_completeness(
    *,
    node_id: UUID,
    org_id: UUID,
    interval_s: int,
    nodes: NodeRepository,
    now: datetime,
) -> float | None:
    """Readings received / expected over the last 24 h, capped at 1.0."""
    if interval_s <= 0:
        return None
    since = now - _COMPLETENESS_WINDOW
    received = await nodes.count_readings_since(node_id, org_id, since)
    expected = _COMPLETENESS_WINDOW.total_seconds() / interval_s
    return min(received / expected, 1.0)


async def get_node_health(
    *,
    user_id: UUID,
    node_id: UUID,
    nodes: NodeRepository,
    memberships: MembershipRepository,
    now: datetime | None = None,
) -> NodeHealth:
    node, _role = await resolve_node_access(
        user_id=user_id, node_id=node_id, nodes=nodes, memberships=memberships
    )
    assert node.org_id is not None  # resolve_node_access only returns claimed nodes
    reference_now = now or datetime.now(UTC)
    completeness = await compute_node_completeness(
        node_id=node.id,
        org_id=node.org_id,
        interval_s=node.interval_s,
        nodes=nodes,
        now=reference_now,
    )
    return NodeHealth(
        last_seen_at=node.last_seen_at, battery_v=None, rssi=None, completeness_24h=completeness
    )


async def get_plot_nodes_health(
    *,
    plot_id: UUID,
    org_id: UUID,
    nodes: NodeRepository,
    now: datetime | None = None,
) -> list[PlotNodeHealth]:
    """Health of every claimed node of a plot (docs/04 §Estado; D-T0.5).

    Org-scoped through NodeRepository.list_for_org.
    """
    reference_now = now or datetime.now(UTC)
    node_list = await nodes.list_for_org(org_id, plot_id=plot_id, limit=_MAX_NODES_PER_PLOT)
    result: list[PlotNodeHealth] = []
    for node in node_list:
        completeness = await compute_node_completeness(
            node_id=node.id,
            org_id=org_id,
            interval_s=node.interval_s,
            nodes=nodes,
            now=reference_now,
        )
        result.append(
            PlotNodeHealth(
                node_id=node.id,
                status=node.status,
                last_seen_at=node.last_seen_at,
                completeness_24h=completeness,
            )
        )
    return result
