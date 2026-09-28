"""Weather API endpoints (docs/04-api.md:94; docs/06 §6).

`GET /api/v1/plots/{plot_id}/weather?days=`.
Org isolation per docs/09-cuellos-de-botella.md#seguridad: a plot in another
organization responds 404, never 403.
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
from techcamp.main import app
from techcamp.shared.dates import BOGOTA_TZ as _BOGOTA_TZ
from techcamp.shared.ids import uuid7
from techcamp.weather.adapters.api.deps import get_now
from techcamp.weather.adapters.repositories import SqlAlchemyWeatherRepository
from techcamp.weather.domain.models import WeatherDay

pytestmark = pytest.mark.anyio
_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)

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


async def _make_plot(
    db_session: AsyncSession,
    org_id: UUID,
    *,
    name: str = "Lote 1",
    weather_cell_id: int | None = None,
) -> UUID:
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
            weather_cell_id=weather_cell_id,
        )
    )
    await db_session.commit()
    return plot_id


def _day(
    day: date,
    *,
    is_forecast: bool,
    et0_mm: float | None = 4.5,
    rain_mm: float | None = 0.0,
    tmin_c: float | None = 22.0,
    tmax_c: float | None = 32.0,
    rh_mean_pct: float | None = 75.0,
    fetched_at: datetime,
) -> WeatherDay:
    return WeatherDay(
        day=day,
        is_forecast=is_forecast,
        et0_mm=et0_mm,
        rain_mm=rain_mm,
        tmin_c=tmin_c,
        tmax_c=tmax_c,
        rh_mean_pct=rh_mean_pct,
        fetched_at=fetched_at,
    )


async def test_weather_shape_ordering_and_days_window(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    weather_repo = SqlAlchemyWeatherRepository(db_session)
    cell_id = await weather_repo.get_or_create_cell(10.9, -74.1)
    plot_id = await _make_plot(db_session, org_id, weather_cell_id=cell_id)

    fixed_now = datetime(2026, 9, 26, 15, 0, tzinfo=UTC)
    today = fixed_now.astimezone(_BOGOTA_TZ).date()  # 2026-09-26
    recent_fetch = fixed_now - timedelta(hours=2)

    # Populate:
    # Observed: today-8 to today (today-8 excluded when days=7; today as observed excluded)
    observed_days = [
        _day(
            today - timedelta(days=offset),
            is_forecast=False,
            et0_mm=4.0 + offset,
            fetched_at=recent_fetch,
        )
        for offset in range(9)  # 0 to 8
    ]
    # Forecast: today-1 to today+8 (today-1 forecast excluded; today+7..8 excluded when days=7)
    forecast_days = [
        _day(
            today + timedelta(days=offset),
            is_forecast=True,
            et0_mm=5.0 + offset,
            fetched_at=recent_fetch,
        )
        for offset in range(-1, 9)
    ]
    await weather_repo.upsert_daily(cell_id, observed_days + forecast_days)

    app.dependency_overrides[get_now] = lambda: fixed_now
    try:
        with TestClient(app) as client:
            # Explicit days=7
            response = client.get(
                f"/api/v1/plots/{plot_id}/weather",
                params={"days": 7},
                headers=_auth(token),
            )
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 14  # 7 observed + 7 forecast

            # Check observed rows: [today-7, today-1]
            for i in range(7):
                item = data[i]
                expected_day = (today - timedelta(days=7 - i)).isoformat()
                assert item["day"] == expected_day
                assert item["is_forecast"] is False
                assert item["stale"] is False
                assert datetime.fromisoformat(item["fetched_at"]) == recent_fetch
                assert isinstance(item["et0_mm"], float)

            # Check forecast rows: [today, today+6]
            for i in range(7):
                item = data[7 + i]
                expected_day = (today + timedelta(days=i)).isoformat()
                assert item["day"] == expected_day
                assert item["is_forecast"] is True
                assert item["stale"] is False
                assert datetime.fromisoformat(item["fetched_at"]) == recent_fetch
                assert isinstance(item["et0_mm"], float)

            # Default days=7 when parameter is omitted
            default_resp = client.get(
                f"/api/v1/plots/{plot_id}/weather",
                headers=_auth(token),
            )
            assert default_resp.status_code == 200
            assert default_resp.json() == data

            # days=3 returns last 3 observed and next 3 forecast
            resp_3 = client.get(
                f"/api/v1/plots/{plot_id}/weather",
                params={"days": 3},
                headers=_auth(token),
            )
            assert resp_3.status_code == 200
            data_3 = resp_3.json()
            assert len(data_3) == 6
            assert [item["day"] for item in data_3] == [
                (today - timedelta(days=3)).isoformat(),
                (today - timedelta(days=2)).isoformat(),
                (today - timedelta(days=1)).isoformat(),
                today.isoformat(),
                (today + timedelta(days=1)).isoformat(),
                (today + timedelta(days=2)).isoformat(),
            ]
    finally:
        app.dependency_overrides.clear()


async def test_weather_days_validation_outside_1_to_16_returns_422(
    db_session: AsyncSession,
) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)

    with TestClient(app) as client:
        for bad_days in [0, 17, -1, "abc"]:
            response = client.get(
                f"/api/v1/plots/{plot_id}/weather",
                params={"days": bad_days},
                headers=_auth(token),
            )
            assert response.status_code == 422, f"Expected 422 for days={bad_days}"


async def test_weather_cross_org_returns_404(db_session: AsyncSession) -> None:
    _org_a, _user_a, token_a = await _member(db_session, role="owner", org_name="Finca A")
    org_b, _user_b, _token_b = await _member(db_session, role="owner", org_name="Finca B")
    plot_id_b = await _make_plot(db_session, org_b)

    with TestClient(app) as client:
        # Cross-org plot access -> 404
        response = client.get(
            f"/api/v1/plots/{plot_id_b}/weather",
            headers=_auth(token_a),
        )
        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"

        # Unknown plot UUID -> 404
        unknown_plot_id = uuid7()
        response_unknown = client.get(
            f"/api/v1/plots/{unknown_plot_id}/weather",
            headers=_auth(token_a),
        )
        assert response_unknown.status_code == 404


async def test_weather_stale_semantics(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    weather_repo = SqlAlchemyWeatherRepository(db_session)
    cell_id = await weather_repo.get_or_create_cell(10.9, -74.1)
    plot_id = await _make_plot(db_session, org_id, weather_cell_id=cell_id)

    fixed_now = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
    today = fixed_now.astimezone(_BOGOTA_TZ).date()

    # Case 1: fetched_at is 7 hours old (> 6 h) -> stale: True
    old_fetch = fixed_now - timedelta(hours=7)
    await weather_repo.upsert_daily(
        cell_id,
        [
            _day(today - timedelta(days=1), is_forecast=False, fetched_at=old_fetch),
            _day(today, is_forecast=True, fetched_at=old_fetch),
        ],
    )

    app.dependency_overrides[get_now] = lambda: fixed_now
    try:
        with TestClient(app) as client:
            resp = client.get(
                f"/api/v1/plots/{plot_id}/weather",
                headers=_auth(token),
            )
            assert resp.status_code == 200
            items = resp.json()
            assert len(items) == 2
            assert all(item["stale"] is True for item in items)

        # Case 2: update fetch to 3 hours old (<= 6 h) -> stale: False
        fresh_fetch = fixed_now - timedelta(hours=3)
        await weather_repo.upsert_daily(
            cell_id,
            [
                _day(today, is_forecast=True, fetched_at=fresh_fetch),
            ],
        )

        with TestClient(app) as client:
            resp = client.get(
                f"/api/v1/plots/{plot_id}/weather",
                headers=_auth(token),
            )
            assert resp.status_code == 200
            items = resp.json()
            assert len(items) == 2
            assert all(item["stale"] is False for item in items)
    finally:
        app.dependency_overrides.clear()


async def test_weather_empty_cell_or_no_rows_returns_empty_list(
    db_session: AsyncSession,
) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    weather_repo = SqlAlchemyWeatherRepository(db_session)
    cell_id = await weather_repo.get_or_create_cell(10.9, -74.1)

    # Plot 1: weather_cell_id is None
    plot_no_cell = await _make_plot(db_session, org_id, weather_cell_id=None)

    # Plot 2: weather_cell_id is set, but cell has no rows
    plot_empty_cell = await _make_plot(db_session, org_id, weather_cell_id=cell_id)

    with TestClient(app) as client:
        resp1 = client.get(f"/api/v1/plots/{plot_no_cell}/weather", headers=_auth(token))
        assert resp1.status_code == 200
        assert resp1.json() == []

        resp2 = client.get(f"/api/v1/plots/{plot_empty_cell}/weather", headers=_auth(token))
        assert resp2.status_code == 200
        assert resp2.json() == []


async def test_weather_unauthenticated_returns_401(db_session: AsyncSession) -> None:
    org_id = uuid7()
    db_session.add(OrganizationRow(id=org_id, name="Finca", kind="individual"))
    await db_session.commit()
    plot_id = await _make_plot(db_session, org_id)

    with TestClient(app) as client:
        resp = client.get(f"/api/v1/plots/{plot_id}/weather")
        assert resp.status_code == 401
