"""Farm and plot endpoints (docs/04-api.md:43-49; ADR-0023).

Org isolation per docs/09-cuellos-de-botella.md#seguridad: a resource in
another organization responds 404, never 403.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.adapters.security.token_issuer import issue_token
from techcamp.main import app
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_POLYGON = {
    "type": "Polygon",
    "coordinates": [
        [[-74.10, 10.90], [-74.10, 10.91], [-74.09, 10.91], [-74.09, 10.90], [-74.10, 10.90]]
    ],
}
_POINT = {"type": "Point", "coordinates": [-74.1, 10.9]}


async def _member(
    db_session: AsyncSession, *, role: str, org_name: str = "Finca"
) -> tuple[UUID, UUID, str]:
    org_id, user_id = uuid7(), uuid7()
    db_session.add(OrganizationRow(id=org_id, name=org_name, kind="individual"))
    db_session.add(AppUserRow(id=user_id, phone=f"+5730077{user_id.int % 100000:05d}"))
    await db_session.commit()
    db_session.add(MembershipRow(org_id=org_id, user_id=user_id, role=role))
    await db_session.commit()
    return org_id, user_id, issue_token(str(user_id))


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_owner_creates_a_farm(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)

    response = client.post(
        "/farms",
        json={
            "org_id": str(org_id),
            "name": "Finca A",
            "municipality_code": "47001",
            "location": _POINT,
        },
        headers=_auth(token),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Finca A"
    assert body["location"] == _POINT


async def test_viewer_cannot_create_a_farm(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="viewer")
    client = TestClient(app)

    response = client.post(
        "/farms",
        json={
            "org_id": str(org_id),
            "name": "Finca A",
            "municipality_code": "47001",
            "location": _POINT,
        },
        headers=_auth(token),
    )

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"


async def test_creating_a_farm_in_a_foreign_org_is_404(db_session: AsyncSession) -> None:
    _org_id, _user_id, token = await _member(db_session, role="owner")
    foreign_org_id = uuid7()
    client = TestClient(app)

    response = client.post(
        "/farms",
        json={
            "org_id": str(foreign_org_id),
            "name": "Finca A",
            "municipality_code": "47001",
            "location": _POINT,
        },
        headers=_auth(token),
    )

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


async def test_list_farms_only_returns_the_callers_org(db_session: AsyncSession) -> None:
    org_a, _user_a, token_a = await _member(db_session, role="owner", org_name="Finca A")
    org_b, _user_b, token_b = await _member(db_session, role="owner", org_name="Finca B")
    client = TestClient(app)
    client.post(
        "/farms",
        json={"org_id": str(org_a), "name": "A1", "municipality_code": "47001", "location": _POINT},
        headers=_auth(token_a),
    )
    client.post(
        "/farms",
        json={"org_id": str(org_b), "name": "B1", "municipality_code": "47001", "location": _POINT},
        headers=_auth(token_b),
    )

    response = client.get(f"/farms?org_id={org_a}", headers=_auth(token_a))

    assert response.status_code == 200
    names = [f["name"] for f in response.json()["items"]]
    assert names == ["A1"]


async def test_farm_in_another_org_is_404(db_session: AsyncSession) -> None:
    _org_a, _user_a, token_a = await _member(db_session, role="owner", org_name="Finca A")
    org_b, _user_b, token_b = await _member(db_session, role="owner", org_name="Finca B")
    client = TestClient(app)
    create = client.post(
        "/farms",
        json={"org_id": str(org_b), "name": "B1", "municipality_code": "47001", "location": _POINT},
        headers=_auth(token_b),
    )
    farm_id = create.json()["id"]

    response = client.patch(f"/farms/{farm_id}", json={"name": "renamed"}, headers=_auth(token_a))

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


async def test_owner_patches_a_farm_name(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    create = client.post(
        "/farms",
        json={
            "org_id": str(org_id),
            "name": "Finca A",
            "municipality_code": "47001",
            "location": _POINT,
        },
        headers=_auth(token),
    )
    farm_id = create.json()["id"]

    response = client.patch(
        f"/farms/{farm_id}", json={"name": "Finca A renamed"}, headers=_auth(token)
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Finca A renamed"


async def _create_farm(client: TestClient, org_id: UUID, token: str) -> str:
    response = client.post(
        "/farms",
        json={
            "org_id": str(org_id),
            "name": "Finca A",
            "municipality_code": "47001",
            "location": _POINT,
        },
        headers=_auth(token),
    )
    farm_id: str = response.json()["id"]
    return farm_id


async def test_creating_a_drip_plot_without_efficiency_uses_the_default(
    db_session: AsyncSession,
) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)

    response = client.post(
        f"/farms/{farm_id}/plots",
        json={"name": "Lote 1", "boundary": _POLYGON, "irrigation_system": "drip"},
        headers=_auth(token),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["irrigation_efficiency"] == pytest.approx(0.90)
    assert body["area_ha"] > 0


async def test_rainfed_plot_with_efficiency_is_422(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)

    response = client.post(
        f"/farms/{farm_id}/plots",
        json={
            "name": "Lote 1",
            "boundary": _POLYGON,
            "irrigation_system": "none",
            "irrigation_efficiency": 0.9,
        },
        headers=_auth(token),
    )

    assert response.status_code == 422


async def test_out_of_range_efficiency_is_422_before_the_database(
    db_session: AsyncSession,
) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)

    response = client.post(
        f"/farms/{farm_id}/plots",
        json={
            "name": "Lote 1",
            "boundary": _POLYGON,
            "irrigation_system": "drip",
            "irrigation_efficiency": 1.5,
            "system_flow_lph": 100,
        },
        headers=_auth(token),
    )

    assert response.status_code == 422


async def test_plots_of_a_foreign_farm_are_404(db_session: AsyncSession) -> None:
    _org_a, _user_a, token_a = await _member(db_session, role="owner", org_name="Finca A")
    org_b, user_b, _token_b = await _member(db_session, role="owner", org_name="Finca B")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_b, issue_token(str(user_b)))

    response = client.get(f"/farms/{farm_id}/plots", headers=_auth(token_a))

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


async def test_list_and_patch_plots(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    created = client.post(
        f"/farms/{farm_id}/plots",
        json={"name": "Lote 1", "boundary": _POLYGON, "irrigation_system": "none"},
        headers=_auth(token),
    )
    plot_id = created.json()["id"]

    listed = client.get(f"/farms/{farm_id}/plots", headers=_auth(token))
    assert listed.status_code == 200
    assert [p["id"] for p in listed.json()] == [plot_id]

    patched = client.patch(
        f"/plots/{plot_id}", json={"name": "Lote 1 renamed"}, headers=_auth(token)
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Lote 1 renamed"


async def test_patching_a_plot_to_rainfed_with_leftover_flow_is_422(
    db_session: AsyncSession,
) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    created = client.post(
        f"/farms/{farm_id}/plots",
        json={
            "name": "Lote 1",
            "boundary": _POLYGON,
            "irrigation_system": "drip",
            "irrigation_efficiency": 0.9,
            "system_flow_lph": 250,
        },
        headers=_auth(token),
    )
    plot_id = created.json()["id"]

    response = client.patch(
        f"/plots/{plot_id}", json={"irrigation_system": "none"}, headers=_auth(token)
    )

    assert response.status_code == 422
