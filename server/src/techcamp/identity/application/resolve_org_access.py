"""Request-scoped org/role resolution, reused by every module that scopes by org_id.

docs/04-api.md: a resource in another organization must respond 404, not 403,
so it never reveals that it exists — callers map NotAMemberError to 404.
"""

from __future__ import annotations

from uuid import UUID

from techcamp.identity.application.ports import MembershipRepository
from techcamp.identity.domain.errors import NotAMemberError
from techcamp.identity.domain.models import Membership


async def resolve_org_membership(
    *, user_id: UUID, org_id: UUID, memberships: MembershipRepository
) -> Membership:
    membership = await memberships.get(user_id, org_id)
    if membership is None:
        raise NotAMemberError(user_id=user_id, org_id=org_id)
    return membership
