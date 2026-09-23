import pytest
from fastapi.testclient import TestClient

from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.adapters.security.token_issuer import issue_token
from techcamp.main import app
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio


async def test_read_me_returns_user_and_memberships(db_session) -> None:
    user_id, org_id = uuid7(), uuid7()
    db_session.add(OrganizationRow(id=org_id, name="Finca C", kind="individual"))
    db_session.add(AppUserRow(id=user_id, phone="+573008880001", full_name="Ana"))
    await db_session.commit()
    db_session.add(MembershipRow(org_id=org_id, user_id=user_id, role="owner"))
    await db_session.commit()
    client = TestClient(app)

    response = client.get("/me", headers={"Authorization": f"Bearer {issue_token(str(user_id))}"})

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(user_id)
    assert body["full_name"] == "Ana"
    assert body["memberships"] == [{"org_id": str(org_id), "role": "owner"}]


def test_read_me_requires_a_bearer_token() -> None:
    client = TestClient(app)

    response = client.get("/me")

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"


def test_read_me_rejects_an_invalid_token() -> None:
    client = TestClient(app)

    response = client.get("/me", headers={"Authorization": "Bearer not-a-jwt"})

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"


def test_read_me_returns_404_when_the_token_subject_has_no_user_record() -> None:
    client = TestClient(app)

    response = client.get("/me", headers={"Authorization": f"Bearer {issue_token(str(uuid7()))}"})

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
