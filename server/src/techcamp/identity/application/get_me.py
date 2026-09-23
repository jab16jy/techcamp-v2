"""`GET /me` use case: the caller's user record and their memberships."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from techcamp.identity.application.ports import MembershipRepository, UserRepository
from techcamp.identity.domain.errors import UserNotFoundError
from techcamp.identity.domain.models import AppUser, Membership


@dataclass(frozen=True, slots=True)
class MeView:
    user: AppUser
    memberships: list[Membership]


async def get_me(
    *, user_id: UUID, users: UserRepository, memberships: MembershipRepository
) -> MeView:
    user = await users.get_by_id(user_id)
    if user is None:
        raise UserNotFoundError(user_id)
    return MeView(user=user, memberships=await memberships.list_for_user(user_id))
