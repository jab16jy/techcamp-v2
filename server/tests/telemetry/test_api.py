"""Node, sensor and calibration endpoints (docs/04-api.md:79-93;
docs/06-diseno-detallado.md §2).

Org isolation per docs/09-cuellos-de-botella.md#seguridad: a resource in
another organization responds 404, never 403.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from itertools import count
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.adapters.security.token_issuer import issue_token
from techcamp.main import app
from techcamp.shared.credentials import hash_password, verify_password
from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.orm import CalibrationRow, NodeRow, ReadingRow, SensorRow
from techcamp.telemetry.adapters.repositories import (
    SqlAlchemyCalibrationRepository,
    SqlAlchemyNodeRepository,
)

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)

# A counter, not `uuid7().int % 100000`: the modulo could collide on the
# unique `phone` column between two `uuid7()`s generated close together
# (GitHub issue #21, same reasoning as farms/test_api.py).
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


async def _make_plot(db_session: AsyncSession, org_id: object, *, name: str = "Lote 1") -> UUID:
    farm_id = uuid7()
    db_session.add(
        FarmRow(id=farm_id, org_id=org_id, name=name, municipality_code="47001", location=_POINT)
    )
    await db_session.commit()
    plot_id = uuid7()
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name=name,
            boundary=_BOUNDARY,
            irrigation_system="none",
        )
    )
    await db_session.commit()
    return plot_id


async def _make_unclaimed_node(
    db_session: AsyncSession, *, claim_code: str, interval_s: int = 300
) -> UUID:
    node_id = uuid7()
    db_session.add(
        NodeRow(
            id=node_id,
            org_id=None,
            plot_id=None,
            transport="wifi",
            dev_eui=None,
            claim_code=claim_code,
            credential_hash="unclaimed",
            firmware=None,
            interval_s=interval_s,
            claimed_at=None,
            last_seen_at=None,
            status="provisioned",
        )
    )
    await db_session.commit()
    return node_id


async def _claim_node(
    db_session: AsyncSession,
    org_id: object,
    plot_id: object,
    *,
    claim_code: str,
    interval_s: int = 300,
    status: str = "provisioned",
) -> UUID:
    node_id = uuid7()
    db_session.add(
        NodeRow(
            id=node_id,
            org_id=org_id,
            plot_id=plot_id,
            transport="wifi",
            dev_eui=None,
            claim_code=claim_code,
            credential_hash=hash_password("seed-password"),
            firmware=None,
            interval_s=interval_s,
            claimed_at=datetime(2026, 1, 1, tzinfo=UTC),
            last_seen_at=None,
            status=status,
        )
    )
    await db_session.commit()
    return node_id


async def _make_sensor(
    db_session: AsyncSession, node_id: object, *, channel_key: str = "sm_10"
) -> int:
    sensor = SensorRow(
        node_id=node_id, channel_key=channel_key, metric="soil_moisture", depth_cm=10, unit="pct"
    )
    db_session.add(sensor)
    await db_session.commit()
    await db_session.refresh(sensor)
    return sensor.id


def _client() -> TestClient:
    return TestClient(app, base_url="http://testserver/api/v1")


def _async_client() -> httpx.AsyncClient:
    """For the tests that need two requests genuinely in flight at once.
    `raise_app_exceptions=False` turns an unhandled server error into the 500
    the client would really see, instead of raising it into the test."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver/api/v1",
    )


def _hold_two_reads(monkeypatch: pytest.MonkeyPatch, repository: type, method_name: str) -> None:
    """Hold two concurrent requests at `method_name` until both have read, so
    the second one loses the race the request is about to make. Without it the
    two requests would usually be serialized by the event loop and the test
    would prove nothing about the race."""
    original = getattr(repository, method_name)
    arrived = 0
    gate = asyncio.Event()

    async def _racing(self: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal arrived
        value = await original(self, *args, **kwargs)
        arrived += 1
        if arrived == 2:
            gate.set()
        await asyncio.wait_for(gate.wait(), timeout=5)
        return value

    monkeypatch.setattr(repository, method_name, _racing)


async def test_owner_claims_a_node(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)
    node_id = await _make_unclaimed_node(db_session, claim_code="CLAIM1")
    client = _client()

    response = client.post(
        "/nodes:claim",
        json={"claim_code": "CLAIM1", "plot_id": str(plot_id)},
        headers=_auth(token),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"] == str(node_id)
    assert body["org_id"] == str(org_id)
    assert body["plot_id"] == str(plot_id)
    assert body["status"] == "provisioned"
    assert body["mqtt"]["username"] == str(node_id)
    password = body["mqtt"]["password"]
    assert password

    stored = await db_session.get(NodeRow, node_id)
    assert stored is not None
    assert stored.credential_hash != password
    assert verify_password(password, stored.credential_hash)


async def test_viewer_cannot_claim_a_node(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="viewer")
    plot_id = await _make_plot(db_session, org_id)
    await _make_unclaimed_node(db_session, claim_code="CLAIM2")
    client = _client()

    response = client.post(
        "/nodes:claim",
        json={"claim_code": "CLAIM2", "plot_id": str(plot_id)},
        headers=_auth(token),
    )

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"


async def test_claiming_onto_a_foreign_plot_is_404(db_session: AsyncSession) -> None:
    _org_a, _user_a, token_a = await _member(db_session, role="owner", org_name="Finca A")
    org_b, _user_b, _token_b = await _member(db_session, role="owner", org_name="Finca B")
    foreign_plot_id = await _make_plot(db_session, org_b)
    await _make_unclaimed_node(db_session, claim_code="CLAIM3")
    client = _client()

    response = client.post(
        "/nodes:claim",
        json={"claim_code": "CLAIM3", "plot_id": str(foreign_plot_id)},
        headers=_auth(token_a),
    )

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


async def test_claiming_an_unknown_code_is_404(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)
    client = _client()

    response = client.post(
        "/nodes:claim", json={"claim_code": "NOPE", "plot_id": str(plot_id)}, headers=_auth(token)
    )

    assert response.status_code == 404


async def test_claiming_an_already_claimed_node_twice_is_409_the_second_time(
    db_session: AsyncSession,
) -> None:
    """Also proves the replay case for the deliberately-deferred
    `Idempotency-Key` (docs/04-api.md conventions, farms/adapters/api/router.py):
    a byte-identical replay of `POST /nodes:claim` never silently double-claims."""
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)
    await _make_unclaimed_node(db_session, claim_code="CLAIM4")
    client = _client()
    payload = {"claim_code": "CLAIM4", "plot_id": str(plot_id)}

    first = client.post("/nodes:claim", json=payload, headers=_auth(token))
    second = client.post("/nodes:claim", json=payload, headers=_auth(token))

    assert first.status_code == 201, first.text
    assert second.status_code == 409
    assert second.headers["content-type"] == "application/problem+json"


async def test_list_nodes_only_returns_the_callers_org(db_session: AsyncSession) -> None:
    org_a, _user_a, token_a = await _member(db_session, role="owner", org_name="Finca A")
    org_b, _user_b, _token_b = await _member(db_session, role="owner", org_name="Finca B")
    plot_a = await _make_plot(db_session, org_a)
    plot_b = await _make_plot(db_session, org_b)
    await _claim_node(db_session, org_a, plot_a, claim_code="LISTA")
    await _claim_node(db_session, org_b, plot_b, claim_code="LISTB")
    client = _client()

    response = client.get(f"/nodes?org_id={org_a}", headers=_auth(token_a))

    assert response.status_code == 200
    org_ids = {item["org_id"] for item in response.json()["items"]}
    assert org_ids == {str(org_a)}


async def test_listing_nodes_of_a_foreign_org_is_404(db_session: AsyncSession) -> None:
    _org_a, _user_a, token_a = await _member(db_session, role="owner", org_name="Finca A")
    org_b, _user_b, _token_b = await _member(db_session, role="owner", org_name="Finca B")
    client = _client()

    response = client.get(f"/nodes?org_id={org_b}", headers=_auth(token_a))

    assert response.status_code == 404


async def test_list_nodes_pages_by_cursor_and_stops_on_the_last_page(
    db_session: AsyncSession,
) -> None:
    """docs/04-api.md:18: `?limit=&cursor=` → `{ items, next_cursor }`. A full
    page carries a `next_cursor`; the last, short page must not."""
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)
    first = await _claim_node(db_session, org_id, plot_id, claim_code="PAGE1")
    second = await _claim_node(db_session, org_id, plot_id, claim_code="PAGE2")
    third = await _claim_node(db_session, org_id, plot_id, claim_code="PAGE3")
    client = _client()
    auth = _auth(token)

    first_page = client.get(f"/nodes?org_id={org_id}&limit=2", headers=auth)

    assert first_page.status_code == 200, first_page.text
    body = first_page.json()
    assert [item["id"] for item in body["items"]] == [str(first), str(second)]
    assert body["next_cursor"] == str(second)

    second_page = client.get(
        f"/nodes?org_id={org_id}&limit=2&cursor={body['next_cursor']}", headers=auth
    )

    assert second_page.status_code == 200
    last = second_page.json()
    assert [item["id"] for item in last["items"]] == [str(third)]
    assert last["next_cursor"] is None


async def test_list_nodes_rejects_a_limit_outside_the_documented_range(
    db_session: AsyncSession,
) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    client = _client()

    assert client.get(f"/nodes?org_id={org_id}&limit=0", headers=_auth(token)).status_code == 422
    assert client.get(f"/nodes?org_id={org_id}&limit=201", headers=_auth(token)).status_code == 422


async def test_list_nodes_filters_by_plot_and_by_status(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_a = await _make_plot(db_session, org_id, name="Lote A")
    plot_b = await _make_plot(db_session, org_id, name="Lote B")
    on_a = await _claim_node(db_session, org_id, plot_a, claim_code="FILTER1")
    offline_b = await _claim_node(
        db_session, org_id, plot_b, claim_code="FILTER2", status="offline"
    )
    client = _client()
    auth = _auth(token)

    by_plot = client.get(f"/nodes?org_id={org_id}&plot_id={plot_a}", headers=auth)
    by_status = client.get(f"/nodes?org_id={org_id}&status=offline", headers=auth)
    # Both filters together can legitimately match nothing.
    by_both = client.get(f"/nodes?org_id={org_id}&plot_id={plot_b}&status=online", headers=auth)

    assert [item["id"] for item in by_plot.json()["items"]] == [str(on_a)]
    assert [item["id"] for item in by_status.json()["items"]] == [str(offline_b)]
    assert by_both.status_code == 200
    assert by_both.json()["items"] == []


async def test_owner_patches_a_nodes_status(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)
    node_id = await _claim_node(db_session, org_id, plot_id, claim_code="PATCH1")
    client = _client()

    response = client.patch(f"/nodes/{node_id}", json={"status": "retired"}, headers=_auth(token))

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "retired"


async def test_patching_a_node_with_an_explicit_null_is_422(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)
    node_id = await _claim_node(db_session, org_id, plot_id, claim_code="PATCH2")
    client = _client()

    response = client.patch(f"/nodes/{node_id}", json={"status": None}, headers=_auth(token))

    assert response.status_code == 422


async def test_patching_a_node_onto_a_foreign_plot_is_422(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    other_org, _other_user, _other_token = await _member(db_session, role="owner", org_name="Otra")
    plot_id = await _make_plot(db_session, org_id)
    foreign_plot_id = await _make_plot(db_session, other_org)
    node_id = await _claim_node(db_session, org_id, plot_id, claim_code="PATCH3")
    client = _client()

    response = client.patch(
        f"/nodes/{node_id}", json={"plot_id": str(foreign_plot_id)}, headers=_auth(token)
    )

    assert response.status_code == 422


async def test_patching_a_node_of_a_foreign_org_is_404(db_session: AsyncSession) -> None:
    org_a, _user_a, _token_a = await _member(db_session, role="owner", org_name="Finca A")
    org_b, _user_b, token_b = await _member(db_session, role="owner", org_name="Finca B")
    plot_a = await _make_plot(db_session, org_a)
    node_id = await _claim_node(db_session, org_a, plot_a, claim_code="PATCH4")
    client = _client()

    response = client.patch(f"/nodes/{node_id}", json={"status": "offline"}, headers=_auth(token_b))

    assert response.status_code == 404


async def test_rotating_credentials_invalidates_the_previous_hash(
    db_session: AsyncSession,
) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)
    node_id = await _claim_node(db_session, org_id, plot_id, claim_code="ROT1")
    before = await db_session.get(NodeRow, node_id)
    assert before is not None
    original_hash = before.credential_hash
    client = _client()

    response = client.post(f"/nodes/{node_id}/credentials:rotate", headers=_auth(token))

    assert response.status_code == 200, response.text
    new_password = response.json()["password"]
    await db_session.refresh(before)
    assert before.credential_hash != original_hash
    assert verify_password(new_password, before.credential_hash)
    assert not verify_password("seed-password", before.credential_hash)


@pytest.mark.parametrize("role", ["viewer", "producer"])
async def test_read_only_roles_are_refused_every_node_and_calibration_write(
    db_session: AsyncSession, role: str
) -> None:
    """`WRITE_ROLES` (telemetry/domain/models.py) is owner + technician, so
    dropping an `ensure_can_write` from any of these three endpoints has to fail
    here — and leave nothing written."""
    org_id, _user_id, token = await _member(db_session, role=role)
    plot_id = await _make_plot(db_session, org_id)
    node_id = await _claim_node(db_session, org_id, plot_id, claim_code=f"ROLE{role}")
    sensor_id = await _make_sensor(db_session, node_id)
    client = _client()
    auth = _auth(token)
    before = await db_session.get(NodeRow, node_id)
    assert before is not None
    original_hash = before.credential_hash

    patch = client.patch(f"/nodes/{node_id}", json={"status": "retired"}, headers=auth)
    rotate = client.post(f"/nodes/{node_id}/credentials:rotate", headers=auth)
    calibrate = client.post(
        f"/sensors/{sensor_id}/calibrations",
        json={
            "method": "linear",
            "kind": "field",
            "params": {"scale": 0.1, "offset": 2.0},
            "valid_from": "2026-01-01T00:00:00Z",
        },
        headers=auth,
    )

    assert patch.status_code == 403
    assert rotate.status_code == 403
    assert calibrate.status_code == 403
    assert patch.headers["content-type"] == "application/problem+json"

    await db_session.refresh(before)
    assert before.status == "provisioned"
    assert before.credential_hash == original_hash
    stored = await db_session.execute(
        select(CalibrationRow).where(CalibrationRow.sensor_id == sensor_id)
    )
    assert stored.scalars().all() == []


async def test_node_health_reports_completeness_from_readings(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)
    node_id = await _claim_node(db_session, org_id, plot_id, claim_code="HEALTH1", interval_s=3600)
    sensor_id = await _make_sensor(db_session, node_id)
    now = datetime.now(UTC)
    for hours_ago in range(6):
        db_session.add(
            ReadingRow(
                time=now - timedelta(hours=hours_ago),
                sensor_id=sensor_id,
                raw_value=1.0,
                value=1.0,
                received_at=now,
                quality=0,
            )
        )
    await db_session.commit()
    client = _client()

    response = client.get(f"/nodes/{node_id}/health", headers=_auth(token))

    assert response.status_code == 200, response.text
    body = response.json()
    # 6 hourly readings in a 24h window with a 3600s interval expects 24.
    assert body["completeness_24h"] == pytest.approx(6 / 24)
    assert body["battery_v"] is None
    assert body["rssi"] is None


async def test_health_of_a_foreign_org_node_is_404(db_session: AsyncSession) -> None:
    org_a, _user_a, _token_a = await _member(db_session, role="owner", org_name="Finca A")
    org_b, _user_b, token_b = await _member(db_session, role="owner", org_name="Finca B")
    plot_a = await _make_plot(db_session, org_a)
    node_id = await _claim_node(db_session, org_a, plot_a, claim_code="HEALTH2")
    client = _client()

    response = client.get(f"/nodes/{node_id}/health", headers=_auth(token_b))

    assert response.status_code == 404


async def test_get_sensors_lists_the_nodes_sensors(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)
    node_id = await _claim_node(db_session, org_id, plot_id, claim_code="SENS1")
    await _make_sensor(db_session, node_id, channel_key="sm_10")
    await _make_sensor(db_session, node_id, channel_key="temp")
    client = _client()

    response = client.get(f"/nodes/{node_id}/sensors", headers=_auth(token))

    assert response.status_code == 200
    assert sorted(s["channel_key"] for s in response.json()) == ["sm_10", "temp"]


async def test_creating_a_linear_calibration(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)
    node_id = await _claim_node(db_session, org_id, plot_id, claim_code="CAL1")
    sensor_id = await _make_sensor(db_session, node_id)
    client = _client()

    response = client.post(
        f"/sensors/{sensor_id}/calibrations",
        json={
            "method": "linear",
            "kind": "field",
            "params": {"scale": 0.1, "offset": 2.0},
            "valid_from": "2026-01-01T00:00:00Z",
        },
        headers=_auth(token),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["version"] == 1
    assert body["sensor_id"] == sensor_id


async def test_a_second_calibration_gets_the_next_version(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)
    node_id = await _claim_node(db_session, org_id, plot_id, claim_code="CAL2")
    sensor_id = await _make_sensor(db_session, node_id)
    client = _client()
    body = {
        "method": "linear",
        "kind": "field",
        "params": {"scale": 0.1, "offset": 2.0},
        "valid_from": "2026-01-01T00:00:00Z",
    }

    first = client.post(f"/sensors/{sensor_id}/calibrations", json=body, headers=_auth(token))
    second = client.post(f"/sensors/{sensor_id}/calibrations", json=body, headers=_auth(token))

    assert first.json()["version"] == 1
    assert second.json()["version"] == 2


async def test_calibration_with_invalid_params_is_422(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)
    node_id = await _claim_node(db_session, org_id, plot_id, claim_code="CAL3")
    sensor_id = await _make_sensor(db_session, node_id)
    client = _client()

    response = client.post(
        f"/sensors/{sensor_id}/calibrations",
        json={
            "method": "linear",
            "kind": "field",
            "params": {"scale": 0.1},
            "valid_from": "2026-01-01T00:00:00Z",
        },
        headers=_auth(token),
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.parametrize(
    ("method", "params"),
    [
        pytest.param("linear", {"scale": "one", "offset": 2.0}, id="string-scale"),
        pytest.param("linear", {"scale": 10**400, "offset": 2.0}, id="scale-too-large-for-a-float"),
        pytest.param(
            "two_point",
            {"raw_dry": 2900, "raw_wet": 2900, "vwc_dry": 5, "vwc_wet": 45},
            id="zero-divisor",
        ),
        pytest.param("polynomial", {"coeffs": [1, "two"]}, id="non-numeric-coeff"),
    ],
)
async def test_calibration_params_of_the_wrong_type_are_422(
    db_session: AsyncSession, method: str, params: dict[str, Any]
) -> None:
    """A coefficient the method's arithmetic can't use is a client error, not a
    crash: a `float()` overflow used to escape the `raw=0.0` probe as a 500."""
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)
    node_id = await _claim_node(db_session, org_id, plot_id, claim_code="CALPARAMS")
    sensor_id = await _make_sensor(db_session, node_id)
    client = _client()

    response = client.post(
        f"/sensors/{sensor_id}/calibrations",
        json={
            "method": method,
            "kind": "field",
            "params": params,
            "valid_from": "2026-01-01T00:00:00Z",
        },
        headers=_auth(token),
    )

    assert response.status_code == 422, response.text
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.parametrize("coefficient", ["NaN", "Infinity"], ids=["nan", "infinity"])
async def test_calibration_params_that_are_not_finite_are_422(
    db_session: AsyncSession, coefficient: str
) -> None:
    """A raw body, because `NaN`/`Infinity` are what such a payload actually
    looks like on the wire (Python's JSON reader accepts them) and what the
    `params` JSONB column refuses to store."""
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)
    node_id = await _claim_node(db_session, org_id, plot_id, claim_code="CALFINITE")
    sensor_id = await _make_sensor(db_session, node_id)
    client = _client()

    response = client.post(
        f"/sensors/{sensor_id}/calibrations",
        content=(
            '{"method": "linear", "kind": "field",'
            f' "params": {{"scale": {coefficient}, "offset": 2.0}},'
            ' "valid_from": "2026-01-01T00:00:00Z"}'
        ),
        headers={**_auth(token), "Content-Type": "application/json"},
    )

    assert response.status_code == 422, response.text
    assert response.headers["content-type"] == "application/problem+json"


async def test_concurrent_calibrations_of_one_sensor_leave_a_single_version(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`MAX(version) + 1` and the insert are separate statements, so two POSTs
    can read the same version (docs/03-modelo-datos.md:461: calibration is
    versioned). The client retries the loser, which is a 409, not a 500."""
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)
    node_id = await _claim_node(db_session, org_id, plot_id, claim_code="CALTWO")
    sensor_id = await _make_sensor(db_session, node_id)
    _hold_two_reads(monkeypatch, SqlAlchemyCalibrationRepository, "next_version")
    body = {
        "method": "linear",
        "kind": "field",
        "params": {"scale": 0.1, "offset": 2.0},
        "valid_from": "2026-01-01T00:00:00Z",
    }

    async with _async_client() as client:
        responses = await asyncio.gather(
            client.post(f"/sensors/{sensor_id}/calibrations", json=body, headers=_auth(token)),
            client.post(f"/sensors/{sensor_id}/calibrations", json=body, headers=_auth(token)),
        )

    assert sorted(r.status_code for r in responses) == [201, 409]
    conflict = next(r for r in responses if r.status_code == 409)
    assert conflict.headers["content-type"] == "application/problem+json"
    assert conflict.json()["type"] == "calibration_version_conflict"
    stored = await db_session.execute(
        select(CalibrationRow).where(CalibrationRow.sensor_id == sensor_id)
    )
    rows = stored.scalars().all()
    assert [row.version for row in rows] == [1]


async def test_a_lost_claim_race_is_409(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two claims of one `claim_code` both pass the unclaimed check, then only
    one guarded UPDATE matches a row: the other must surface the lost race as a
    409 instead of reporting a claim that never happened."""
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)
    node_id = await _make_unclaimed_node(db_session, claim_code="RACE1")
    _hold_two_reads(monkeypatch, SqlAlchemyNodeRepository, "get_by_claim_code")
    body = {"claim_code": "RACE1", "plot_id": str(plot_id)}

    async with _async_client() as client:
        responses = await asyncio.gather(
            client.post("/nodes:claim", json=body, headers=_auth(token)),
            client.post("/nodes:claim", json=body, headers=_auth(token)),
        )

    assert sorted(r.status_code for r in responses) == [201, 409]
    stored = await db_session.get(NodeRow, node_id)
    assert stored is not None
    assert stored.org_id == org_id


async def test_calibrating_a_sensor_of_a_foreign_org_is_404(db_session: AsyncSession) -> None:
    org_a, _user_a, _token_a = await _member(db_session, role="owner", org_name="Finca A")
    org_b, _user_b, token_b = await _member(db_session, role="owner", org_name="Finca B")
    plot_a = await _make_plot(db_session, org_a)
    node_id = await _claim_node(db_session, org_a, plot_a, claim_code="CAL4")
    sensor_id = await _make_sensor(db_session, node_id)
    client = _client()

    response = client.post(
        f"/sensors/{sensor_id}/calibrations",
        json={
            "method": "linear",
            "kind": "field",
            "params": {"scale": 0.1, "offset": 2.0},
            "valid_from": "2026-01-01T00:00:00Z",
        },
        headers=_auth(token_b),
    )

    assert response.status_code == 404


async def test_org_isolation_covers_every_node_and_sensor_endpoint(
    db_session: AsyncSession,
) -> None:
    """docs/09-cuellos-de-botella.md#seguridad: another org gets 404 on every
    node/sensor endpoint, and cannot claim a node onto a plot it doesn't own."""
    org_a, _user_a, _token_a = await _member(db_session, role="owner", org_name="Finca A")
    org_b, _user_b, token_b = await _member(db_session, role="owner", org_name="Finca B")
    plot_a = await _make_plot(db_session, org_a)
    node_id = await _claim_node(db_session, org_a, plot_a, claim_code="ISO1")
    sensor_id = await _make_sensor(db_session, node_id)
    db_session.add(
        CalibrationRow(
            id=uuid7(),
            sensor_id=sensor_id,
            version=1,
            method="linear",
            kind="field",
            params={"scale": 1, "offset": 0},
            rmse_pct=None,
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    await db_session.commit()
    client = _client()
    auth_b = _auth(token_b)

    assert client.get(f"/nodes?org_id={org_a}", headers=auth_b).status_code == 404
    assert (
        client.patch(f"/nodes/{node_id}", json={"status": "offline"}, headers=auth_b).status_code
        == 404
    )
    assert client.post(f"/nodes/{node_id}/credentials:rotate", headers=auth_b).status_code == 404
    assert client.get(f"/nodes/{node_id}/health", headers=auth_b).status_code == 404
    assert client.get(f"/nodes/{node_id}/sensors", headers=auth_b).status_code == 404
    assert (
        client.post(
            f"/sensors/{sensor_id}/calibrations",
            json={
                "method": "linear",
                "kind": "field",
                "params": {"scale": 1, "offset": 0},
                "valid_from": "2026-01-01T00:00:00Z",
            },
            headers=auth_b,
        ).status_code
        == 404
    )
    unclaimed = await _make_unclaimed_node(db_session, claim_code="ISO2")
    claim_response = client.post(
        "/nodes:claim", json={"claim_code": "ISO2", "plot_id": str(plot_a)}, headers=auth_b
    )
    assert claim_response.status_code == 404
    still_unclaimed = await db_session.get(NodeRow, unclaimed)
    assert still_unclaimed is not None
    assert still_unclaimed.org_id is None
