"""`GET /me` and org-scoped identity endpoints (docs/04-api.md)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from techcamp.identity.adapters.api.deps import CurrentUserId, MembershipRepoDep, UserRepoDep
from techcamp.identity.application.get_me import get_me
from techcamp.identity.application.resolve_org_access import resolve_org_membership
from techcamp.identity.domain.errors import NotAMemberError, UserNotFoundError
from techcamp.shared.errors import ProblemError

router = APIRouter(tags=["identity"])


class MembershipView(BaseModel):
    org_id: UUID
    role: str


class MeResponse(BaseModel):
    id: UUID
    phone: str
    email: str | None
    full_name: str | None
    memberships: list[MembershipView]


@router.get("/me", response_model=MeResponse)
async def read_me(
    user_id: CurrentUserId, users: UserRepoDep, memberships: MembershipRepoDep
) -> MeResponse:
    try:
        view = await get_me(user_id=user_id, users=users, memberships=memberships)
    except UserNotFoundError as exc:
        raise ProblemError(status=404, title="User not found") from exc
    return MeResponse(
        id=view.user.id,
        phone=view.user.phone,
        email=view.user.email,
        full_name=view.user.full_name,
        memberships=[MembershipView(org_id=m.org_id, role=m.role) for m in view.memberships],
    )


class MemberView(BaseModel):
    org_id: UUID
    user_id: UUID
    role: str


@router.get("/organizations/{org_id}/members", response_model=list[MemberView])
async def list_org_members(
    org_id: UUID, user_id: CurrentUserId, memberships: MembershipRepoDep
) -> list[MemberView]:
    try:
        await resolve_org_membership(user_id=user_id, org_id=org_id, memberships=memberships)
    except NotAMemberError as exc:
        # docs/04-api.md: a resource in another organization is 404, never 403.
        raise ProblemError(status=404, title="Organization not found") from exc
    org_members = await memberships.list_for_org(org_id)
    return [MemberView(org_id=m.org_id, user_id=m.user_id, role=m.role) for m in org_members]
