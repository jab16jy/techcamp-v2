"""End-to-end acceptance test: request OTP -> code in log -> verify -> JWT -> GET /me."""

import pytest
from fastapi.testclient import TestClient

from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.main import app
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio


async def test_full_sign_in_flow_returns_the_user_and_their_memberships(
    db_session, capsys: pytest.CaptureFixture[str]
) -> None:
    org_id, user_id = uuid7(), uuid7()
    phone = "+573006660001"
    db_session.add(OrganizationRow(id=org_id, name="Finca D", kind="individual"))
    db_session.add(AppUserRow(id=user_id, phone=phone, full_name="Luz"))
    await db_session.commit()
    db_session.add(MembershipRow(org_id=org_id, user_id=user_id, role="producer"))
    await db_session.commit()
    client = TestClient(app, base_url="http://testserver/api/v1")

    otp_response = client.post("/dev/auth/otp", json={"phone": phone})
    assert otp_response.status_code == 204
    printed = capsys.readouterr().out.strip()
    code = printed.rsplit(":", 1)[-1].strip()
    assert phone in printed

    verify_response = client.post("/dev/auth/otp/verify", json={"phone": phone, "code": code})
    assert verify_response.status_code == 200
    token = verify_response.json()["access_token"]

    me_response = client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert me_response.status_code == 200
    body = me_response.json()
    assert body["full_name"] == "Luz"
    assert body["memberships"] == [{"org_id": str(org_id), "role": "producer"}]
