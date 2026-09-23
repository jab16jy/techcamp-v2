import pytest
from fastapi.testclient import TestClient

from techcamp.identity.adapters.orm import AppUserRow
from techcamp.main import app
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio


def _read_code(capsys: pytest.CaptureFixture[str]) -> str:
    out = capsys.readouterr().out.strip()
    return out.rsplit(":", 1)[-1].strip()


def test_otp_request_prints_a_code_and_returns_204(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = TestClient(app)

    response = client.post("/dev/auth/otp", json={"phone": "+573009990001"})

    assert response.status_code == 204
    assert "+573009990001" in capsys.readouterr().out


async def test_otp_verify_returns_a_jwt_for_an_existing_user(
    db_session, capsys: pytest.CaptureFixture[str]
) -> None:
    db_session.add(AppUserRow(id=uuid7(), phone="+573009990002"))
    await db_session.commit()
    client = TestClient(app)
    client.post("/dev/auth/otp", json={"phone": "+573009990002"})
    code = _read_code(capsys)

    response = client.post("/dev/auth/otp/verify", json={"phone": "+573009990002", "code": code})

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_otp_verify_rejects_a_wrong_code(capsys: pytest.CaptureFixture[str]) -> None:
    client = TestClient(app)
    client.post("/dev/auth/otp", json={"phone": "+573009990003"})
    capsys.readouterr()

    response = client.post(
        "/dev/auth/otp/verify", json={"phone": "+573009990003", "code": "000000"}
    )

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"


def test_otp_verify_rejects_an_unknown_user(capsys: pytest.CaptureFixture[str]) -> None:
    client = TestClient(app)
    client.post("/dev/auth/otp", json={"phone": "+573009990004"})
    code = _read_code(capsys)

    response = client.post("/dev/auth/otp/verify", json={"phone": "+573009990004", "code": code})

    assert response.status_code == 401
