import pytest

from techcamp.identity.application.resolve_org_access import resolve_org_membership
from techcamp.identity.domain.errors import NotAMemberError
from techcamp.identity.domain.models import Membership, Role
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio


class FakeMembershipRepository:
    def __init__(self, memberships: list[Membership]) -> None:
        self._memberships = memberships

    async def get(self, user_id, org_id):
        return next(
            (m for m in self._memberships if m.user_id == user_id and m.org_id == org_id), None
        )

    async def list_for_user(self, user_id):
        raise NotImplementedError

    async def list_for_org(self, org_id):
        raise NotImplementedError


async def test_resolve_org_membership_returns_the_caller_membership():
    user_id, org_id = uuid7(), uuid7()
    membership = Membership(org_id=org_id, user_id=user_id, role=Role.TECHNICIAN)

    resolved = await resolve_org_membership(
        user_id=user_id, org_id=org_id, memberships=FakeMembershipRepository([membership])
    )

    assert resolved == membership


async def test_resolve_org_membership_raises_when_caller_is_not_a_member():
    user_id, org_id, other_org_id = uuid7(), uuid7(), uuid7()
    membership = Membership(org_id=other_org_id, user_id=user_id, role=Role.VIEWER)

    with pytest.raises(NotAMemberError):
        await resolve_org_membership(
            user_id=user_id, org_id=org_id, memberships=FakeMembershipRepository([membership])
        )
