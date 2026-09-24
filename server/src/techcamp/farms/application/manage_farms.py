"""Farm write use cases and org-scoped access resolution (docs/04-api.md).

`POST /farms` and `PATCH /farms/{farm_id}`. docs/04 is silent on which
membership roles may write; T2 decision (odd/tasks/techcamp-v2-e3-farms.md):
owner and technician write, producer and viewer read only
(farms/domain/models.py `ensure_can_write`).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import UUID

from techcamp.farms.application.ports import FarmRepository
from techcamp.farms.domain.errors import FarmNotFoundError
from techcamp.farms.domain.models import Farm, ensure_can_write
from techcamp.identity.application.ports import MembershipRepository
from techcamp.identity.application.resolve_org_access import resolve_org_membership
from techcamp.identity.domain.models import Role
from techcamp.shared.ids import uuid7


async def resolve_farm_access(
    *, user_id: UUID, farm_id: UUID, farms: FarmRepository, memberships: MembershipRepository
) -> tuple[Farm, Role]:
    """The farm and the caller's role in its org, or `FarmNotFoundError`.

    A farm-id-only route (no `org_id` in the path) can't call
    `resolve_org_membership` directly, so this checks the farm against every
    org the caller belongs to (docs/09-cuellos-de-botella.md#seguridad).
    """
    roles_by_org = {m.org_id: m.role for m in await memberships.list_for_user(user_id)}
    farm = await farms.get_for_orgs(farm_id, list(roles_by_org))
    if farm is None:
        raise FarmNotFoundError(farm_id)
    return farm, roles_by_org[farm.org_id]


async def create_farm(
    *,
    user_id: UUID,
    org_id: UUID,
    name: str,
    municipality_code: str,
    location_wkt: str,
    technician_id: UUID | None,
    farms: FarmRepository,
    memberships: MembershipRepository,
) -> Farm:
    membership = await resolve_org_membership(
        user_id=user_id, org_id=org_id, memberships=memberships
    )
    ensure_can_write(membership.role)
    return await farms.create(
        farm_id=uuid7(),
        org_id=org_id,
        name=name,
        municipality_code=municipality_code,
        location_wkt=location_wkt,
        technician_id=technician_id,
    )


async def update_farm(
    *,
    user_id: UUID,
    farm_id: UUID,
    changes: dict[str, Any],
    farms: FarmRepository,
    memberships: MembershipRepository,
) -> Farm:
    farm, role = await resolve_farm_access(
        user_id=user_id, farm_id=farm_id, farms=farms, memberships=memberships
    )
    ensure_can_write(role)
    merged = replace(farm, **changes)
    return await farms.update(
        farm_id, farm.org_id, name=merged.name, technician_id=merged.technician_id
    )
