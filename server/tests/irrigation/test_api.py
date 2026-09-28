"""Tests for irrigation REST API (E6 T5, docs/04-api.md:105-109; docs/06 §5).

`GET /api/v1/plots/{plot_id}/irrigation/recommendation?day=`
`GET /api/v1/plots/{plot_id}/water-balance?from=&to=`
Org isolation per docs/09-cuellos-de-botella.md#seguridad.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from itertools import count
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.adapters.security.token_issuer import issue_token
from techcamp.irrigation.adapters.api.deps import get_now
from techcamp.irrigation.adapters.repositories import (
    SqlAlchemyIrrigationRecommendationRepository,
    SqlAlchemyWaterBalanceRepository,
)
from techcamp.irrigation.domain.models import (
    IrrigationRecommendation,
    RainfedAdvice,
    RecommendationKind,
    WaterBalanceDay,
)
from techcamp.main import app
from techcamp.shared.dates import BOGOTA_TZ as _BOGOTA_TZ
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio
_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)

_phone_seq = count()


async def _member(
    db_session: AsyncSession, *, role: str = "owner", org_name: str = "Finca"
) -> tuple[UUID, UUID, str]:
    org_id, user_id = uuid7(), uuid7()
    db_session.add(OrganizationRow(id=org_id, name=org_name, kind="individual"))
    db_session.add(AppUserRow(id=user_id, phone=f"+5730078{next(_phone_seq):05d}"))
    await db_session.commit()
    db_session.add(MembershipRow(org_id=org_id, user_id=user_id, role=role))
    await db_session.commit()
    return org_id, user_id, issue_token(str(user_id))


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _make_plot(
    db_session: AsyncSession,
    org_id: UUID,
    *,
    name: str = "Lote 1",
    irrigation_system: str = "drip",
) -> UUID:
    farm_id = uuid7()
    db_session.add(
        FarmRow(id=farm_id, org_id=org_id, name=name, municipality_code="47001", location=_POINT)
    )
    await db_session.commit()
    plot_id = uuid7()
    efficiency = 0.9 if irrigation_system != "none" else None
    flow = 1000.0 if irrigation_system != "none" else None
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name=name,
            boundary=_BOUNDARY,
            irrigation_system=irrigation_system,
            irrigation_efficiency=efficiency,
            system_flow_lph=flow,
        )
    )
    await db_session.commit()
    return plot_id


async def test_get_recommendation_irrigate_for_day(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id, irrigation_system="drip")
    rec_repo = SqlAlchemyIrrigationRecommendationRepository(db_session)

    target_day = date(2026, 9, 25)
    rec = IrrigationRecommendation(
        kind=RecommendationKind.IRRIGATE,
        depth_mm=12.5,
        duration_min=45,
        advice=(),
        rationale={"et0_mm": 4.5, "kc": 1.15, "raw_mm": 35.0, "depletion_mm": 38.0},
    )
    await rec_repo.upsert(rec, plot_id=plot_id, day=target_day)
    await db_session.commit()

    with TestClient(app) as client:
        resp = client.get(
            f"/api/v1/plots/{plot_id}/irrigation/recommendation",
            params={"day": target_day.isoformat()},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["plot_id"] == str(plot_id)
        assert data["day"] == target_day.isoformat()
        assert data["kind"] == "irrigate"
        assert data["depth_mm"] == 12.5
        assert data["duration_min"] == 45
        assert data["advice"] == []
        assert data["rationale"]["et0_mm"] == 4.5


async def test_get_recommendation_rainfed_for_day(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id, irrigation_system="none")
    rec_repo = SqlAlchemyIrrigationRecommendationRepository(db_session)

    target_day = date(2026, 9, 25)
    rec = IrrigationRecommendation(
        kind=RecommendationKind.RAINFED,
        depth_mm=None,
        duration_min=None,
        advice=(RainfedAdvice.RAIN_EXPECTED, RainfedAdvice.CONSERVE_MOISTURE),
        rationale={"dr": 42.0, "raw": 35.0, "forecast_rain_7d_mm": 50.0},
    )
    await rec_repo.upsert(rec, plot_id=plot_id, day=target_day)
    await db_session.commit()

    with TestClient(app) as client:
        resp = client.get(
            f"/api/v1/plots/{plot_id}/irrigation/recommendation",
            params={"day": target_day.isoformat()},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["plot_id"] == str(plot_id)
        assert data["day"] == target_day.isoformat()
        assert data["kind"] == "rainfed"
        assert data["depth_mm"] is None
        assert data["duration_min"] is None
        assert data["advice"] == ["rain_expected", "conserve_moisture"]
        assert data["rationale"]["dr"] == 42.0


async def test_get_recommendation_default_day(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id, irrigation_system="drip")
    rec_repo = SqlAlchemyIrrigationRecommendationRepository(db_session)

    fixed_now = datetime(2026, 9, 27, 3, 0, tzinfo=UTC)
    today = fixed_now.astimezone(_BOGOTA_TZ).date()

    rec = IrrigationRecommendation(
        kind=RecommendationKind.NOT_NEEDED,
        depth_mm=None,
        duration_min=None,
        advice=(),
        rationale={"dr": 10.0, "raw": 30.0},
    )
    await rec_repo.upsert(rec, plot_id=plot_id, day=today)
    await db_session.commit()

    app.dependency_overrides[get_now] = lambda: fixed_now
    try:
        with TestClient(app) as client:
            resp = client.get(
                f"/api/v1/plots/{plot_id}/irrigation/recommendation",
                headers=_auth(token),
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["plot_id"] == str(plot_id)
            assert data["day"] == today.isoformat()
            assert data["kind"] == "not_needed"
    finally:
        app.dependency_overrides.pop(get_now, None)


async def test_get_recommendation_missing_day_returns_404_with_distinct_title(
    db_session: AsyncSession,
) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)

    missing_day = date(2026, 1, 1)

    with TestClient(app) as client:
        resp = client.get(
            f"/api/v1/plots/{plot_id}/irrigation/recommendation",
            params={"day": missing_day.isoformat()},
            headers=_auth(token),
        )
        assert resp.status_code == 404
        assert resp.headers["content-type"] == "application/problem+json"
        data = resp.json()
        assert data["title"] == "Recommendation not found"
        assert data["status"] == 404


def _balance(
    plot_id: UUID,
    day: date,
    *,
    etc_mm: float = 4.5,
    effective_rain_mm: float = 0.0,
    irrigation_mm: float = 0.0,
    taw_mm: float = 60.0,
    raw_mm: float = 30.0,
    depletion_model_mm: float = 15.0,
    depletion_mm: float = 15.0,
    soil_moisture_obs_pct: float | None = 25.0,
    assimilation_k: float = 0.5,
    stress_moisture_pct: float = 20.0,
) -> WaterBalanceDay:
    return WaterBalanceDay(
        plot_id=plot_id,
        day=day,
        etc_mm=etc_mm,
        effective_rain_mm=effective_rain_mm,
        irrigation_mm=irrigation_mm,
        taw_mm=taw_mm,
        raw_mm=raw_mm,
        depletion_model_mm=depletion_model_mm,
        depletion_mm=depletion_mm,
        soil_moisture_obs_pct=soil_moisture_obs_pct,
        assimilation_k=assimilation_k,
        stress_moisture_pct=stress_moisture_pct,
    )


async def test_get_water_balance_range_ordered_with_status(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_rainfed = await _make_plot(db_session, org_id, name="Rainfed", irrigation_system="none")
    # Irrigated plot: reports 'irrigate' when Dr >= RAW
    plot_irrigated = await _make_plot(
        db_session, org_id, name="Irrigated", irrigation_system="drip"
    )

    wb_repo = SqlAlchemyWaterBalanceRepository(db_session)

    d1 = date(2026, 9, 20)
    d2 = date(2026, 9, 21)
    d3 = date(2026, 9, 22)

    # For rainfed plot:
    # d1: Dr=20.0 < 0.8 * 30.0 (24.0) -> 'ok'
    # d2: 0.8 * 30 <= Dr=26.0 < 30.0 -> 'watch'
    # d3: Dr=32.0 >= 30.0 (rainfed) -> 'stress'
    # Insert in reverse order to verify sorting by day
    await wb_repo.upsert(_balance(plot_rainfed, d3, raw_mm=30.0, depletion_mm=32.0))
    await wb_repo.upsert(_balance(plot_rainfed, d1, raw_mm=30.0, depletion_mm=20.0))
    await wb_repo.upsert(_balance(plot_rainfed, d2, raw_mm=30.0, depletion_mm=26.0))

    # For irrigated plot:
    # d3: Dr=32.0 >= 30.0 (irrigated) -> 'irrigate'
    await wb_repo.upsert(_balance(plot_irrigated, d3, raw_mm=30.0, depletion_mm=32.0))
    await db_session.commit()

    with TestClient(app) as client:
        # 1. Query rainfed plot
        resp_rf = client.get(
            f"/api/v1/plots/{plot_rainfed}/water-balance",
            params={"from": d1.isoformat(), "to": d3.isoformat()},
            headers=_auth(token),
        )
        assert resp_rf.status_code == 200
        items_rf = resp_rf.json()
        assert len(items_rf) == 3
        # Check ordering and fields
        assert items_rf[0]["day"] == d1.isoformat()
        assert items_rf[0]["status"] == "ok"
        assert items_rf[0]["raw_mm"] == 30.0
        assert items_rf[0]["depletion_mm"] == 20.0

        assert items_rf[1]["day"] == d2.isoformat()
        assert items_rf[1]["status"] == "watch"
        assert items_rf[1]["depletion_mm"] == 26.0

        assert items_rf[2]["day"] == d3.isoformat()
        assert items_rf[2]["status"] == "stress"
        assert items_rf[2]["depletion_mm"] == 32.0

        # 2. Query irrigated plot
        resp_irr = client.get(
            f"/api/v1/plots/{plot_irrigated}/water-balance",
            params={"from": d3.isoformat(), "to": d3.isoformat()},
            headers=_auth(token),
        )
        assert resp_irr.status_code == 200
        items_irr = resp_irr.json()
        assert len(items_irr) == 1
        assert items_irr[0]["day"] == d3.isoformat()
        assert items_irr[0]["status"] == "irrigate"


async def test_get_water_balance_defaults(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)
    wb_repo = SqlAlchemyWaterBalanceRepository(db_session)

    fixed_now = datetime(2026, 9, 27, 3, 0, tzinfo=UTC)
    local_today = fixed_now.astimezone(_BOGOTA_TZ).date()  # 2026-09-26
    local_yesterday = local_today - timedelta(days=1)  # 2026-09-25
    default_from = local_yesterday - timedelta(days=29)  # 2026-08-27

    # Inside window
    await wb_repo.upsert(_balance(plot_id, default_from))
    await wb_repo.upsert(_balance(plot_id, local_yesterday))
    # Outside window
    await wb_repo.upsert(_balance(plot_id, default_from - timedelta(days=1)))
    await wb_repo.upsert(_balance(plot_id, local_today))
    await db_session.commit()

    app.dependency_overrides[get_now] = lambda: fixed_now
    try:
        with TestClient(app) as client:
            resp = client.get(
                f"/api/v1/plots/{plot_id}/water-balance",
                headers=_auth(token),
            )
            assert resp.status_code == 200
            days = [item["day"] for item in resp.json()]
            assert days == [default_from.isoformat(), local_yesterday.isoformat()]
    finally:
        app.dependency_overrides.pop(get_now, None)


async def test_get_water_balance_validation_errors(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)

    with TestClient(app) as client:
        # Case 1: from > to -> 422
        resp_inverted = client.get(
            f"/api/v1/plots/{plot_id}/water-balance",
            params={"from": "2026-09-25", "to": "2026-09-20"},
            headers=_auth(token),
        )
        assert resp_inverted.status_code == 422
        assert resp_inverted.headers["content-type"] == "application/problem+json"
        assert resp_inverted.json()["title"] == "Invalid date range"

        # Case 2: range > 366 days (367 inclusive days -> (to - from).days == 366 > 365) -> 422
        resp_too_wide = client.get(
            f"/api/v1/plots/{plot_id}/water-balance",
            params={"from": "2025-01-01", "to": "2026-01-02"},
            headers=_auth(token),
        )
        assert resp_too_wide.status_code == 422
        assert resp_too_wide.headers["content-type"] == "application/problem+json"
        assert resp_too_wide.json()["title"] == "Invalid date range"

        # Case 3: range == 366 days (366 inclusive days -> (to - from).days == 365 <= 365) -> 200
        resp_ok = client.get(
            f"/api/v1/plots/{plot_id}/water-balance",
            params={"from": "2025-01-01", "to": "2026-01-01"},
            headers=_auth(token),
        )
        assert resp_ok.status_code == 200
        assert resp_ok.json() == []


async def test_irrigation_endpoints_cross_org_returns_404(db_session: AsyncSession) -> None:
    _org_a, _user_a, token_a = await _member(db_session, role="owner", org_name="Finca A")
    org_b, _user_b, _token_b = await _member(db_session, role="owner", org_name="Finca B")
    plot_b = await _make_plot(db_session, org_b)

    with TestClient(app) as client:
        # Cross-org recommendation -> 404
        resp_rec = client.get(
            f"/api/v1/plots/{plot_b}/irrigation/recommendation",
            headers=_auth(token_a),
        )
        assert resp_rec.status_code == 404
        assert resp_rec.headers["content-type"] == "application/problem+json"
        assert resp_rec.json()["title"] == "Plot not found"

        # Cross-org water-balance -> 404
        resp_wb = client.get(
            f"/api/v1/plots/{plot_b}/water-balance",
            headers=_auth(token_a),
        )
        assert resp_wb.status_code == 404
        assert resp_wb.headers["content-type"] == "application/problem+json"
        assert resp_wb.json()["title"] == "Plot not found"

        # Unknown plot UUID -> 404
        unknown_id = uuid7()
        resp_unknown_rec = client.get(
            f"/api/v1/plots/{unknown_id}/irrigation/recommendation",
            headers=_auth(token_a),
        )
        assert resp_unknown_rec.status_code == 404
        assert resp_unknown_rec.json()["title"] == "Plot not found"

        resp_unknown_wb = client.get(
            f"/api/v1/plots/{unknown_id}/water-balance",
            headers=_auth(token_a),
        )
        assert resp_unknown_wb.status_code == 404
        assert resp_unknown_wb.json()["title"] == "Plot not found"


async def test_irrigation_endpoints_unauthenticated_returns_401(db_session: AsyncSession) -> None:
    org_id = uuid7()
    db_session.add(OrganizationRow(id=org_id, name="Finca", kind="individual"))
    await db_session.commit()
    plot_id = await _make_plot(db_session, org_id)

    with TestClient(app) as client:
        resp_rec = client.get(f"/api/v1/plots/{plot_id}/irrigation/recommendation")
        assert resp_rec.status_code == 401

        resp_wb = client.get(f"/api/v1/plots/{plot_id}/water-balance")
        assert resp_wb.status_code == 401
