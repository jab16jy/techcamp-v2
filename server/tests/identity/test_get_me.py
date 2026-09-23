import pytest

from techcamp.identity.application.get_me import get_me
from techcamp.identity.domain.errors import UserNotFoundError
from techcamp.identity.domain.models import AppUser, Membership, Role
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio


class FakeUserRepository:
    def __init__(self, users: list[AppUser]) -> None:
        self._by_id = {u.id: u for u in users}
        self._by_phone = {u.phone: u for u in users}

    async def get_by_id(self, user_id):
        return self._by_id.get(user_id)

    async def get_by_phone(self, phone):
        return self._by_phone.get(phone)


class FakeMembershipRepository:
    def __init__(self, memberships: list[Membership]) -> None:
        self._memberships = memberships

    async def list_for_user(self, user_id):
        return [m for m in self._memberships if m.user_id == user_id]

    async def list_for_org(self, org_id):
        return [m for m in self._memberships if m.org_id == org_id]

    async def get(self, user_id, org_id):
        return next(
            (m for m in self._memberships if m.user_id == user_id and m.org_id == org_id), None
        )


async def test_get_me_returns_user_with_their_memberships():
    user_id = uuid7()
    org_id = uuid7()
    user = AppUser(id=user_id, phone="+573000000001")
    membership = Membership(org_id=org_id, user_id=user_id, role=Role.OWNER)

    view = await get_me(
        user_id=user_id,
        users=FakeUserRepository([user]),
        memberships=FakeMembershipRepository([membership]),
    )

    assert view.user == user
    assert view.memberships == [membership]


async def test_get_me_raises_when_user_is_missing():
    with pytest.raises(UserNotFoundError):
        await get_me(
            user_id=uuid7(),
            users=FakeUserRepository([]),
            memberships=FakeMembershipRepository([]),
        )
