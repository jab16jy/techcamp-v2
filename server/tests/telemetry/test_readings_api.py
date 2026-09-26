"""`GET /plots/{plot_id}/readings` (docs/04-api.md:92-97).

Org isolation per docs/09-cuellos-de-botella.md#seguridad: a plot in another
organization responds 404, never 403. Continuous-aggregate refresh follows
the same AUTOCOMMIT reasoning as `test_reading_repository.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.main import app
from techcamp.shared.db import engine
from techcamp.telemetry.adapters.orm import ReadingRow, SensorRow

from .test_api import _auth, _claim_node, _make_plot, _make_sensor, _member

pytestmark = pytest.mark.anyio


async def _insert_reading(
    db_session: AsyncSession, sensor_id: int, *, at: datetime, value: float | None
) -> None:
    db_session.add(
        ReadingRow(
            time=at, sensor_id=sensor_id, raw_value=1.0, value=value, received_at=at, quality=0
        )
    )
    await db_session.commit()


async def _refresh_aggregate(view: str) -> None:
    autocommit_engine = engine.execution_options(isolation_level="AUTOCOMMIT")
    async with autocommit_engine.connect() as conn:
        await conn.execute(text(f"CALL refresh_continuous_aggregate('{view}', NULL, NULL)"))


async def test_raw_series_returns_calibrated_points(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)
    node_id = await _claim_node(db_session, org_id, plot_id, claim_code="C1")
    sensor_id = await _make_sensor(db_session, node_id)
    await _insert_reading(
        db_session, sensor_id, at=datetime(2026, 3, 1, 12, tzinfo=UTC), value=21.0
    )
    await _insert_reading(
        db_session, sensor_id, at=datetime(2026, 3, 1, 13, tzinfo=UTC), value=None
    )

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/plots/{plot_id}/readings",
            params={
                "metric": "soil_moisture",
                "from": "2026-03-01T00:00:00Z",
                "to": "2026-03-02T00:00:00Z",
                "resolution": "raw",
            },
            headers=_auth(token),
        )

    assert response.status_code == 200
    body = response.json()
    assert len(body["series"]) == 1
    series = body["series"][0]
    assert series["sensor_id"] == sensor_id
    assert series["depth_cm"] == 10
    assert series["points"] == [["2026-03-01T12:00:00Z", 21.0]]


async def test_hour_resolution_returns_aggregate_buckets(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)
    node_id = await _claim_node(db_session, org_id, plot_id, claim_code="C1")
    sensor_id = await _make_sensor(db_session, node_id)
    await _insert_reading(
        db_session, sensor_id, at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC), value=10.0
    )
    await _insert_reading(
        db_session, sensor_id, at=datetime(2026, 3, 1, 10, 30, tzinfo=UTC), value=20.0
    )
    await _refresh_aggregate("reading_hourly")

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/plots/{plot_id}/readings",
            params={
                "metric": "soil_moisture",
                "from": "2026-03-01T00:00:00Z",
                "to": "2026-03-02T00:00:00Z",
                "resolution": "hour",
            },
            headers=_auth(token),
        )

    assert response.status_code == 200
    points = response.json()["series"][0]["points"]
    assert points == [["2026-03-01T10:00:00Z", 15.0]]


async def test_day_resolution_returns_aggregate_buckets(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)
    node_id = await _claim_node(db_session, org_id, plot_id, claim_code="C1")
    sensor_id = await _make_sensor(db_session, node_id)
    await _insert_reading(db_session, sensor_id, at=datetime(2026, 1, 1, 1, tzinfo=UTC), value=10.0)
    await _insert_reading(
        db_session, sensor_id, at=datetime(2026, 1, 1, 23, tzinfo=UTC), value=30.0
    )
    await _refresh_aggregate("reading_daily")

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/plots/{plot_id}/readings",
            params={
                "metric": "soil_moisture",
                "from": "2025-11-01T00:00:00Z",
                "to": "2026-01-31T00:00:00Z",
                "resolution": "day",
            },
            headers=_auth(token),
        )

    assert response.status_code == 200
    points = response.json()["series"][0]["points"]
    assert points == [["2026-01-01T00:00:00Z", 20.0]]


async def test_raw_beyond_2_days_is_rejected(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/plots/{plot_id}/readings",
            params={
                "metric": "soil_moisture",
                "from": "2026-01-01T00:00:00Z",
                "to": "2026-01-05T00:00:00Z",
                "resolution": "raw",
            },
            headers=_auth(token),
        )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"


async def test_hour_beyond_60_days_is_rejected(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/plots/{plot_id}/readings",
            params={
                "metric": "soil_moisture",
                "from": "2026-01-01T00:00:00Z",
                "to": "2026-04-15T00:00:00Z",
                "resolution": "hour",
            },
            headers=_auth(token),
        )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"


async def test_bad_resolution_is_rejected(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/plots/{plot_id}/readings",
            params={
                "metric": "soil_moisture",
                "from": "2026-01-01T00:00:00Z",
                "to": "2026-01-02T00:00:00Z",
                "resolution": "weekly",
            },
            headers=_auth(token),
        )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"


async def test_from_after_to_is_rejected(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/plots/{plot_id}/readings",
            params={
                "metric": "soil_moisture",
                "from": "2026-01-02T00:00:00Z",
                "to": "2026-01-01T00:00:00Z",
                "resolution": "raw",
            },
            headers=_auth(token),
        )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"


async def test_metric_filters_sensors(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)
    node_id = await _claim_node(db_session, org_id, plot_id, claim_code="C1")
    matching_sensor_id = await _make_sensor(db_session, node_id, channel_key="sm_10")
    other_sensor = SensorRow(
        node_id=node_id, channel_key="at", metric="air_temp", depth_cm=None, unit="c"
    )
    db_session.add(other_sensor)
    await db_session.commit()
    await db_session.refresh(other_sensor)
    await _insert_reading(
        db_session, matching_sensor_id, at=datetime(2026, 3, 1, 12, tzinfo=UTC), value=21.0
    )
    await _insert_reading(
        db_session, other_sensor.id, at=datetime(2026, 3, 1, 12, tzinfo=UTC), value=30.0
    )

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/plots/{plot_id}/readings",
            params={
                "metric": "soil_moisture",
                "from": "2026-03-01T00:00:00Z",
                "to": "2026-03-02T00:00:00Z",
                "resolution": "raw",
            },
            headers=_auth(token),
        )

    body = response.json()
    assert len(body["series"]) == 1
    assert body["series"][0]["sensor_id"] == matching_sensor_id


async def test_plot_in_another_org_returns_404(db_session: AsyncSession) -> None:
    _org_a, _user_a, token_a = await _member(db_session, role="owner", org_name="Finca A")
    org_b, _user_b, _token_b = await _member(db_session, role="owner", org_name="Finca B")
    plot_id = await _make_plot(db_session, org_b)

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/plots/{plot_id}/readings",
            params={
                "metric": "soil_moisture",
                "from": "2026-03-01T00:00:00Z",
                "to": "2026-03-02T00:00:00Z",
                "resolution": "raw",
            },
            headers=_auth(token_a),
        )

    assert response.status_code == 404


async def test_plot_with_no_nodes_returns_empty_series(db_session: AsyncSession) -> None:
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/plots/{plot_id}/readings",
            params={
                "metric": "soil_moisture",
                "from": "2026-03-01T00:00:00Z",
                "to": "2026-03-02T00:00:00Z",
                "resolution": "raw",
            },
            headers=_auth(token),
        )

    assert response.status_code == 200
    assert response.json() == {"series": []}


async def test_naive_from_and_to_are_rejected(db_session: AsyncSession) -> None:
    """A boundary without an offset would be read against the session
    timezone (docs/04-api.md:20: ISO 8601 in UTC) — 422, never a silent shift."""
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/plots/{plot_id}/readings",
            params={
                "metric": "soil_moisture",
                "from": "2026-03-01T00:00:00",
                "to": "2026-03-02T00:00:00",
                "resolution": "raw",
            },
            headers=_auth(token),
        )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"


async def test_mixed_timezone_awareness_is_rejected(db_session: AsyncSession) -> None:
    """One aware and one naive boundary used to raise `TypeError` and answer
    500 (#37); it is a 422 like every other invalid range."""
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            f"/api/v1/plots/{plot_id}/readings",
            params={
                "metric": "soil_moisture",
                "from": "2026-03-01T00:00:00Z",
                "to": "2026-03-02T00:00:00",
                "resolution": "raw",
            },
            headers=_auth(token),
        )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"


async def test_unknown_metric_is_rejected(db_session: AsyncSession) -> None:
    """A typo'd `metric` used to answer 200 with an empty `series`, the same
    as a plot with no matching sensor (#37); docs/04-api.md:94 rejects it."""
    org_id, _user_id, token = await _member(db_session, role="owner")
    plot_id = await _make_plot(db_session, org_id)

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/plots/{plot_id}/readings",
            params={
                "metric": "soil_moisturre",
                "from": "2026-03-01T00:00:00Z",
                "to": "2026-03-02T00:00:00Z",
                "resolution": "raw",
            },
            headers=_auth(token),
        )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
