"""The enrollment-survey endpoints (docs/04-api.md:51-52, 233; D-T0.10, D-T0.11).

Against the real database and the real router: roles, the upsert, and the
`404`s org isolation requires (docs/09-cuellos-de-botella.md#seguridad) are
adapter behavior, so a double at the port would prove none of it.

Every behavior test carries its negative assertion: the rejected call is
followed by the state it must not have changed.
"""

from __future__ import annotations

from itertools import count
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.adapters.security.token_issuer import issue_token
from techcamp.main import app
from techcamp.metrics.adapters.orm import PlotBaselineRow
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)
_CROP_ID = 1
"""`maize`, seeded by `67cf2dd1f13e_add_crop_catalog`; no test writes the catalog."""
_ENROLLED_ON = "2026-02-10"
"""Frozen: a test clock must not drift (E11 lessons, #12)."""

# A counter, not `uuid7().int % 10**10`: the modulo could collide on the unique
# `phone` column between two `uuid7()`s generated close together (#21).
_phone_seq = count()


def _client() -> TestClient:
    return TestClient(app, base_url="http://testserver/api/v1")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _org_with_plot(db_session: AsyncSession, *, role: str) -> tuple[UUID, UUID, UUID, str]:
    """One organization with a farm, a plot and a member of `role`;
    returns `(org_id, plot_id, user_id, token)`."""
    org_id, user_id = uuid7(), uuid7()
    db_session.add(OrganizationRow(id=org_id, name="Finca", kind="individual"))
    db_session.add(AppUserRow(id=user_id, phone=f"+5730099{next(_phone_seq):05d}"))
    await db_session.commit()
    farm_id = uuid7()
    db_session.add(
        FarmRow(id=farm_id, org_id=org_id, name="Finca", municipality_code="47001", location=_POINT)
    )
    await db_session.commit()
    plot_id = uuid7()
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=_BOUNDARY,
            irrigation_system="drip",
        )
    )
    db_session.add(MembershipRow(org_id=org_id, user_id=user_id, role=role))
    await db_session.commit()
    return org_id, plot_id, user_id, issue_token(str(user_id))


def _survey(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "enrolled_on": _ENROLLED_ON,
        "crop_id": _CROP_ID,
        "last_yield_kg_ha": 3200,
        "irrigation_practice": "gravity",
    }
    payload.update(changes)
    return payload


async def test_an_owner_saves_and_reads_the_survey(db_session: AsyncSession) -> None:
    _org_id, plot_id, user_id, token = await _org_with_plot(db_session, role="owner")
    client = _client()

    response = client.put(
        f"/plots/{plot_id}/baseline",
        json=_survey(last_cost_cop_ha=1_500_000),
        headers=_auth(token),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["plot_id"] == str(plot_id)
    assert body["crop_id"] == _CROP_ID
    assert body["last_yield_kg_ha"] == 3200
    assert body["last_cost_cop_ha"] == 1_500_000
    assert body["irrigation_practice"] == "gravity"
    assert body["enrolled_on"] == _ENROLLED_ON

    read = client.get(f"/plots/{plot_id}/baseline", headers=_auth(token))

    assert read.status_code == 200, read.text
    assert read.json() == body
    assert body["recorded_by"] == str(user_id)


async def test_a_technician_edits_an_existing_survey(db_session: AsyncSession) -> None:
    org_id, plot_id, _user_id, token = await _org_with_plot(db_session, role="owner")
    client = _client()
    client.put(f"/plots/{plot_id}/baseline", json=_survey(), headers=_auth(token))
    technician_id = uuid7()
    db_session.add(AppUserRow(id=technician_id, phone=f"+5730088{next(_phone_seq):05d}"))
    await db_session.commit()
    db_session.add(MembershipRow(org_id=org_id, user_id=technician_id, role="technician"))
    await db_session.commit()

    response = client.put(
        f"/plots/{plot_id}/baseline",
        json=_survey(last_yield_kg_ha=2800, irrigation_practice="drip"),
        headers=_auth(issue_token(str(technician_id))),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["last_yield_kg_ha"] == 2800
    assert body["irrigation_practice"] == "drip"
    assert body["recorded_by"] == str(technician_id)

    rows = (
        await db_session.execute(
            select(func.count())
            .select_from(PlotBaselineRow)
            .where(PlotBaselineRow.plot_id == plot_id)
        )
    ).scalar_one()
    assert rows == 1


async def test_an_omitted_cost_is_null_and_not_zero(db_session: AsyncSession) -> None:
    _org_id, plot_id, _user_id, token = await _org_with_plot(db_session, role="owner")
    client = _client()

    response = client.put(f"/plots/{plot_id}/baseline", json=_survey(), headers=_auth(token))

    assert response.status_code == 200, response.text
    assert response.json()["last_cost_cop_ha"] is None


async def test_a_plot_without_a_survey_is_404(db_session: AsyncSession) -> None:
    _org_id, plot_id, _user_id, token = await _org_with_plot(db_session, role="owner")
    client = _client()

    response = client.get(f"/plots/{plot_id}/baseline", headers=_auth(token))

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


async def test_a_plot_in_another_org_is_404(db_session: AsyncSession) -> None:
    _org_a, _plot_a, _user_a, token_a = await _org_with_plot(db_session, role="owner")
    _org_b, plot_b, _user_b, _token_b = await _org_with_plot(db_session, role="owner")
    client = _client()

    response = client.put(f"/plots/{plot_b}/baseline", json=_survey(), headers=_auth(token_a))

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"

    read = client.get(f"/plots/{plot_b}/baseline", headers=_auth(token_a))

    assert read.status_code == 404

    stored = (
        await db_session.execute(
            select(func.count())
            .select_from(PlotBaselineRow)
            .where(PlotBaselineRow.plot_id == plot_b)
        )
    ).scalar_one()
    assert stored == 0


@pytest.mark.parametrize("role", ["producer", "viewer"])
async def test_a_read_only_role_cannot_save_the_survey(db_session: AsyncSession, role: str) -> None:
    org_id, plot_id, _user_id, token = await _org_with_plot(db_session, role=role)
    owner_id = uuid7()
    db_session.add(AppUserRow(id=owner_id, phone=f"+5730088{next(_phone_seq):05d}"))
    await db_session.commit()
    db_session.add(MembershipRow(org_id=org_id, user_id=owner_id, role="owner"))
    await db_session.commit()
    client = _client()

    response = client.put(f"/plots/{plot_id}/baseline", json=_survey(), headers=_auth(token))

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"

    stored = (
        await db_session.execute(
            select(func.count())
            .select_from(PlotBaselineRow)
            .where(PlotBaselineRow.plot_id == plot_id)
        )
    ).scalar_one()
    assert stored == 0


async def test_a_viewer_reads_the_survey_of_the_plot(db_session: AsyncSession) -> None:
    org_id, plot_id, _owner_id, owner_token = await _org_with_plot(db_session, role="owner")
    client = _client()
    client.put(f"/plots/{plot_id}/baseline", json=_survey(), headers=_auth(owner_token))
    viewer_id = uuid7()
    db_session.add(AppUserRow(id=viewer_id, phone=f"+5730077{next(_phone_seq):05d}"))
    await db_session.commit()
    db_session.add(MembershipRow(org_id=org_id, user_id=viewer_id, role="viewer"))
    await db_session.commit()

    response = client.get(f"/plots/{plot_id}/baseline", headers=_auth(issue_token(str(viewer_id))))

    assert response.status_code == 200, response.text
    assert response.json()["plot_id"] == str(plot_id)


async def test_a_crop_outside_the_catalog_is_422(db_session: AsyncSession) -> None:
    _org_id, plot_id, _user_id, token = await _org_with_plot(db_session, role="owner")
    client = _client()

    response = client.put(
        f"/plots/{plot_id}/baseline", json=_survey(crop_id=9999), headers=_auth(token)
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"

    stored = (
        await db_session.execute(
            select(func.count())
            .select_from(PlotBaselineRow)
            .where(PlotBaselineRow.plot_id == plot_id)
        )
    ).scalar_one()
    assert stored == 0


async def test_an_unknown_irrigation_practice_is_422(db_session: AsyncSession) -> None:
    _org_id, plot_id, _user_id, token = await _org_with_plot(db_session, role="owner")
    client = _client()

    response = client.put(
        f"/plots/{plot_id}/baseline",
        json=_survey(irrigation_practice="flood"),
        headers=_auth(token),
    )

    assert response.status_code == 422


async def test_a_negative_yield_is_422(db_session: AsyncSession) -> None:
    _org_id, plot_id, _user_id, token = await _org_with_plot(db_session, role="owner")
    client = _client()

    response = client.put(
        f"/plots/{plot_id}/baseline", json=_survey(last_yield_kg_ha=-1), headers=_auth(token)
    )

    assert response.status_code == 422
