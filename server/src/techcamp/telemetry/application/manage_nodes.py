"""Node write use cases and org-scoped access resolution (docs/04-api.md:79-93;
docs/06-diseno-detallado.md §2).

Claiming a node is a write on the target plot, so `claim_node` reuses farms'
plot access resolution and role check (task instruction): the same
organization, the same `owner`/`technician` write roles farms already
enforces for plot writes.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from techcamp.farms.application.manage_plots import resolve_plot_access
from techcamp.farms.application.ports import PlotRepository
from techcamp.identity.application.ports import MembershipRepository
from techcamp.identity.domain.models import Role
from techcamp.shared.credentials import generate_password, hash_password
from techcamp.telemetry.application.ports import NodeRepository
from techcamp.telemetry.domain.errors import (
    ClaimCodeNotFoundError,
    InvalidPlotError,
    NodeAlreadyClaimedError,
    NodeNotFoundError,
)
from techcamp.telemetry.domain.models import Node, ensure_can_write


async def resolve_node_access(
    *, user_id: UUID, node_id: UUID, nodes: NodeRepository, memberships: MembershipRepository
) -> tuple[Node, Role]:
    """The node and the caller's role in its org, or `NodeNotFoundError` (see
    `farms.manage_farms.resolve_farm_access` for why this checks every org
    the caller belongs to: a node-id-only route has no `org_id` in the
    path)."""
    roles_by_org = {m.org_id: m.role for m in await memberships.list_for_user(user_id)}
    node = await nodes.get_for_orgs(node_id, list(roles_by_org))
    if node is None:
        raise NodeNotFoundError(node_id)
    assert node.org_id is not None  # get_for_orgs only matches real orgs, never unclaimed nodes
    return node, roles_by_org[node.org_id]


async def claim_node(
    *,
    user_id: UUID,
    claim_code: str,
    plot_id: UUID,
    nodes: NodeRepository,
    plots: PlotRepository,
    memberships: MembershipRepository,
) -> tuple[Node, str]:
    """`POST /nodes:claim` (docs/06-diseno-detallado.md §2). Returns the
    claimed node and its plaintext MQTT password, generated once here and
    never recoverable afterwards — only its hash is stored."""
    plot, role = await resolve_plot_access(
        user_id=user_id, plot_id=plot_id, plots=plots, memberships=memberships
    )
    ensure_can_write(role)
    node = await nodes.get_by_claim_code(claim_code)
    if node is None:
        raise ClaimCodeNotFoundError(claim_code)
    if node.org_id is not None:
        raise NodeAlreadyClaimedError(claim_code)
    password = generate_password()
    claimed = await nodes.claim(
        node.id,
        org_id=plot.org_id,
        plot_id=plot_id,
        credential_hash=hash_password(password),
        claimed_at=datetime.now(UTC),
    )
    if claimed is None:
        # Lost a race against a concurrent claim of the same node between the
        # check above and this update.
        raise NodeAlreadyClaimedError(claim_code)
    return claimed, password


async def update_node(
    *,
    user_id: UUID,
    node_id: UUID,
    changes: dict[str, Any],
    nodes: NodeRepository,
    plots: PlotRepository,
    memberships: MembershipRepository,
) -> Node:
    """`PATCH /nodes/{node_id}` (docs/04-api.md:82): `{plot_id?, status?}`. A
    new `plot_id` must stay in the node's own organization (task
    instruction), checked the same way farms validates a nested reference
    (`InvalidPlotError`, mirroring `InvalidTechnicianError`)."""
    node, role = await resolve_node_access(
        user_id=user_id, node_id=node_id, nodes=nodes, memberships=memberships
    )
    ensure_can_write(role)
    assert node.org_id is not None  # resolve_node_access only returns claimed nodes
    if "plot_id" in changes:
        plot = await plots.get_for_orgs(changes["plot_id"], [node.org_id])
        if plot is None:
            raise InvalidPlotError(changes["plot_id"])
    merged = replace(node, **changes)
    return await nodes.update(node.id, node.org_id, plot_id=merged.plot_id, status=merged.status)


async def rotate_credentials(
    *, user_id: UUID, node_id: UUID, nodes: NodeRepository, memberships: MembershipRepository
) -> str:
    """`POST /nodes/{node_id}/credentials:rotate` (docs/04-api.md:83):
    replaces the stored hash, invalidating the previous password immediately
    (docs/06-diseno-detallado.md §2)."""
    node, role = await resolve_node_access(
        user_id=user_id, node_id=node_id, nodes=nodes, memberships=memberships
    )
    ensure_can_write(role)
    assert node.org_id is not None  # resolve_node_access only returns claimed nodes
    password = generate_password()
    await nodes.set_credential_hash(node.id, node.org_id, hash_password(password))
    return password
