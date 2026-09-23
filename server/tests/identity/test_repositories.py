import pytest
from sqlalchemy.exc import IntegrityError

from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.adapters.repositories import (
    SqlAlchemyMembershipRepository,
    SqlAlchemyUserRepository,
)
from techcamp.identity.domain.models import Role
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio


async def test_user_repository_round_trips_by_id_and_phone(db_session):
    user_id = uuid7()
    db_session.add(AppUserRow(id=user_id, phone="+573001112233"))
    await db_session.commit()

    repo = SqlAlchemyUserRepository(db_session)

    by_id = await repo.get_by_id(user_id)
    by_phone = await repo.get_by_phone("+573001112233")

    assert by_id is not None
    assert by_id.id == user_id
    assert by_phone is not None
    assert by_phone.id == user_id


async def test_user_repository_returns_none_for_unknown_user(db_session):
    repo = SqlAlchemyUserRepository(db_session)

    assert await repo.get_by_id(uuid7()) is None
    assert await repo.get_by_phone("+573000000000") is None


async def test_membership_repository_lists_for_user_and_org(db_session):
    org_id = uuid7()
    user_id = uuid7()
    db_session.add(OrganizationRow(id=org_id, name="Finca A", kind="individual"))
    db_session.add(AppUserRow(id=user_id, phone="+573001112244"))
    await db_session.commit()
    db_session.add(MembershipRow(org_id=org_id, user_id=user_id, role="owner"))
    await db_session.commit()

    repo = SqlAlchemyMembershipRepository(db_session)

    for_user = await repo.list_for_user(user_id)
    for_org = await repo.list_for_org(org_id)
    single = await repo.get(user_id, org_id)

    assert [m.role for m in for_user] == [Role.OWNER]
    assert [m.user_id for m in for_org] == [user_id]
    assert single is not None
    assert single.role == Role.OWNER


async def test_membership_role_is_constrained_by_the_database(db_session):
    org_id = uuid7()
    user_id = uuid7()
    db_session.add(OrganizationRow(id=org_id, name="Finca B", kind="individual"))
    db_session.add(AppUserRow(id=user_id, phone="+573001112255"))
    await db_session.commit()
    db_session.add(MembershipRow(org_id=org_id, user_id=user_id, role="not-a-role"))

    with pytest.raises(IntegrityError):
        await db_session.commit()
