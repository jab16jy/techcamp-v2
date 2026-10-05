"""`GET /plots/{plot_id}/status` endpoint (E9 T2).

docs/04-api.md §Estado de la parcela (pantalla principal): one request, one
`problem+json` for a plot of another organization, `null` for what is missing.

Org isolation per docs/09-cuellos-de-botella.md#seguridad: a plot that is not in
one of the caller's organizations answers 404, never a payload that leaks it
exists. Every test carries its negative assertion.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.identity.adapters.security.token_issuer import issue_token
from techcamp.irrigation.adapters.api.deps import get_now
from techcamp.main import app
from techcamp.metrics.adapters.orm import PlotMetricMonthlyRow
from techcamp.shared.ids import uuid7

from .conftest import (
    NOW,
    TODAY,
    YESTERDAY,
    HomeEnv,
    add_alert,
    add_cycle,
    add_forecast,
    add_node,
    add_reading,
    add_recommendation,
    add_sensor,
    add_soil,
    add_water_balance,
    calibrate,
    make_env,
)

pytestmark = pytest.mark.anyio


def _client() -> TestClient:
    # One fixed clock for the whole module: `today`, the 24 h freshness window
    # and the default water-balance range are all read against it.
    app.dependency_overrides[get_now] = lambda: NOW
    return TestClient(app, base_url="http://testserver/api/v1")


@pytest.fixture
def client() -> TestClient:
    test_client = _client()
    yield test_client
    app.dependency_overrides.clear()


def _get(client: TestClient, env: HomeEnv) -> dict[str, object]:
    token = issue_token(str(env.user_id))
    response = client.get(
        f"/plots/{env.plot_id}/status", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


async def test_status_returns_the_whole_docs_payload_in_one_request(
    db_session: AsyncSession,
    client: TestClient,
) -> None:
    env = await make_env(db_session)
    await add_soil(db_session, env, root_depth_cm=60.0)
    node_id = await add_node(db_session, env, claim_code="NODE-1", last_seen_at=NOW)
    soil = await add_sensor(db_session, node_id, depth_cm=30, channel_key="sm_30")
    await calibrate(db_session, soil)
    await add_reading(db_session, soil, at=NOW - timedelta(hours=2), value=21.5)
    temp = await add_sensor(db_session, node_id, metric="air_temp", channel_key="t_air")
    await add_reading(db_session, temp, at=NOW - timedelta(hours=1), value=29.0)
    rh = await add_sensor(db_session, node_id, metric="air_rh", channel_key="rh_air")
    await add_reading(db_session, rh, at=NOW - timedelta(hours=1), value=78.0)
    await add_cycle(db_session, env, sown_on=TODAY - timedelta(days=24))
    await add_water_balance(db_session, env, day=YESTERDAY)
    await add_recommendation(db_session, env, day=TODAY, rationale={"forecast_rain_7d_mm": 12.5})
    alert_id = await add_alert(db_session, env, severity="critical")
    await add_forecast(db_session, env, days=3)

    body = _get(client, env)

    assert set(body) == {
        "plot",
        "active_cycle",
        "latest",
        "water_balance",
        "recommendation",
        "open_alerts",
        "weather_next_3d",
        "nodes",
        "digital_adoption_index",
    }
    assert body["plot"]["id"] == str(env.plot_id)
    assert body["plot"]["irrigation_system"] == "drip"
    assert body["active_cycle"]["crop"]["code"] == "maize"
    assert body["active_cycle"]["stage"] == "development"
    assert body["active_cycle"]["day_of_cycle"] == 25
    assert body["latest"] == {
        "soil_moisture_pct": 21.5,
        "air_temp_c": 29.0,
        "air_rh_pct": 78.0,
        "at": "2026-09-30T14:00:00Z",
    }
    assert body["water_balance"]["status"] == "ok"
    assert body["water_balance"]["taw_mm"] == pytest.approx(84.0)
    assert body["recommendation"]["kind"] == "irrigate"
    assert body["recommendation"]["depth_mm"] == pytest.approx(12.0)
    assert body["recommendation"]["rationale"] == {"forecast_rain_7d_mm": 12.5}
    assert [alert["id"] for alert in body["open_alerts"]] == [str(alert_id)]
    assert body["open_alerts"][0]["severity"] == "critical"
    assert [day["day"] for day in body["weather_next_3d"]] == [
        "2026-09-30",
        "2026-10-01",
        "2026-10-02",
    ]
    assert [node["node_id"] for node in body["nodes"]] == [str(node_id)]
    # E11 T8 (D-T0.13): this plot has no stored month, so the field is `null`
    # on the wire — present in the payload, never a zero.
    assert body["digital_adoption_index"] is None

    # Negative: the `advice` of an irrigated recommendation stays out, and no
    # fourth forecast day appears.
    assert body["recommendation"]["advice"] == []
    assert len(body["weather_next_3d"]) == 3


async def test_status_carries_the_latest_monthly_index_with_its_month(
    db_session: AsyncSession,
    client: TestClient,
) -> None:
    """docs/04 §Estado: `digital_adoption_index: { value, month } | null`.

    The `month` travels because the web writes "Adopción digital: 72 ·
    septiembre" (docs/07 §Inicio) and cannot say which month a bare 72 is from.
    """
    env = await make_env(db_session)
    db_session.add(
        PlotMetricMonthlyRow(
            plot_id=env.plot_id,
            org_id=env.org_id,
            month=TODAY.replace(day=1),
            monitoring=Decimal("0.5"),
            record_keeping=Decimal(1),
            decision=Decimal(1),
            risk_management=None,
            digital_adoption_index=Decimal("62.5"),
            computed_at=NOW,
        )
    )
    await db_session.commit()

    body = _get(client, env)

    assert body["digital_adoption_index"] == {"value": 62.5, "month": "2026-09-01"}

    # Negative: the stored components never ride along. `/status` is the home
    # screen on 3G, not the metrics read API of T7 (docs/04 §Métricas).
    assert set(body["digital_adoption_index"]) == {"value", "month"}


async def test_rainfed_plot_never_shows_irrigate_depth_or_minutes(
    db_session: AsyncSession,
    client: TestClient,
) -> None:
    """ADR-0023's home variant: the rainfed card carries advice, not a depth."""
    env = await make_env(db_session, irrigation_system="none")
    await add_water_balance(db_session, env, day=YESTERDAY, depletion_mm=60.0, raw_mm=46.2)
    await add_recommendation(
        db_session,
        env,
        day=TODAY,
        kind="rainfed",
        depth_mm=None,
        duration_min=None,
        advice=["delay_sowing", "conserve_moisture"],
    )

    body = _get(client, env)

    assert body["plot"]["irrigation_system"] == "none"
    assert body["water_balance"]["status"] == "stress"
    assert body["recommendation"]["kind"] == "rainfed"
    assert body["recommendation"]["depth_mm"] is None
    assert body["recommendation"]["duration_min"] is None
    assert body["recommendation"]["advice"] == ["delay_sowing", "conserve_moisture"]

    # Negative: `irrigate` never appears on a rainfed plot, at any depletion.
    assert body["water_balance"]["status"] != "irrigate"
    assert body["recommendation"]["kind"] != "irrigate"


async def test_a_plot_without_cycle_readings_or_recommendation_is_all_null(
    db_session: AsyncSession,
    client: TestClient,
) -> None:
    env = await make_env(db_session)

    body = _get(client, env)

    assert body["active_cycle"] is None
    assert body["latest"] == {
        "soil_moisture_pct": None,
        "air_temp_c": None,
        "air_rh_pct": None,
        "at": None,
    }
    assert body["water_balance"] is None
    assert body["recommendation"] is None
    assert body["open_alerts"] == []
    assert body["weather_next_3d"] == []
    assert body["nodes"] == []
    assert body["digital_adoption_index"] is None

    # Negative: missing evidence is `null`, never 0, `false` or an empty object.
    assert body["latest"]["soil_moisture_pct"] not in (0, 0.0, False)
    assert body["water_balance"] not in ({}, [])


async def test_future_sowing_is_an_active_cycle_with_a_null_stage_and_day(
    db_session: AsyncSession,
    client: TestClient,
) -> None:
    env = await make_env(db_session)
    await add_cycle(db_session, env, sown_on=TODAY + timedelta(days=6))

    body = _get(client, env)

    assert body["active_cycle"] is not None
    assert body["active_cycle"]["stage"] is None
    assert body["active_cycle"]["day_of_cycle"] is None

    # Negative: the whole cycle is not null, and `day_of_cycle` is not a
    # negative number for a sowing that has not happened yet.
    assert body["active_cycle"]["crop"]["code"] == "maize"
    assert body["active_cycle"]["day_of_cycle"] != -4


async def test_representative_sensor_wins_over_a_newer_reading_at_another_depth(
    db_session: AsyncSession,
    client: TestClient,
) -> None:
    """D-T2.1 over HTTP: the payload the web renders on 3G."""
    env = await make_env(db_session)
    await add_soil(db_session, env, root_depth_cm=60.0)
    representative_node = await add_node(db_session, env, claim_code="NODE-A")
    representative = await add_sensor(
        db_session, representative_node, depth_cm=30, channel_key="sm_30"
    )
    await calibrate(db_session, representative)
    await add_reading(db_session, representative, at=NOW - timedelta(hours=5), value=21.0)
    shallow_node = await add_node(db_session, env, claim_code="NODE-B")
    shallow = await add_sensor(db_session, shallow_node, depth_cm=10, channel_key="sm_10")
    await calibrate(db_session, shallow, kind="lab")
    await add_reading(db_session, shallow, at=NOW - timedelta(hours=1), value=33.0)

    body = _get(client, env)

    assert body["latest"]["soil_moisture_pct"] == pytest.approx(21.0)
    assert body["latest"]["at"] == "2026-09-30T10:00:00Z"
    # Negative: the newest reading at any depth is 33.0 one hour ago, and that
    # is what the home would show without the representative-sensor rule.
    assert body["latest"]["soil_moisture_pct"] != pytest.approx(33.0)
    assert body["latest"]["at"] != "2026-09-30T14:00:00Z"


async def test_a_resolved_alert_is_not_in_open_alerts(
    db_session: AsyncSession,
    client: TestClient,
) -> None:
    env = await make_env(db_session)
    await add_alert(db_session, env, rule_code="water_stress", resolve=True)

    body = _get(client, env)

    assert body["open_alerts"] == []


async def test_a_plot_of_another_organization_is_404(
    db_session: AsyncSession,
    client: TestClient,
) -> None:
    other = await make_env(db_session, name="Finca Ajena")
    mine = await make_env(db_session, name="Finca Mía")
    await add_water_balance(db_session, other, day=YESTERDAY)
    await add_alert(db_session, other)

    token = issue_token(str(mine.user_id))
    response = client.get(
        f"/plots/{other.plot_id}/status", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["title"] == "Plot not found"
    # Negative: the 404 body leaks nothing about the other plot — no data, no
    # name, no count.
    assert "Finca Ajena" not in response.text
    assert response.json().get("detail") is None or "Lote" not in response.text


async def test_a_plot_id_of_no_organization_at_all_answers_the_same_404(
    db_session: AsyncSession,
    client: TestClient,
) -> None:
    """A caller naming a plot that does not exist gets the same answer as one
    naming a real plot of another org, so existence never leaks
    (docs/09-cuellos-de-botella.md#seguridad)."""
    mine = await make_env(db_session)
    unknown = uuid7()

    token = issue_token(str(mine.user_id))
    missing = client.get(f"/plots/{unknown}/status", headers={"Authorization": f"Bearer {token}"})
    other = await make_env(db_session, name="Finca Ajena")
    foreign = client.get(
        f"/plots/{other.plot_id}/status", headers={"Authorization": f"Bearer {token}"}
    )

    assert missing.status_code == foreign.status_code == 404
    assert missing.json()["title"] == foreign.json()["title"] == "Plot not found"
    # Negative: a syntactically invalid id is a 422, not a 404 with a body.
    invalid = client.get("/plots/not-a-uuid/status", headers={"Authorization": f"Bearer {token}"})
    assert invalid.status_code == 422


async def test_an_unauthenticated_request_is_401(
    db_session: AsyncSession,
    client: TestClient,
) -> None:
    env = await make_env(db_session)

    response = client.get(f"/plots/{env.plot_id}/status")

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    # Negative: no payload is served without a token, not even a null-filled one.
    assert "plot" not in response.text
