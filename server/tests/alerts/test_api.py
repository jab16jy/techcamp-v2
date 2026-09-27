"""Alert endpoints (docs/04-api.md §Alertas y notificaciones; D15).

Org isolation per docs/09-cuellos-de-botella.md#seguridad: an alert of another
organization responds 404, never 403, and `GET /alerts` lists one org only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.orm import AlertRuleRow
from techcamp.alerts.adapters.repositories import SqlAlchemyAlertRepository
from techcamp.alerts.application import open_alert
from techcamp.alerts.domain import Alert, AlertRule, AlertState, Severity
from techcamp.farms.adapters.orm import CropRow, FarmRow, PlotRow
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.adapters.security.token_issuer import issue_token
from techcamp.main import app
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)
_MORNING = datetime(2026, 9, 26, 15, 0, tzinfo=UTC)  # 10:00 Bogotá


@dataclass(frozen=True, slots=True)
class Org:
    org_id: UUID
    farm_id: UUID
    plot_id: UUID
    user_ids: dict[str, UUID]
    tokens: dict[str, str]


async def _org(
    session: AsyncSession, *, roles: tuple[str, ...] = ("owner", "producer", "viewer")
) -> Org:
    org_id, farm_id, plot_id = uuid7(), uuid7(), uuid7()
    user_ids = {role: uuid7() for role in roles}
    for role, user_id in user_ids.items():
        session.add(AppUserRow(id=user_id, phone=f"+57{uuid7().int % 10**13:013d}", full_name=role))
    session.add(OrganizationRow(id=org_id, name=f"Org {org_id.hex[:6]}", kind="individual"))
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
    return Org(
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


async def _factory_rule(session: AsyncSession, code: str) -> AlertRule:
    """A factory rule as the evaluator holds it (`org_id = null`).

    The adapter boundary: `alert_rule` stores its thresholds as `Numeric` and
    the domain takes them as `float`.
    """
    row = (
        await session.execute(select(AlertRuleRow).where(AlertRuleRow.code == code))
    ).scalar_one()
    return AlertRule(
        code=row.code,
        id=row.id,
        org_id=row.org_id,
        metric=row.metric,
        operator=row.operator,
        threshold=float(row.threshold) if row.threshold is not None else None,
        hysteresis=float(row.hysteresis),
        min_duration=timedelta(minutes=row.min_duration_min),
        severity=Severity(row.severity),
        crop_id=row.crop_id,
    )


async def _open(session: AsyncSession, org: Org, code: str, *, at: datetime = _MORNING) -> Alert:
    return await open_alert(
        rule=await _factory_rule(session, code),
        plot_id=org.plot_id,
        evidence={"at": at.isoformat()},
        at=at,
        alerts=SqlAlchemyAlertRepository(session),
    )


async def test_list_alerts_filters_by_plot_and_state(db_session: AsyncSession) -> None:
    org = await _org(db_session)
    other_plot_id = uuid7()
    db_session.add(
        PlotRow(
            id=other_plot_id,
            org_id=org.org_id,
            farm_id=org.farm_id,
            name="Lote 2",
            boundary=_BOUNDARY,
            irrigation_system="drip",
        )
    )
    await db_session.commit()
    heat = await _open(db_session, org, "heat_stress")
    await open_alert(
        rule=await _factory_rule(db_session, "water_stress"),
        plot_id=other_plot_id,
        at=_MORNING + timedelta(minutes=1),
        alerts=SqlAlchemyAlertRepository(db_session),
    )
    client = _client()

    response = client.get(
        f"/alerts?org_id={org.org_id}&plot_id={org.plot_id}", headers=_auth(org.tokens["owner"])
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["id"] for item in body["items"]] == [str(heat.id)]
    assert body["items"][0]["rule_code"] == "heat_stress"
    assert body["next_cursor"] is None

    by_state = client.get(
        f"/alerts?org_id={org.org_id}&state=acknowledged", headers=_auth(org.tokens["owner"])
    )
    assert by_state.json()["items"] == []

    client.post(f"/alerts/{heat.id}:acknowledge", headers=_auth(org.tokens["producer"]))
    acknowledged = client.get(
        f"/alerts?org_id={org.org_id}&state=acknowledged", headers=_auth(org.tokens["owner"])
    )
    assert [item["id"] for item in acknowledged.json()["items"]] == [str(heat.id)]


async def test_list_alerts_pages_by_cursor_newest_first(db_session: AsyncSession) -> None:
    org = await _org(db_session)
    opened = [
        await _open(db_session, org, code, at=_MORNING + timedelta(minutes=index))
        for index, code in enumerate(("heat_stress", "water_stress", "waterlogging"))
    ]
    client = _client()

    first = client.get(
        f"/alerts?org_id={org.org_id}&limit=2", headers=_auth(org.tokens["owner"])
    ).json()

    # uuid7 is time-ordered, so newest first is descending `id`.
    newest = sorted((alert.id for alert in opened), reverse=True)
    assert [item["id"] for item in first["items"]] == [str(newest[0]), str(newest[1])]
    assert first["next_cursor"] == str(newest[1])

    second = client.get(
        f"/alerts?org_id={org.org_id}&limit=2&cursor={newest[1]}",
        headers=_auth(org.tokens["owner"]),
    ).json()
    assert [item["id"] for item in second["items"]] == [str(newest[2])]
    assert second["next_cursor"] is None


async def test_list_alerts_never_returns_another_orgs_alerts(db_session: AsyncSession) -> None:
    org = await _org(db_session)
    other = await _org(db_session)
    mine = await _open(db_session, org, "heat_stress")
    theirs = await _open(db_session, other, "water_stress")
    client = _client()

    response = client.get(f"/alerts?org_id={org.org_id}", headers=_auth(org.tokens["owner"]))

    assert [item["id"] for item in response.json()["items"]] == [str(mine.id)]
    assert str(theirs.id) not in response.text


async def test_listing_alerts_of_a_foreign_org_is_404(db_session: AsyncSession) -> None:
    org = await _org(db_session)
    other = await _org(db_session)
    client = _client()

    response = client.get(f"/alerts?org_id={org.org_id}", headers=_auth(other.tokens["owner"]))

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["title"] == "Organization not found"


async def test_acknowledge_then_resolve_stores_the_note(db_session: AsyncSession) -> None:
    org = await _org(db_session)
    alert = await _open(db_session, org, "heat_stress")
    client = _client()

    acknowledged = client.post(
        f"/alerts/{alert.id}:acknowledge", headers=_auth(org.tokens["producer"])
    )

    assert acknowledged.status_code == 200, acknowledged.text
    body = acknowledged.json()
    assert body["state"] == "acknowledged"
    assert body["acknowledged_at"] is not None
    assert body["resolved_at"] is None

    resolved = client.post(
        f"/alerts/{alert.id}:resolve",
        json={"note": "Se abrió la válvula"},
        headers=_auth(org.tokens["owner"]),
    )

    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["state"] == "resolved"
    assert resolved.json()["resolution_note"] == "Se abrió la válvula"


async def test_resolving_an_open_alert_is_409(db_session: AsyncSession) -> None:
    """Manual resolution is only from `acknowledged` (docs/06 §3 diagram, D12)."""
    org = await _org(db_session)
    alert = await _open(db_session, org, "heat_stress")
    client = _client()

    response = client.post(f"/alerts/{alert.id}:resolve", headers=_auth(org.tokens["owner"]))

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"


async def test_an_alert_of_another_org_is_404(db_session: AsyncSession) -> None:
    org = await _org(db_session)
    other = await _org(db_session)
    alert = await _open(db_session, org, "heat_stress")
    client = _client()

    response = client.post(f"/alerts/{alert.id}:acknowledge", headers=_auth(other.tokens["owner"]))

    assert response.status_code == 404
    assert response.json()["title"] == "Alert not found"


async def test_a_viewer_may_not_acknowledge_an_alert(db_session: AsyncSession) -> None:
    org = await _org(db_session)
    alert = await _open(db_session, org, "heat_stress")
    client = _client()

    response = client.post(f"/alerts/{alert.id}:acknowledge", headers=_auth(org.tokens["viewer"]))

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"
    assert alert.state is AlertState.OPEN


def _rule_body(org: Org, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "org_id": str(org.org_id),
        "code": "orchard_heat",
        "metric": "air_temp",
        "operator": ">",
        "threshold": 34.0,
        "hysteresis": 1.5,
        "min_duration_min": 30,
        "severity": "critical",
    }
    return {**body, **overrides}


async def _crop_id(session: AsyncSession) -> int:
    return (await session.execute(select(CropRow.id).limit(1))).scalar_one()


async def test_list_alert_rules_returns_the_factory_rules_and_the_orgs(
    db_session: AsyncSession,
) -> None:
    """D11: the factory rules (`org_id = null`) are readable, another org's are not."""
    org = await _org(db_session)
    other = await _org(db_session)
    created = (
        _client()
        .post("/alert-rules", json=_rule_body(org), headers=_auth(org.tokens["owner"]))
        .json()
    )
    theirs = (
        _client()
        .post(
            "/alert-rules",
            json=_rule_body(other, code="other_heat"),
            headers=_auth(other.tokens["owner"]),
        )
        .json()
    )
    client = _client()

    response = client.get(f"/alert-rules?org_id={org.org_id}", headers=_auth(org.tokens["viewer"]))

    assert response.status_code == 200, response.text
    by_code = {rule["code"]: rule for rule in response.json()}
    assert "heat_stress" in by_code  # a factory rule (org_id is null)
    assert by_code["orchard_heat"]["id"] == created["id"]
    assert "other_heat" not in by_code
    assert theirs["id"] not in response.text


async def test_listing_alert_rules_of_a_foreign_org_is_404(db_session: AsyncSession) -> None:
    org = await _org(db_session)
    other = await _org(db_session)
    client = _client()

    response = client.get(f"/alert-rules?org_id={org.org_id}", headers=_auth(other.tokens["owner"]))

    assert response.status_code == 404
    assert response.json()["title"] == "Organization not found"


async def test_owner_creates_an_alert_rule(db_session: AsyncSession) -> None:
    org = await _org(db_session)
    crop_id = await _crop_id(db_session)
    client = _client()

    response = client.post(
        "/alert-rules",
        json=_rule_body(org, crop_id=crop_id),
        headers=_auth(org.tokens["owner"]),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["org_id"], body["code"], body["metric"]) == (
        str(org.org_id),
        "orchard_heat",
        "air_temp",
    )
    assert (body["operator"], body["threshold"], body["hysteresis"]) == (">", 34.0, 1.5)
    assert (body["min_duration_min"], body["severity"], body["crop_id"]) == (
        30,
        "critical",
        crop_id,
    )
    row = (
        await db_session.execute(select(AlertRuleRow).where(AlertRuleRow.id == UUID(body["id"])))
    ).scalar_one()
    assert (row.org_id, row.code) == (org.org_id, "orchard_heat")


async def test_creating_an_alert_rule_as_producer_is_403(db_session: AsyncSession) -> None:
    org = await _org(db_session)
    client = _client()

    response = client.post(
        "/alert-rules", json=_rule_body(org), headers=_auth(org.tokens["producer"])
    )

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"
    assert (
        await db_session.execute(select(AlertRuleRow).where(AlertRuleRow.code == "orchard_heat"))
    ).scalars().all() == []


async def test_creating_an_alert_rule_in_a_foreign_org_is_404(db_session: AsyncSession) -> None:
    org = await _org(db_session)
    other = await _org(db_session)
    client = _client()

    response = client.post(
        "/alert-rules", json=_rule_body(org), headers=_auth(other.tokens["owner"])
    )

    assert response.status_code == 404
    assert response.json()["title"] == "Organization not found"


async def test_creating_an_alert_rule_without_a_threshold_is_422(db_session: AsyncSession) -> None:
    """Only the factory `water_stress` rule has no threshold (docs/03 `alert_rule`)."""
    org = await _org(db_session)
    body = _rule_body(org)
    del body["threshold"]
    client = _client()

    response = client.post("/alert-rules", json=body, headers=_auth(org.tokens["owner"]))

    assert response.status_code == 422


async def test_creating_an_alert_rule_with_an_unknown_crop_is_422(
    db_session: AsyncSession,
) -> None:
    org = await _org(db_session)
    client = _client()

    response = client.post(
        "/alert-rules", json=_rule_body(org, crop_id=999_999), headers=_auth(org.tokens["owner"])
    )

    assert response.status_code == 422
    assert response.json()["title"] == "Invalid alert rule"


async def test_owner_patches_its_own_alert_rule(db_session: AsyncSession) -> None:
    org = await _org(db_session)
    client = _client()
    rule_id = client.post(
        "/alert-rules", json=_rule_body(org), headers=_auth(org.tokens["owner"])
    ).json()["id"]

    response = client.patch(
        f"/alert-rules/{rule_id}",
        json={"threshold": 36.5, "hysteresis": 2.0, "min_duration_min": 45, "severity": "warning"},
        headers=_auth(org.tokens["owner"]),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["threshold"], body["hysteresis"]) == (36.5, 2.0)
    assert (body["min_duration_min"], body["severity"]) == (45, "warning")
    # The rest of the rule is untouched.
    assert (body["code"], body["metric"], body["operator"]) == ("orchard_heat", "air_temp", ">")


async def test_patching_a_factory_rule_is_404(db_session: AsyncSession) -> None:
    """D11: factory rules are read-only for orgs."""
    org = await _org(db_session)
    factory_rule_id = (
        await db_session.execute(
            select(AlertRuleRow.id).where(AlertRuleRow.org_id.is_(None)).limit(1)
        )
    ).scalar_one()
    client = _client()

    response = client.patch(
        f"/alert-rules/{factory_rule_id}",
        json={"threshold": 1.0},
        headers=_auth(org.tokens["owner"]),
    )

    assert response.status_code == 404
    assert response.json()["title"] == "Alert rule not found"


async def test_patching_another_orgs_alert_rule_is_404(db_session: AsyncSession) -> None:
    org = await _org(db_session)
    other = await _org(db_session)
    client = _client()
    rule_id = client.post(
        "/alert-rules", json=_rule_body(org), headers=_auth(org.tokens["owner"])
    ).json()["id"]

    response = client.patch(
        f"/alert-rules/{rule_id}",
        json={"threshold": 40.0},
        headers=_auth(other.tokens["owner"]),
    )

    assert response.status_code == 404
    assert response.json()["title"] == "Alert rule not found"


async def test_patching_an_alert_rule_as_producer_is_403(db_session: AsyncSession) -> None:
    org = await _org(db_session)
    client = _client()
    rule_id = client.post(
        "/alert-rules", json=_rule_body(org), headers=_auth(org.tokens["owner"])
    ).json()["id"]

    response = client.patch(
        f"/alert-rules/{rule_id}", json={"threshold": 40.0}, headers=_auth(org.tokens["producer"])
    )

    assert response.status_code == 403
    unchanged = (
        await db_session.execute(select(AlertRuleRow).where(AlertRuleRow.id == UUID(rule_id)))
    ).scalar_one()
    assert float(unchanged.threshold) == 34.0


async def test_patching_an_alert_rule_to_null_is_422(db_session: AsyncSession) -> None:
    """An explicit `null` on a field the rule needs is a client error, like
    the farms PATCH routes."""
    org = await _org(db_session)
    client = _client()
    rule_id = client.post(
        "/alert-rules", json=_rule_body(org), headers=_auth(org.tokens["owner"])
    ).json()["id"]

    response = client.patch(
        f"/alert-rules/{rule_id}", json={"threshold": None}, headers=_auth(org.tokens["owner"])
    )

    assert response.status_code == 422
    assert response.json()["title"] == "threshold cannot be null"
