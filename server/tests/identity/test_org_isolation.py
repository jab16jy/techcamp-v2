"""Org isolation (docs/09-cuellos-de-botella.md#seguridad): a user of org A must
not be able to see org B's resources — not even to learn that org B exists.
"""

import pytest
from fastapi.testclient import TestClient

from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.adapters.security.token_issuer import issue_token
from techcamp.main import app
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio


async def test_user_of_org_a_gets_404_for_org_b_members(db_session) -> None:
    org_a, org_b = uuid7(), uuid7()
    user_a, user_b = uuid7(), uuid7()
    db_session.add(OrganizationRow(id=org_a, name="Finca A", kind="individual"))
    db_session.add(OrganizationRow(id=org_b, name="Finca B", kind="individual"))
    db_session.add(AppUserRow(id=user_a, phone="+573007770001"))
    db_session.add(AppUserRow(id=user_b, phone="+573007770002"))
    await db_session.commit()
    db_session.add(MembershipRow(org_id=org_a, user_id=user_a, role="owner"))
    db_session.add(MembershipRow(org_id=org_b, user_id=user_b, role="owner"))
    await db_session.commit()
    client = TestClient(app)
    token_a = issue_token(str(user_a))

    own_org_response = client.get(
        f"/organizations/{org_a}/members", headers={"Authorization": f"Bearer {token_a}"}
    )
    other_org_response = client.get(
        f"/organizations/{org_b}/members", headers={"Authorization": f"Bearer {token_a}"}
    )

    assert own_org_response.status_code == 200
    assert [m["user_id"] for m in own_org_response.json()] == [str(user_a)]
    assert other_org_response.status_code == 404
    assert other_org_response.headers["content-type"] == "application/problem+json"
