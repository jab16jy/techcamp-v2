"""E4 T8: the simulator's HTTP flow (dev OTP login, node claim, ensuring a
calibration per sensor) against the real FastAPI app over an in-process ASGI
transport (docs/06-diseno-detallado.md §10)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import count
from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.adapters.security.otp_store import otp_store
from techcamp.main import app
from techcamp.shared.ids import uuid7
from techcamp.simulator.node_client import claim_node, ensure_calibrations, request_otp, verify_otp
from techcamp.telemetry.adapters.orm import NodeRow, SensorRow
from techcamp.telemetry.adapters.repositories import SqlAlchemyCalibrationRepository
from techcamp.telemetry.domain.models import apply_calibration

pytestmark = pytest.mark.anyio

_POINT = "POINT(-74.1 10.9)"
_BOUNDARY = "POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
_phone_seq = count()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver/api/v1"
    )


async def _member(db_session: AsyncSession) -> tuple[UUID, str]:
    org_id, user_id = uuid7(), uuid7()
    phone = f"+5730099{next(_phone_seq):05d}"
    db_session.add(OrganizationRow(id=org_id, name="Finca", kind="individual"))
    db_session.add(AppUserRow(id=user_id, phone=phone))
    await db_session.commit()
    db_session.add(MembershipRow(org_id=org_id, user_id=user_id, role="owner"))
    await db_session.commit()
    return org_id, phone


async def _make_plot(db_session: AsyncSession, org_id: UUID) -> UUID:
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
            irrigation_system="none",
        )
    )
    await db_session.commit()
    return plot_id


async def _make_unclaimed_node(db_session: AsyncSession, *, claim_code: str) -> UUID:
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
            interval_s=900,
            claimed_at=None,
            last_seen_at=None,
            status="provisioned",
        )
    )
    await db_session.commit()
    db_session.add(
        SensorRow(
            node_id=node_id, channel_key="sm_10", metric="soil_moisture", depth_cm=10, unit="%"
        )
    )
    await db_session.commit()
    return node_id


async def _login(client: httpx.AsyncClient, phone: str) -> str:
    await request_otp(client, phone)
    code = otp_store._codes[phone][0]
    return await verify_otp(client, phone, code)


async def test_request_otp_and_verify_otp_returns_a_bearer_token(db_session: AsyncSession) -> None:
    _org_id, phone = await _member(db_session)

    async with _client() as client:
        token = await _login(client, phone)

    assert token


async def test_claim_node_claims_it_into_the_plot_and_lists_its_sensor(
    db_session: AsyncSession,
) -> None:
    org_id, phone = await _member(db_session)
    plot_id = await _make_plot(db_session, org_id)
    node_id = await _make_unclaimed_node(db_session, claim_code="SIM-CLAIM-1")

    async with _client() as client:
        token = await _login(client, phone)
        node = await claim_node(client, token=token, claim_code="SIM-CLAIM-1", plot_id=plot_id)

    assert node.node_id == node_id
    assert node.mqtt_password
    assert [s["channel_key"] for s in node.sensors] == ["sm_10"]


async def test_ensure_calibrations_posts_a_working_two_point_calibration_for_a_percent_sensor(
    db_session: AsyncSession,
) -> None:
    org_id, phone = await _member(db_session)
    plot_id = await _make_plot(db_session, org_id)
    await _make_unclaimed_node(db_session, claim_code="SIM-CLAIM-2")
    now = datetime.now(UTC)

    async with _client() as client:
        token = await _login(client, phone)
        node = await claim_node(client, token=token, claim_code="SIM-CLAIM-2", plot_id=plot_id)
        await ensure_calibrations(client, token=token, node=node, valid_from=now)

    sensor_id = node.sensors[0]["id"]
    calibration = await SqlAlchemyCalibrationRepository(db_session).get_latest_valid_at(
        sensor_id, org_id, now
    )
    assert calibration is not None
    # docs/03-modelo-datos.md:466's own two-point example: raw_dry=2900 -> 5%,
    # raw_wet=1300 -> 45%.
    assert apply_calibration(calibration, 2900) == pytest.approx(5)
    assert apply_calibration(calibration, 1300) == pytest.approx(45)


async def test_ensure_calibrations_is_valid_at_the_earliest_backfill_timestamp(
    db_session: AsyncSession,
) -> None:
    """R3-calibration-valid-from-after-backfill: `valid_from` must cover the
    earliest backfilled uplink (trajectory.backfill_timestamps' first
    timestamp is `now - backfill_days`), or ingest finds no calibration for
    those readings."""
    org_id, phone = await _member(db_session)
    plot_id = await _make_plot(db_session, org_id)
    await _make_unclaimed_node(db_session, claim_code="SIM-CLAIM-3")
    now = datetime.now(UTC)
    earliest_backfill_at = now - timedelta(days=1.0)

    async with _client() as client:
        token = await _login(client, phone)
        node = await claim_node(client, token=token, claim_code="SIM-CLAIM-3", plot_id=plot_id)
        await ensure_calibrations(client, token=token, node=node, valid_from=earliest_backfill_at)

    sensor_id = node.sensors[0]["id"]
    calibration = await SqlAlchemyCalibrationRepository(db_session).get_latest_valid_at(
        sensor_id, org_id, earliest_backfill_at
    )
    assert calibration is not None
