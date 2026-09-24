"""Farm and plot endpoints (docs/04-api.md:43-49; ADR-0023).

Org isolation per docs/09-cuellos-de-botella.md#seguridad: a resource in
another organization responds 404, never 403.
"""

from __future__ import annotations

import json as jsonlib
from itertools import count
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.api.deps import get_soilgrids_port
from techcamp.farms.adapters.soilgrids import (
    SEMINAR_FIXTURE_RESPONSE,
    SOILGRIDS_PROPERTIES,
    IsricSoilGridsAdapter,
)
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

# A counter, not `uuid7().int % 100000`: the modulo could collide on the
# unique `phone` column between two `uuid7()`s generated close together
# (GitHub issue #21).
_phone_seq = count()


async def _member(
    db_session: AsyncSession, *, role: str, org_name: str = "Finca"
) -> tuple[UUID, UUID, str]:
    org_id, user_id = uuid7(), uuid7()
    db_session.add(OrganizationRow(id=org_id, name=org_name, kind="individual"))
    db_session.add(AppUserRow(id=user_id, phone=f"+5730077{next(_phone_seq):05d}"))
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


async def test_patching_a_plot_to_rainfed_clears_leftover_efficiency_and_flow(
    db_session: AsyncSession,
) -> None:
    """ADR-0023: rainfed has neither efficiency nor flow. A `PATCH` that
    switches to `none` without sending either field clears both instead of
    leaving the old system's values behind (GitHub issue #21 round 4)."""
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

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["irrigation_efficiency"] is None
    assert body["system_flow_lph"] is None


async def test_patching_a_plot_to_rainfed_with_explicit_flow_is_422(
    db_session: AsyncSession,
) -> None:
    """Unlike an omitted field, an explicit non-null value on a field that
    contradicts `none` stays a client error (#21 round 4)."""
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
        f"/plots/{plot_id}",
        json={"irrigation_system": "none", "system_flow_lph": 250},
        headers=_auth(token),
    )

    assert response.status_code == 422


async def test_switching_a_plot_to_irrigated_without_efficiency_uses_the_default(
    db_session: AsyncSession,
) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    created = client.post(
        f"/farms/{farm_id}/plots",
        json={"name": "Lote 1", "boundary": _POLYGON, "irrigation_system": "none"},
        headers=_auth(token),
    )
    plot_id = created.json()["id"]

    response = client.patch(
        f"/plots/{plot_id}", json={"irrigation_system": "drip"}, headers=_auth(token)
    )

    assert response.status_code == 200, response.text
    assert response.json()["irrigation_efficiency"] == pytest.approx(0.90)


async def test_switching_between_irrigated_systems_uses_the_new_default(
    db_session: AsyncSession,
) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    created = client.post(
        f"/farms/{farm_id}/plots",
        json={"name": "Lote 1", "boundary": _POLYGON, "irrigation_system": "drip"},
        headers=_auth(token),
    )
    plot_id = created.json()["id"]

    response = client.patch(
        f"/plots/{plot_id}", json={"irrigation_system": "gravity"}, headers=_auth(token)
    )

    assert response.status_code == 200, response.text
    assert response.json()["irrigation_efficiency"] == pytest.approx(0.60)


async def test_explicit_null_efficiency_on_a_plot_that_stays_irrigated_is_422(
    db_session: AsyncSession,
) -> None:
    """An irrigated plot always needs an efficiency: an explicit `null` is
    invalid input, not "use the default" (the default applies only when the
    field is omitted; GitHub issue #21 round 4)."""
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
        },
        headers=_auth(token),
    )
    plot_id = created.json()["id"]

    response = client.patch(
        f"/plots/{plot_id}", json={"irrigation_efficiency": None}, headers=_auth(token)
    )

    assert response.status_code == 422


async def test_patching_a_plots_boundary_recomputes_area_ha(db_session: AsyncSession) -> None:
    """`area_ha` is a DB-generated column (`ST_Area(boundary::geography)`);
    an `UPDATE` of `boundary` must recompute it, not keep the old value
    (GitHub issue #21 round 4)."""
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    created = client.post(
        f"/farms/{farm_id}/plots",
        json={"name": "Lote 1", "boundary": _POLYGON, "irrigation_system": "none"},
        headers=_auth(token),
    )
    plot_id = created.json()["id"]
    original_area_ha = created.json()["area_ha"]
    larger_polygon = {
        "type": "Polygon",
        "coordinates": [
            [[-74.20, 10.80], [-74.20, 10.90], [-74.10, 10.90], [-74.10, 10.80], [-74.20, 10.80]]
        ],
    }

    response = client.patch(
        f"/plots/{plot_id}", json={"boundary": larger_polygon}, headers=_auth(token)
    )

    assert response.status_code == 200, response.text
    assert response.json()["area_ha"] != pytest.approx(original_area_ha)


@pytest.mark.parametrize(
    ("path_suffix", "payload"),
    [
        ("/farms/{farm_id}", {"name": None}),
    ],
)
async def test_explicit_null_on_farm_name_is_422(
    db_session: AsyncSession, path_suffix: str, payload: dict[str, object]
) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)

    response = client.patch(path_suffix.format(farm_id=farm_id), json=payload, headers=_auth(token))

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.parametrize(
    "payload",
    [
        {"name": None},
        {"boundary": None},
        {"irrigation_system": None},
    ],
)
async def test_explicit_null_on_non_nullable_plot_fields_is_422(
    db_session: AsyncSession, payload: dict[str, object]
) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    created = client.post(
        f"/farms/{farm_id}/plots",
        json={"name": "Lote 1", "boundary": _POLYGON, "irrigation_system": "none"},
        headers=_auth(token),
    )
    plot_id = created.json()["id"]

    response = client.patch(f"/plots/{plot_id}", json=payload, headers=_auth(token))

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"


async def test_explicit_null_on_technician_id_still_clears_it(
    db_session: AsyncSession,
) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)

    response = client.patch(f"/farms/{farm_id}", json={"technician_id": None}, headers=_auth(token))

    assert response.status_code == 200, response.text
    assert response.json()["technician_id"] is None


async def test_foreign_technician_id_is_422_not_500(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)

    response = client.post(
        "/farms",
        json={
            "org_id": str(org_id),
            "name": "Finca A",
            "municipality_code": "47001",
            "location": _POINT,
            "technician_id": str(uuid7()),
        },
        headers=_auth(token),
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"


async def test_technician_id_of_a_producer_is_422(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    _org2, producer_id, _token2 = await _member(db_session, role="producer", org_name="Finca A")
    client = TestClient(app)
    db_session.add(MembershipRow(org_id=org_id, user_id=producer_id, role="producer"))
    await db_session.commit()

    response = client.post(
        "/farms",
        json={
            "org_id": str(org_id),
            "name": "Finca A",
            "municipality_code": "47001",
            "location": _POINT,
            "technician_id": str(producer_id),
        },
        headers=_auth(token),
    )

    assert response.status_code == 422


async def test_technician_id_of_a_technician_is_accepted(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    _org2, tech_id, _token2 = await _member(db_session, role="technician", org_name="Finca A")
    db_session.add(MembershipRow(org_id=org_id, user_id=tech_id, role="technician"))
    await db_session.commit()
    client = TestClient(app)

    response = client.post(
        "/farms",
        json={
            "org_id": str(org_id),
            "name": "Finca A",
            "municipality_code": "47001",
            "location": _POINT,
            "technician_id": str(tech_id),
        },
        headers=_auth(token),
    )

    assert response.status_code == 201, response.text


async def test_stale_technician_does_not_block_an_unrelated_patch(
    db_session: AsyncSession,
) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    _org2, tech_id, _token2 = await _member(db_session, role="technician", org_name="Finca A")
    db_session.add(MembershipRow(org_id=org_id, user_id=tech_id, role="technician"))
    await db_session.commit()
    client = TestClient(app)
    created = client.post(
        "/farms",
        json={
            "org_id": str(org_id),
            "name": "Finca A",
            "municipality_code": "47001",
            "location": _POINT,
            "technician_id": str(tech_id),
        },
        headers=_auth(token),
    )
    assert created.status_code == 201, created.text
    await db_session.execute(
        update(MembershipRow)
        .where(MembershipRow.org_id == org_id, MembershipRow.user_id == tech_id)
        .values(role="producer")
    )
    await db_session.commit()

    response = client.patch(
        f"/farms/{created.json()['id']}", json={"name": "Finca B"}, headers=_auth(token)
    )

    assert response.status_code == 200, response.text


async def test_technician_can_write_a_plot(db_session: AsyncSession) -> None:
    org_id, _user_id, owner_token = await _member(db_session, role="owner")
    _org2, tech_id, tech_token = await _member(db_session, role="technician", org_name="Other")
    db_session.add(MembershipRow(org_id=org_id, user_id=tech_id, role="technician"))
    await db_session.commit()
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, owner_token)

    response = client.post(
        f"/farms/{farm_id}/plots",
        json={"name": "Lote 1", "boundary": _POLYGON, "irrigation_system": "none"},
        headers=_auth(tech_token),
    )

    assert response.status_code == 201, response.text


@pytest.mark.parametrize("role", ["producer", "viewer"])
async def test_non_writer_roles_cannot_patch_a_farm(db_session: AsyncSession, role: str) -> None:
    org_id, _owner_id, owner_token = await _member(db_session, role="owner")
    _user_id, member_id, member_token = await _member(db_session, role=role, org_name="Other")
    db_session.add(MembershipRow(org_id=org_id, user_id=member_id, role=role))
    await db_session.commit()
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, owner_token)

    response = client.patch(
        f"/farms/{farm_id}", json={"name": "renamed"}, headers=_auth(member_token)
    )

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.parametrize("role", ["producer", "viewer"])
async def test_non_writer_roles_cannot_create_a_plot(db_session: AsyncSession, role: str) -> None:
    org_id, _owner_id, owner_token = await _member(db_session, role="owner")
    _user_id, member_id, member_token = await _member(db_session, role=role, org_name="Other")
    db_session.add(MembershipRow(org_id=org_id, user_id=member_id, role=role))
    await db_session.commit()
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, owner_token)

    response = client.post(
        f"/farms/{farm_id}/plots",
        json={"name": "Lote 1", "boundary": _POLYGON, "irrigation_system": "none"},
        headers=_auth(member_token),
    )

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.parametrize("role", ["producer", "viewer"])
async def test_non_writer_roles_cannot_patch_a_plot(db_session: AsyncSession, role: str) -> None:
    org_id, _owner_id, owner_token = await _member(db_session, role="owner")
    _user_id, member_id, member_token = await _member(db_session, role=role, org_name="Other")
    db_session.add(MembershipRow(org_id=org_id, user_id=member_id, role=role))
    await db_session.commit()
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, owner_token)
    created = client.post(
        f"/farms/{farm_id}/plots",
        json={"name": "Lote 1", "boundary": _POLYGON, "irrigation_system": "none"},
        headers=_auth(owner_token),
    )
    plot_id = created.json()["id"]

    response = client.patch(
        f"/plots/{plot_id}", json={"name": "renamed"}, headers=_auth(member_token)
    )

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"


async def test_patching_a_plot_of_a_foreign_org_is_404(db_session: AsyncSession) -> None:
    _org_a, _user_a, token_a = await _member(db_session, role="owner", org_name="Finca A")
    org_b, _user_b, token_b = await _member(db_session, role="owner", org_name="Finca B")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_b, token_b)
    created = client.post(
        f"/farms/{farm_id}/plots",
        json={"name": "Lote 1", "boundary": _POLYGON, "irrigation_system": "none"},
        headers=_auth(token_b),
    )
    plot_id = created.json()["id"]

    response = client.patch(f"/plots/{plot_id}", json={"name": "renamed"}, headers=_auth(token_a))

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


async def test_get_farms_pages_by_cursor(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    for i in range(3):
        client.post(
            "/farms",
            json={
                "org_id": str(org_id),
                "name": f"Finca {i}",
                "municipality_code": "47001",
                "location": _POINT,
            },
            headers=_auth(token),
        )

    first_page = client.get(f"/farms?org_id={org_id}&limit=2", headers=_auth(token))
    assert first_page.status_code == 200
    first_body = first_page.json()
    assert len(first_body["items"]) == 2
    assert first_body["next_cursor"] is not None

    second_page = client.get(
        f"/farms?org_id={org_id}&limit=2&cursor={first_body['next_cursor']}", headers=_auth(token)
    )
    assert second_page.status_code == 200
    second_body = second_page.json()
    assert len(second_body["items"]) == 1
    assert second_body["next_cursor"] is None
    assert {f["name"] for f in first_body["items"] + second_body["items"]} == {
        "Finca 0",
        "Finca 1",
        "Finca 2",
    }


@pytest.mark.parametrize(
    "coordinates",
    [
        [-181.0, 10.9],
        [-74.1, 91.0],
        [float("nan"), 10.9],
        [-74.1, float("inf")],
    ],
)
async def test_point_with_invalid_coordinates_is_422(
    db_session: AsyncSession, coordinates: list[float]
) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    # httpx's `json=` param rejects NaN/Infinity client-side (allow_nan=False);
    # `json.dumps` defaults to allow_nan=True, so send the raw body instead —
    # pydantic-core accepts NaN/Infinity floats by default (allow_inf_nan=True),
    # which is exactly the wire case the server-side validator must reject.
    payload = {
        "org_id": str(org_id),
        "name": "Finca A",
        "municipality_code": "47001",
        "location": {"type": "Point", "coordinates": coordinates},
    }
    headers = {**_auth(token), "Content-Type": "application/json"}

    response = client.post("/farms", content=jsonlib.dumps(payload), headers=headers)

    assert response.status_code == 422


async def test_polygon_with_out_of_range_coordinates_is_422(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    bad_polygon = {
        "type": "Polygon",
        "coordinates": [
            [[-181.0, 10.90], [-74.10, 10.91], [-74.09, 10.91], [-74.09, 10.90], [-181.0, 10.90]]
        ],
    }

    response = client.post(
        f"/farms/{farm_id}/plots",
        json={"name": "Lote 1", "boundary": bad_polygon, "irrigation_system": "none"},
        headers=_auth(token),
    )

    assert response.status_code == 422


async def test_get_crops_returns_the_catalog_with_stages_kc_and_kc_source(
    db_session: AsyncSession,
) -> None:
    """docs/04-api.md:53: `GET /crops` -> `Crop[]` with stages, Kc and kc_source.
    Global reference data (docs/03-modelo-datos.md:115-127): any authenticated
    member can read it, with no `org_id` involved.
    """
    _org_id, _user_id, token = await _member(db_session, role="viewer")
    client = TestClient(app)

    response = client.get("/crops", headers=_auth(token))

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 12
    maize = next(c for c in body if c["code"] == "maize")
    assert maize["kc_source"] == "fao56"
    assert {s["stage"] for s in maize["stages"]} == {"initial", "development", "mid", "late"}
    assert next(s for s in maize["stages"] if s["stage"] == "mid")["kc"] == pytest.approx(1.20)
    yam = next(c for c in body if c["code"] == "yam")
    assert yam["kc_source"] == "none"
    assert yam["stages"] == []


async def test_get_crops_without_a_token_is_401(db_session: AsyncSession) -> None:
    await _member(db_session, role="viewer")
    client = TestClient(app)

    response = client.get("/crops")

    assert response.status_code == 401


async def _create_plot(client: TestClient, farm_id: str, token: str) -> str:
    created = client.post(
        f"/farms/{farm_id}/plots",
        json={"name": "Lote 1", "boundary": _POLYGON, "irrigation_system": "none"},
        headers=_auth(token),
    )
    plot_id: str = created.json()["id"]
    return plot_id


async def test_owner_puts_a_lab_soil_profile(db_session: AsyncSession) -> None:
    """docs/04-api.md:49; lab values pass through and `source` becomes `lab`."""
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    plot_id = await _create_plot(client, farm_id, token)

    response = client.put(
        f"/plots/{plot_id}/soil",
        json={
            "texture": "sandy_loam",
            "ph": 6.5,
            "organic_matter_pct": 3.2,
            "field_capacity_pct": 22.0,
            "wilting_point_pct": 9.0,
            "root_depth_cm": 60,
        },
        headers=_auth(token),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["plot_id"] == plot_id
    assert body["source"] == "lab"
    assert body["field_capacity_pct"] == pytest.approx(22.0)
    assert body["wilting_point_pct"] == pytest.approx(9.0)


async def test_soil_profile_without_water_limits_falls_back_to_fao56_texture(
    db_session: AsyncSession,
) -> None:
    """FAO-56 Table 19 texture fallback (docs/03-modelo-datos.md:445): a
    verified texture class fills θFC/θWP and marks `source = fao56_texture`.
    """
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    plot_id = await _create_plot(client, farm_id, token)

    response = client.put(f"/plots/{plot_id}/soil", json={"texture": "silt"}, headers=_auth(token))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "fao56_texture"
    assert body["field_capacity_pct"] == pytest.approx(32.0)
    assert body["wilting_point_pct"] == pytest.approx(17.0)


async def test_soil_profile_with_an_unrecognized_texture_leaves_water_limits_null(
    db_session: AsyncSession,
) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    plot_id = await _create_plot(client, farm_id, token)

    response = client.put(f"/plots/{plot_id}/soil", json={"texture": "peat"}, headers=_auth(token))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] is None
    assert body["field_capacity_pct"] is None
    assert body["wilting_point_pct"] is None


async def test_putting_a_soil_profile_twice_replaces_it(db_session: AsyncSession) -> None:
    """`PUT` is idempotent full-document write (docs/04-api.md:49), not a merge."""
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    plot_id = await _create_plot(client, farm_id, token)
    first = client.put(
        f"/plots/{plot_id}/soil",
        json={"texture": "silt", "ph": 6.0},
        headers=_auth(token),
    )
    assert first.status_code == 200, first.text

    second = client.put(
        f"/plots/{plot_id}/soil",
        json={"field_capacity_pct": 25.0, "wilting_point_pct": 10.0},
        headers=_auth(token),
    )

    assert second.status_code == 200, second.text
    body = second.json()
    assert body["texture"] is None
    assert body["ph"] is None
    assert body["source"] == "lab"
    assert body["field_capacity_pct"] == pytest.approx(25.0)


async def test_wilting_point_at_or_above_field_capacity_is_422(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    plot_id = await _create_plot(client, farm_id, token)

    response = client.put(
        f"/plots/{plot_id}/soil",
        json={"field_capacity_pct": 20.0, "wilting_point_pct": 20.0},
        headers=_auth(token),
    )

    assert response.status_code == 422


async def test_soil_profile_with_only_one_water_limit_is_422(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    plot_id = await _create_plot(client, farm_id, token)

    response = client.put(
        f"/plots/{plot_id}/soil",
        json={"field_capacity_pct": 20.0},
        headers=_auth(token),
    )

    assert response.status_code == 422


async def test_soil_profile_with_out_of_range_ph_is_422(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    plot_id = await _create_plot(client, farm_id, token)

    response = client.put(f"/plots/{plot_id}/soil", json={"ph": 15.0}, headers=_auth(token))

    assert response.status_code == 422


async def test_viewer_cannot_put_a_soil_profile(db_session: AsyncSession) -> None:
    org_id, _user_id, owner_token = await _member(db_session, role="owner")
    _org2, viewer_id, _viewer_token = await _member(db_session, role="viewer", org_name="Other")
    db_session.add(MembershipRow(org_id=org_id, user_id=viewer_id, role="viewer"))
    await db_session.commit()
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, owner_token)
    plot_id = await _create_plot(client, farm_id, owner_token)
    viewer_token = issue_token(str(viewer_id))

    response = client.put(
        f"/plots/{plot_id}/soil", json={"texture": "silt"}, headers=_auth(viewer_token)
    )

    assert response.status_code == 403


async def test_putting_a_soil_profile_of_a_foreign_org_plot_is_404(
    db_session: AsyncSession,
) -> None:
    _org_a, _user_a, token_a = await _member(db_session, role="owner", org_name="Finca A")
    org_b, user_b, token_b = await _member(db_session, role="owner", org_name="Finca B")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_b, token_b)
    plot_id = await _create_plot(client, farm_id, token_b)

    response = client.put(
        f"/plots/{plot_id}/soil", json={"texture": "silt"}, headers=_auth(token_a)
    )

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


async def test_autofilling_soil_uses_the_seminar_recorded_fixture(
    db_session: AsyncSession,
) -> None:
    """T5, ADR-0021: in tests (seminar profile, `TECHCAMP_PROFILE` unset),
    the wired adapter is the recorded fixture — no network call."""
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    plot_id = await _create_plot(client, farm_id, token)

    response = client.post(f"/plots/{plot_id}/soil:autofill", headers=_auth(token))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["plot_id"] == plot_id
    assert body["source"] == "soilgrids"
    assert body["texture"] == "loam"
    assert body["ph"] == pytest.approx(6.5)
    assert body["organic_matter_pct"] == pytest.approx(1.5 * 1.724)
    assert body["field_capacity_pct"] == pytest.approx(25.0)
    assert body["wilting_point_pct"] == pytest.approx(12.0)


async def test_technician_can_autofill_soil(db_session: AsyncSession) -> None:
    org_id, _user_id, owner_token = await _member(db_session, role="owner")
    _org2, tech_id, _tech_token = await _member(db_session, role="technician", org_name="Other")
    db_session.add(MembershipRow(org_id=org_id, user_id=tech_id, role="technician"))
    await db_session.commit()
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, owner_token)
    plot_id = await _create_plot(client, farm_id, owner_token)
    tech_token = issue_token(str(tech_id))

    response = client.post(f"/plots/{plot_id}/soil:autofill", headers=_auth(tech_token))

    assert response.status_code == 200, response.text


async def test_viewer_cannot_autofill_soil(db_session: AsyncSession) -> None:
    org_id, _user_id, owner_token = await _member(db_session, role="owner")
    _org2, viewer_id, _viewer_token = await _member(db_session, role="viewer", org_name="Other")
    db_session.add(MembershipRow(org_id=org_id, user_id=viewer_id, role="viewer"))
    await db_session.commit()
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, owner_token)
    plot_id = await _create_plot(client, farm_id, owner_token)
    viewer_token = issue_token(str(viewer_id))

    response = client.post(f"/plots/{plot_id}/soil:autofill", headers=_auth(viewer_token))

    assert response.status_code == 403


async def test_autofilling_soil_of_a_foreign_org_plot_is_404(db_session: AsyncSession) -> None:
    _org_a, _user_a, token_a = await _member(db_session, role="owner", org_name="Finca A")
    org_b, _user_b, token_b = await _member(db_session, role="owner", org_name="Finca B")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_b, token_b)
    plot_id = await _create_plot(client, farm_id, token_b)

    response = client.post(f"/plots/{plot_id}/soil:autofill", headers=_auth(token_a))

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


async def test_autofilling_soil_maps_a_soilgrids_timeout_to_503(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    plot_id = await _create_plot(client, farm_id, token)

    def _timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom", request=request)

    app.dependency_overrides[get_soilgrids_port] = lambda: IsricSoilGridsAdapter(
        transport=httpx.MockTransport(_timeout)
    )
    try:
        response = client.post(f"/plots/{plot_id}/soil:autofill", headers=_auth(token))
    finally:
        del app.dependency_overrides[get_soilgrids_port]

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"


async def test_autofilling_soil_maps_a_soilgrids_error_status_to_502(
    db_session: AsyncSession,
) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    plot_id = await _create_plot(client, farm_id, token)

    def _server_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    app.dependency_overrides[get_soilgrids_port] = lambda: IsricSoilGridsAdapter(
        transport=httpx.MockTransport(_server_error)
    )
    try:
        response = client.post(f"/plots/{plot_id}/soil:autofill", headers=_auth(token))
    finally:
        del app.dependency_overrides[get_soilgrids_port]

    assert response.status_code == 502
    assert response.headers["content-type"] == "application/problem+json"


async def test_autofilling_soil_queries_soilgrids_at_the_plots_centroid(
    db_session: AsyncSession,
) -> None:
    """GitHub issue #21 round 7: proves the plot's actual PostGIS centroid
    reaches SoilGrids as `lon`/`lat`, not swapped. `_POLYGON` is an
    axis-aligned square with a known centroid, `(-74.095, 10.905)`: lon and
    lat are distinct enough that a swap would fail this assertion."""
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    plot_id = await _create_plot(client, farm_id, token)

    captured: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=SEMINAR_FIXTURE_RESPONSE)

    app.dependency_overrides[get_soilgrids_port] = lambda: IsricSoilGridsAdapter(
        transport=httpx.MockTransport(_handler)
    )
    try:
        response = client.post(f"/plots/{plot_id}/soil:autofill", headers=_auth(token))
    finally:
        del app.dependency_overrides[get_soilgrids_port]

    assert response.status_code == 200, response.text
    assert len(captured) == 1
    params = captured[0].url.params
    assert float(params["lon"]) == pytest.approx(-74.095)
    assert float(params["lat"]) == pytest.approx(10.905)
    assert set(params.get_list("property")) == set(SOILGRIDS_PROPERTIES)
    assert params.get_list("depth") == ["0-5cm"]
    assert params.get_list("value") == ["mean"]


# T6: crop cycles. `_MAIZE_ID = 1` (FAO-56, stages sum to 90 days) and
# `_YAM_ID = 5` (`kc_source = none`, no `crop_stage` rows) are seeded by the
# `67cf2dd1f13e` migration (T3).
_MAIZE_ID = 1
_YAM_ID = 5


async def _create_cycle(
    client: TestClient,
    plot_id: str,
    token: str,
    *,
    crop_id: int = _MAIZE_ID,
    sown_on: str = "2026-01-01",
) -> dict[str, object]:
    created = client.post(
        f"/plots/{plot_id}/cycles",
        json={"crop_id": crop_id, "sown_on": sown_on},
        headers=_auth(token),
    )
    body: dict[str, object] = created.json()
    return body


async def test_owner_creates_a_crop_cycle_and_derives_expected_harvest_on(
    db_session: AsyncSession,
) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    plot_id = await _create_plot(client, farm_id, token)

    response = client.post(
        f"/plots/{plot_id}/cycles",
        json={"crop_id": _MAIZE_ID, "sown_on": "2026-01-01"},
        headers=_auth(token),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["plot_id"] == plot_id
    assert body["crop_id"] == _MAIZE_ID
    assert body["sown_on"] == "2026-01-01"
    assert body["expected_harvest_on"] == "2026-04-01"  # 18+27+31+14 = 90 days
    assert body["status"] == "active"


async def test_creating_a_cycle_for_a_crop_with_no_stages_leaves_expected_harvest_on_null(
    db_session: AsyncSession,
) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    plot_id = await _create_plot(client, farm_id, token)

    response = client.post(
        f"/plots/{plot_id}/cycles",
        json={"crop_id": _YAM_ID, "sown_on": "2026-01-01"},
        headers=_auth(token),
    )

    assert response.status_code == 201, response.text
    assert response.json()["expected_harvest_on"] is None


async def test_creating_a_second_active_cycle_on_the_same_plot_is_409(
    db_session: AsyncSession,
) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    plot_id = await _create_plot(client, farm_id, token)
    first = client.post(
        f"/plots/{plot_id}/cycles",
        json={"crop_id": _MAIZE_ID, "sown_on": "2026-01-01"},
        headers=_auth(token),
    )
    assert first.status_code == 201, first.text

    response = client.post(
        f"/plots/{plot_id}/cycles",
        json={"crop_id": _MAIZE_ID, "sown_on": "2026-02-01"},
        headers=_auth(token),
    )

    assert response.status_code == 409, response.text
    assert response.headers["content-type"] == "application/problem+json"


async def test_creating_a_cycle_with_an_unknown_crop_id_is_422(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    plot_id = await _create_plot(client, farm_id, token)

    response = client.post(
        f"/plots/{plot_id}/cycles",
        json={"crop_id": 9999, "sown_on": "2026-01-01"},
        headers=_auth(token),
    )

    assert response.status_code == 422, response.text
    assert response.headers["content-type"] == "application/problem+json"


async def test_viewer_cannot_create_a_crop_cycle(db_session: AsyncSession) -> None:
    org_id, _user_id, owner_token = await _member(db_session, role="owner")
    _org2, viewer_id, _viewer_token = await _member(db_session, role="viewer", org_name="Other")
    db_session.add(MembershipRow(org_id=org_id, user_id=viewer_id, role="viewer"))
    await db_session.commit()
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, owner_token)
    plot_id = await _create_plot(client, farm_id, owner_token)
    viewer_token = issue_token(str(viewer_id))

    response = client.post(
        f"/plots/{plot_id}/cycles",
        json={"crop_id": _MAIZE_ID, "sown_on": "2026-01-01"},
        headers=_auth(viewer_token),
    )

    assert response.status_code == 403


async def test_technician_can_create_a_crop_cycle(db_session: AsyncSession) -> None:
    org_id, _user_id, owner_token = await _member(db_session, role="owner")
    _org2, tech_id, _tech_token = await _member(db_session, role="technician", org_name="Other")
    db_session.add(MembershipRow(org_id=org_id, user_id=tech_id, role="technician"))
    await db_session.commit()
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, owner_token)
    plot_id = await _create_plot(client, farm_id, owner_token)
    tech_token = issue_token(str(tech_id))

    response = client.post(
        f"/plots/{plot_id}/cycles",
        json={"crop_id": _MAIZE_ID, "sown_on": "2026-01-01"},
        headers=_auth(tech_token),
    )

    assert response.status_code == 201, response.text


async def test_creating_a_cycle_on_a_foreign_org_plot_is_404(db_session: AsyncSession) -> None:
    _org_a, _user_a, token_a = await _member(db_session, role="owner", org_name="Finca A")
    org_b, _user_b, token_b = await _member(db_session, role="owner", org_name="Finca B")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_b, token_b)
    plot_id = await _create_plot(client, farm_id, token_b)

    response = client.post(
        f"/plots/{plot_id}/cycles",
        json={"crop_id": _MAIZE_ID, "sown_on": "2026-01-01"},
        headers=_auth(token_a),
    )

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


async def test_owner_patches_a_cycle_to_harvested(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    plot_id = await _create_plot(client, farm_id, token)
    cycle = await _create_cycle(client, plot_id, token)

    response = client.patch(
        f"/cycles/{cycle['id']}", json={"status": "harvested"}, headers=_auth(token)
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "harvested"


async def test_patching_a_cycle_can_change_expected_harvest_on_without_a_status(
    db_session: AsyncSession,
) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    plot_id = await _create_plot(client, farm_id, token)
    cycle = await _create_cycle(client, plot_id, token)

    response = client.patch(
        f"/cycles/{cycle['id']}", json={"expected_harvest_on": "2026-05-01"}, headers=_auth(token)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["expected_harvest_on"] == "2026-05-01"
    assert body["status"] == "active"


async def test_patching_a_harvested_cycle_back_to_active_is_422(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    plot_id = await _create_plot(client, farm_id, token)
    cycle = await _create_cycle(client, plot_id, token)
    harvested = client.patch(
        f"/cycles/{cycle['id']}", json={"status": "harvested"}, headers=_auth(token)
    )
    assert harvested.status_code == 200, harvested.text

    response = client.patch(
        f"/cycles/{cycle['id']}", json={"status": "active"}, headers=_auth(token)
    )

    assert response.status_code == 422, response.text
    assert response.headers["content-type"] == "application/problem+json"


async def test_patching_a_cycle_with_explicit_null_status_is_422(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, token)
    plot_id = await _create_plot(client, farm_id, token)
    cycle = await _create_cycle(client, plot_id, token)

    response = client.patch(f"/cycles/{cycle['id']}", json={"status": None}, headers=_auth(token))

    assert response.status_code == 422, response.text
    assert response.headers["content-type"] == "application/problem+json"


async def test_viewer_cannot_patch_a_crop_cycle(db_session: AsyncSession) -> None:
    org_id, _user_id, owner_token = await _member(db_session, role="owner")
    _org2, viewer_id, _viewer_token = await _member(db_session, role="viewer", org_name="Other")
    db_session.add(MembershipRow(org_id=org_id, user_id=viewer_id, role="viewer"))
    await db_session.commit()
    client = TestClient(app)
    farm_id = await _create_farm(client, org_id, owner_token)
    plot_id = await _create_plot(client, farm_id, owner_token)
    cycle = await _create_cycle(client, plot_id, owner_token)
    viewer_token = issue_token(str(viewer_id))

    response = client.patch(
        f"/cycles/{cycle['id']}", json={"status": "harvested"}, headers=_auth(viewer_token)
    )

    assert response.status_code == 403


async def test_patching_a_cycle_of_a_foreign_org_plot_is_404(db_session: AsyncSession) -> None:
    _org_a, _user_a, token_a = await _member(db_session, role="owner", org_name="Finca A")
    org_b, _user_b, token_b = await _member(db_session, role="owner", org_name="Finca B")
    client = TestClient(app)
    farm_id = await _create_farm(client, org_b, token_b)
    plot_id = await _create_plot(client, farm_id, token_b)
    cycle = await _create_cycle(client, plot_id, token_b)

    response = client.patch(
        f"/cycles/{cycle['id']}", json={"status": "harvested"}, headers=_auth(token_a)
    )

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
