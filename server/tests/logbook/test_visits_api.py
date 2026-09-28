"""Extension visit read API tests (docs/04 §Visitas; docs/03 §extension_visit; docs/09; D9).

GET /api/v1/farms/{farm_id}/visits
GET /api/v1/organizations/{org_id}/visits?from=&to=
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.adapters.security.token_issuer import issue_token
from techcamp.logbook.adapters.orm import ExtensionVisitRow
from techcamp.main import app
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)
_NOW = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class OrgContext:
    org_id: UUID
    farm_id: UUID
    plot_id: UUID
    user_ids: dict[str, UUID]
    tokens: dict[str, str]


async def _create_org_context(
    session: AsyncSession,
    *,
    roles: tuple[str, ...] = ("owner", "technician", "producer", "viewer"),
) -> OrgContext:
    org_id = uuid7()
    farm_id = uuid7()
    plot_id = uuid7()
    user_ids = {role: uuid7() for role in roles}

    session.add(OrganizationRow(id=org_id, name=f"Org {org_id.hex[:6]}", kind="individual"))
    for role, user_id in user_ids.items():
        session.add(AppUserRow(id=user_id, phone=f"+57{uuid7().int % 10**13:013d}", full_name=role))
    await session.commit()

    for role, user_id in user_ids.items():
        session.add(MembershipRow(org_id=org_id, user_id=user_id, role=role))
    await session.commit()

    session.add(
        FarmRow(
            id=farm_id,
            org_id=org_id,
            name="Finca Principal",
            municipality_code="47001",
            location=_POINT,
            technician_id=user_ids.get("technician"),
        )
    )
    await session.commit()

    session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=_BOUNDARY,
            irrigation_system="drip",
        )
    )
    await session.commit()

    return OrgContext(
        org_id=org_id,
        farm_id=farm_id,
        plot_id=plot_id,
        user_ids=user_ids,
        tokens={role: issue_token(str(user_id)) for role, user_id in user_ids.items()},
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _client() -> TestClient:
    return TestClient(app, base_url="http://testserver/api/v1")


def _make_visit(
    *,
    org_id: UUID,
    farm_id: UUID,
    technician_id: UUID,
    visited_on: date,
    plot_id: UUID | None = None,
    topics: list[str] | None = None,
    recommendations: str | None = "Recommendations",
    commitments: str | None = "Commitments",
    notes: str | None = "Notes",
    client_updated_at: datetime | None = None,
    deleted_at: datetime | None = None,
    **overrides: Any,
) -> ExtensionVisitRow:
    return ExtensionVisitRow(
        id=overrides.get("id", uuid7()),
        org_id=org_id,
        farm_id=farm_id,
        plot_id=plot_id,
        technician_id=technician_id,
        visited_on=visited_on,
        topics=topics if topics is not None else ["human_capacities", "social_capacities"],
        recommendations=recommendations,
        commitments=commitments,
        notes=notes,
        client_updated_at=client_updated_at or _NOW,
        deleted_at=deleted_at,
    )


async def test_farm_visits_returns_only_that_farms_non_deleted_visits_newest_first_and_pages(
    db_session: AsyncSession,
) -> None:
    org = await _create_org_context(db_session)
    other_farm_id = uuid7()
    db_session.add(
        FarmRow(
            id=other_farm_id,
            org_id=org.org_id,
            name="Otra Finca",
            municipality_code="47001",
            location=_POINT,
        )
    )
    await db_session.commit()

    # 3 active visits on farm_id, 1 deleted on farm_id, 1 active on other_farm_id
    v1 = _make_visit(
        org_id=org.org_id,
        farm_id=org.farm_id,
        technician_id=org.user_ids["technician"],
        visited_on=date(2026, 9, 20),
        client_updated_at=_NOW - timedelta(days=2),
    )
    v2 = _make_visit(
        org_id=org.org_id,
        farm_id=org.farm_id,
        technician_id=org.user_ids["technician"],
        visited_on=date(2026, 9, 22),
        client_updated_at=_NOW - timedelta(days=1),
    )
    v3 = _make_visit(
        org_id=org.org_id,
        farm_id=org.farm_id,
        technician_id=org.user_ids["technician"],
        visited_on=date(2026, 9, 25),
        client_updated_at=_NOW,
    )
    v_deleted = _make_visit(
        org_id=org.org_id,
        farm_id=org.farm_id,
        technician_id=org.user_ids["technician"],
        visited_on=date(2026, 9, 24),
        deleted_at=_NOW,
    )
    v_other_farm = _make_visit(
        org_id=org.org_id,
        farm_id=other_farm_id,
        technician_id=org.user_ids["technician"],
        visited_on=date(2026, 9, 23),
    )
    for v in (v1, v2, v3, v_deleted, v_other_farm):
        db_session.add(v)
    await db_session.commit()

    client = _client()

    # Request page 1 with limit=2 (any member can read, test with viewer)
    response = client.get(
        f"/farms/{org.farm_id}/visits?limit=2",
        headers=_auth(org.tokens["viewer"]),
    )
    assert response.status_code == 200, response.text
    data = response.json()

    # Positive assertions: 2 items returned, newest first (v3, v2 by uuid7 id desc)
    expected_newest = sorted([v1.id, v2.id, v3.id], reverse=True)
    assert len(data["items"]) == 2
    assert data["items"][0]["id"] == str(expected_newest[0])
    assert data["items"][1]["id"] == str(expected_newest[1])
    assert data["next_cursor"] == str(expected_newest[1])

    # Negative assertions: deleted visit and other farm visit NOT returned
    assert str(v_deleted.id) not in response.text
    assert str(v_other_farm.id) not in response.text

    # Request page 2 with cursor
    response_p2 = client.get(
        f"/farms/{org.farm_id}/visits?limit=2&cursor={data['next_cursor']}",
        headers=_auth(org.tokens["producer"]),
    )
    assert response_p2.status_code == 200, response_p2.text
    data_p2 = response_p2.json()
    assert len(data_p2["items"]) == 1
    assert data_p2["items"][0]["id"] == str(expected_newest[2])
    assert data_p2["next_cursor"] is None
    assert str(v_deleted.id) not in response_p2.text
    assert str(v_other_farm.id) not in response_p2.text


async def test_farm_visits_for_foreign_or_missing_farm_returns_404(
    db_session: AsyncSession,
) -> None:
    org_a = await _create_org_context(db_session)
    org_b = await _create_org_context(db_session)
    client = _client()

    # Foreign farm from org_a requested by user of org_b -> 404
    response = client.get(
        f"/farms/{org_a.farm_id}/visits",
        headers=_auth(org_b.tokens["owner"]),
    )
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["title"] == "Farm not found"
    assert response.status_code != 200

    # Nonexistent farm -> 404
    random_farm_id = uuid7()
    response_missing = client.get(
        f"/farms/{random_farm_id}/visits",
        headers=_auth(org_a.tokens["owner"]),
    )
    assert response_missing.status_code == 404
    assert response_missing.headers["content-type"] == "application/problem+json"
    assert response_missing.json()["title"] == "Farm not found"


async def test_org_visits_export_filters_from_and_to_inclusive_and_pages(
    db_session: AsyncSession,
) -> None:
    org = await _create_org_context(db_session)

    v_before = _make_visit(
        org_id=org.org_id,
        farm_id=org.farm_id,
        technician_id=org.user_ids["technician"],
        visited_on=date(2026, 9, 20),
        client_updated_at=_NOW - timedelta(days=5),
    )
    # 5 active visits within the [2026-09-25, 2026-09-28] inclusive range
    v1 = _make_visit(
        org_id=org.org_id,
        farm_id=org.farm_id,
        technician_id=org.user_ids["technician"],
        visited_on=date(2026, 9, 25),
        client_updated_at=_NOW - timedelta(hours=4),
    )
    v2 = _make_visit(
        org_id=org.org_id,
        farm_id=org.farm_id,
        technician_id=org.user_ids["technician"],
        visited_on=date(2026, 9, 26),
        client_updated_at=_NOW - timedelta(hours=3),
    )
    v3 = _make_visit(
        org_id=org.org_id,
        farm_id=org.farm_id,
        technician_id=org.user_ids["technician"],
        visited_on=date(2026, 9, 27),
        client_updated_at=_NOW - timedelta(hours=2),
    )
    v4 = _make_visit(
        org_id=org.org_id,
        farm_id=org.farm_id,
        technician_id=org.user_ids["technician"],
        visited_on=date(2026, 9, 28),
        client_updated_at=_NOW - timedelta(hours=1),
    )
    v5 = _make_visit(
        org_id=org.org_id,
        farm_id=org.farm_id,
        technician_id=org.user_ids["technician"],
        visited_on=date(2026, 9, 28),
        client_updated_at=_NOW,
    )
    v_after = _make_visit(
        org_id=org.org_id,
        farm_id=org.farm_id,
        technician_id=org.user_ids["technician"],
        visited_on=date(2026, 10, 2),
        client_updated_at=_NOW + timedelta(days=1),
    )
    v_deleted_in_range = _make_visit(
        org_id=org.org_id,
        farm_id=org.farm_id,
        technician_id=org.user_ids["technician"],
        visited_on=date(2026, 9, 26),
        deleted_at=_NOW,
    )
    for v in (v_before, v1, v2, v3, v4, v5, v_after, v_deleted_in_range):
        db_session.add(v)
    await db_session.commit()

    client = _client()

    # Limit/cursor walk over the 5 in-range visits with limit=2 (not an exact multiple: 2, 2, 1)
    expected_newest = sorted([v1.id, v2.id, v3.id, v4.id, v5.id], reverse=True)

    # Page 1: first 2 items
    response_p1 = client.get(
        f"/organizations/{org.org_id}/visits?from=2026-09-25&to=2026-09-28&limit=2",
        headers=_auth(org.tokens["owner"]),
    )
    assert response_p1.status_code == 200, response_p1.text
    data_p1 = response_p1.json()
    assert len(data_p1["items"]) == 2
    assert [item["id"] for item in data_p1["items"]] == [
        str(expected_newest[0]),
        str(expected_newest[1]),
    ]
    assert data_p1["next_cursor"] == str(expected_newest[1])
    assert str(v_before.id) not in response_p1.text
    assert str(v_after.id) not in response_p1.text
    assert str(v_deleted_in_range.id) not in response_p1.text

    # Page 2: next 2 items
    response_p2 = client.get(
        f"/organizations/{org.org_id}/visits?from=2026-09-25&to=2026-09-28&limit=2&cursor={data_p1['next_cursor']}",
        headers=_auth(org.tokens["technician"]),
    )
    assert response_p2.status_code == 200, response_p2.text
    data_p2 = response_p2.json()
    assert len(data_p2["items"]) == 2
    assert [item["id"] for item in data_p2["items"]] == [
        str(expected_newest[2]),
        str(expected_newest[3]),
    ]
    assert data_p2["next_cursor"] == str(expected_newest[3])
    assert str(v_before.id) not in response_p2.text
    assert str(v_after.id) not in response_p2.text
    assert str(v_deleted_in_range.id) not in response_p2.text

    # Page 3: last 1 item, next_cursor is null
    response_p3 = client.get(
        f"/organizations/{org.org_id}/visits?from=2026-09-25&to=2026-09-28&limit=2&cursor={data_p2['next_cursor']}",
        headers=_auth(org.tokens["owner"]),
    )
    assert response_p3.status_code == 200, response_p3.text
    data_p3 = response_p3.json()
    assert len(data_p3["items"]) == 1
    assert [item["id"] for item in data_p3["items"]] == [str(expected_newest[4])]
    assert data_p3["next_cursor"] is None
    assert str(v_before.id) not in response_p3.text
    assert str(v_after.id) not in response_p3.text
    assert str(v_deleted_in_range.id) not in response_p3.text


async def test_org_visits_role_authorization_owner_technician_vs_producer_viewer(
    db_session: AsyncSession,
) -> None:
    org = await _create_org_context(db_session)
    visit = _make_visit(
        org_id=org.org_id,
        farm_id=org.farm_id,
        technician_id=org.user_ids["technician"],
        visited_on=date(2026, 9, 28),
    )
    db_session.add(visit)
    await db_session.commit()

    client = _client()

    # Owner -> 200
    res_owner = client.get(
        f"/organizations/{org.org_id}/visits",
        headers=_auth(org.tokens["owner"]),
    )
    assert res_owner.status_code == 200
    assert len(res_owner.json()["items"]) == 1

    # Technician -> 200
    res_tech = client.get(
        f"/organizations/{org.org_id}/visits",
        headers=_auth(org.tokens["technician"]),
    )
    assert res_tech.status_code == 200
    assert len(res_tech.json()["items"]) == 1

    # Producer -> 403 Forbidden
    res_producer = client.get(
        f"/organizations/{org.org_id}/visits",
        headers=_auth(org.tokens["producer"]),
    )
    assert res_producer.status_code == 403
    assert res_producer.headers["content-type"] == "application/problem+json"
    assert res_producer.json()["title"] == "Role cannot export visits"
    assert res_producer.status_code != 200
    assert str(visit.id) not in res_producer.text

    # Viewer -> 403 Forbidden
    res_viewer = client.get(
        f"/organizations/{org.org_id}/visits",
        headers=_auth(org.tokens["viewer"]),
    )
    assert res_viewer.status_code == 403
    assert res_viewer.headers["content-type"] == "application/problem+json"
    assert res_viewer.json()["title"] == "Role cannot export visits"
    assert res_viewer.status_code != 200
    assert str(visit.id) not in res_viewer.text


async def test_org_visits_for_non_member_returns_404(
    db_session: AsyncSession,
) -> None:
    org_a = await _create_org_context(db_session)
    org_b = await _create_org_context(db_session)
    client = _client()

    # User of org_b tries to export visits of org_a -> 404 (not 403, no existence leak)
    response = client.get(
        f"/organizations/{org_a.org_id}/visits",
        headers=_auth(org_b.tokens["owner"]),
    )
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["title"] == "Organization not found"
    assert response.status_code != 200
    assert response.status_code != 403


async def test_cross_org_isolation_per_endpoint(
    db_session: AsyncSession,
) -> None:
    org_a = await _create_org_context(db_session)
    org_b = await _create_org_context(db_session)

    visit_a = _make_visit(
        org_id=org_a.org_id,
        farm_id=org_a.farm_id,
        technician_id=org_a.user_ids["technician"],
        visited_on=date(2026, 9, 28),
    )
    visit_b = _make_visit(
        org_id=org_b.org_id,
        farm_id=org_b.farm_id,
        technician_id=org_b.user_ids["technician"],
        visited_on=date(2026, 9, 28),
    )
    db_session.add(visit_a)
    db_session.add(visit_b)
    await db_session.commit()

    client = _client()

    # Endpoint 1: GET /farms/{farm_id}/visits
    # Org A sees only visit_a, never visit_b
    res_farm_a = client.get(
        f"/farms/{org_a.farm_id}/visits",
        headers=_auth(org_a.tokens["owner"]),
    )
    assert res_farm_a.status_code == 200
    items_a = res_farm_a.json()["items"]
    assert len(items_a) == 1
    assert items_a[0]["id"] == str(visit_a.id)
    assert str(visit_b.id) not in res_farm_a.text

    # Endpoint 2: GET /organizations/{org_id}/visits
    # Org A export sees only visit_a, never visit_b
    res_org_a = client.get(
        f"/organizations/{org_a.org_id}/visits",
        headers=_auth(org_a.tokens["owner"]),
    )
    assert res_org_a.status_code == 200
    items_org_a = res_org_a.json()["items"]
    assert len(items_org_a) == 1
    assert items_org_a[0]["id"] == str(visit_a.id)
    assert str(visit_b.id) not in res_org_a.text
