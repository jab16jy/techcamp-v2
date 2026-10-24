"""E16 T3: the scenario's weather fixture lands in `weather_daily`
(docs/04-api.md:263; D-T16.2; docs/06-diseno-detallado.md:490).

The recording's rows are keyed by the scenario's day offset, not by the
calendar dates the capture covered, so the loader places each row on the
scenario's day 0 plus its offset. The default anchor is the one that puts the
scenario's forecast window on today: the fixture's observed rows are the days
before it, so its forecast rows are today's and the six days after
(docs/06-diseno-detallado.md:246).
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import PlotRow
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.adapters.security.token_issuer import issue_token
from techcamp.main import app
from techcamp.shared.dates import local_today
from techcamp.shared.ids import uuid7
from techcamp.weather.adapters.orm import WeatherCellRow, WeatherDailyRow

pytestmark = pytest.mark.anyio


async def _org(session: AsyncSession, *, phone: str) -> tuple[UUID, str]:
    """One organization with one owner, the way a seminar stack starts."""
    org_id, user_id = uuid7(), uuid7()
    session.add(AppUserRow(id=user_id, phone=phone, full_name="Owner"))
    session.add(OrganizationRow(id=org_id, name=f"Org {org_id.hex[:6]}", kind="individual"))
    await session.commit()
    session.add(MembershipRow(org_id=org_id, user_id=user_id, role="owner"))
    await session.commit()
    return org_id, issue_token(str(user_id))


def _client() -> TestClient:
    return TestClient(app, base_url="http://testserver/api/v1")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_the_fixture_lands_on_the_scenario_days(db_session: AsyncSession) -> None:
    """The recording's rows are keyed by the scenario's day offset
    (docs/06:490), so they land on the anchor plus the offset — not on the
    calendar dates the capture covered.

    The default anchor is the one that puts the scenario's forecast window on
    today: the fixture's fourteen observed days are the days before it, so the
    forecast rows are today's and the six days after (docs/06:246).
    """
    _org_id, token = await _org(db_session, phone="+573000000010")

    response = _client().post("/dev/scenarios/el-nino:load", json={}, headers=_auth(token))
    assert response.status_code == 202, response.text

    rows = (
        (
            await db_session.execute(
                select(WeatherDailyRow).order_by(WeatherDailyRow.day, WeatherDailyRow.is_forecast)
            )
        )
        .scalars()
        .all()
    )

    assert len(rows) == 21
    assert rows[0].day == local_today() - timedelta(days=14)
    observed = [row for row in rows if not row.is_forecast]
    forecast = [row for row in rows if row.is_forecast]
    assert len(observed) == 14
    assert len(forecast) == 7
    assert forecast[0].day == local_today()
    assert forecast[-1].day == local_today() + timedelta(days=6)


async def test_the_weather_rows_land_on_the_parcel_cell(db_session: AsyncSession) -> None:
    """The parcel's cell and the fixture's cell are the same one: the balance
    reads the weather of the cell the parcel's centre falls in
    (docs/06-diseno-detallado.md §6)."""
    _org_id, token = await _org(db_session, phone="+573000000011")

    response = _client().post("/dev/scenarios/el-nino:load", json={}, headers=_auth(token))
    plot_id = UUID(response.json()["plot_ids"][0])

    plot = (await db_session.execute(select(PlotRow).where(PlotRow.id == plot_id))).scalar_one()
    cell = (
        await db_session.execute(
            select(WeatherCellRow).where(WeatherCellRow.id == plot.weather_cell_id)
        )
    ).scalar_one()

    assert float(cell.lat) == 11.0
    assert float(cell.lon) == -74.8
    rows = (
        (
            await db_session.execute(
                select(WeatherDailyRow).where(WeatherDailyRow.cell_id == cell.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 21
