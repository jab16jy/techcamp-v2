"""`GET /plots/{plot_id}/status` fed by the simulator's own payloads (E9 T7).

docs/10 E9 closes on "pantalla de inicio completa con datos reales del
simulador", so this test proves that phrase instead of asserting it: the
uplinks are produced by `simulator.runner.publish_backfill` itself (`docs/06
§10`) through the `UplinkPublisher` port and encoded exactly as
`MqttUplinkPublisher` encodes them, and the calibration is the one
`node_client.ensure_calibrations` posts for a `%` sensor
(`docs/03-modelo-datos.md:463-467`). Nothing here is hand-made JSON, and the
backfill is not reimplemented here either, so a change in either breaks this
test the way it would break the seminar.

The payload under test is `docs/04 §Estado de la parcela (pantalla principal)`,
read through the real ingest flush (`docs/06 §1`): parse → channel → calibrate →
quality → batch insert → node status. D-T0.5 (`nodes`) is what this file
exercises. D-T0.4's 24 h window and D-T2.1's representative depth are NOT
proved here — every reading this file generates is inside the window, and one
moisture sensor cannot tell representative selection from "any moisture sensor"
apart. Both are covered where they belong: the window at
`tests/telemetry/test_home_queries.py`, the representative sensor at
`tests/home/test_plot_status.py`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.repositories import SqlAlchemyPlotRepository
from techcamp.identity.adapters.security.token_issuer import issue_token
from techcamp.irrigation.adapters.api.deps import get_now
from techcamp.main import app
from techcamp.simulator.node_client import _calibration_payload
from techcamp.simulator.runner import publish_backfill
from techcamp.simulator.trajectory import backfill_timestamps, raw_value_at
from techcamp.telemetry.adapters.orm import ReadingRow, SensorRow
from techcamp.telemetry.adapters.repositories import (
    SqlAlchemyCalibrationRepository,
    SqlAlchemyNodeRepository,
    SqlAlchemyPlotEventsNotifier,
    SqlAlchemyReadingRepository,
    SqlAlchemySensorRepository,
)
from techcamp.telemetry.application.ingest_uplinks import RawUplink, ingest_uplinks
from techcamp.telemetry.domain.models import (
    Calibration,
    CalibrationKind,
    CalibrationMethod,
    apply_calibration,
)

from .conftest import NOW, HomeEnv, add_node, add_sensor, add_soil, make_env

pytestmark = pytest.mark.anyio

DAYS = 1.0
INTERVAL_S = 900
"""docs/06 §10's scenario `interval_s: 900`, so the backfill lands one day of
readings exactly like `--backfill-days 1` does in the seminar."""

SEED = 0
"""`--seed`'s default."""


@pytest.fixture
def client() -> TestClient:
    # One fixed clock for the whole module, like tests/home/test_api.py: the 24 h
    # freshness window and `local_today` are read against it. The override is
    # cleared on teardown, so `pytest-randomly` cannot leak this clock into
    # another module's tests.
    app.dependency_overrides[get_now] = lambda: NOW
    yield TestClient(app, base_url="http://testserver/api/v1")
    app.dependency_overrides.clear()


def _get(client: TestClient, env: HomeEnv) -> dict[str, Any]:
    token = issue_token(str(env.user_id))
    response = client.get(
        f"/plots/{env.plot_id}/status", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _iso(at: datetime) -> str:
    """Pydantic's own rendering of an aware UTC datetime (`…T14:45:00Z`)."""
    return at.isoformat().replace("+00:00", "Z")


async def _set_unit(session: AsyncSession, sensor_id: int, unit: str) -> None:
    """`provision_unclaimed_node` gives the simulated sensor `unit='%'`
    (`docs/03-modelo-datos.md:463-467`), and `classify_reading_range` only
    enforces the documented 0–100 range for that spelling. The shared builder
    defaults to `'pct'`, which has no documented range at all, so the simulator's
    own calibration would be an identity and its 'percentage' a raw ADC count.
    """
    await session.execute(update(SensorRow).where(SensorRow.id == sensor_id).values(unit=unit))
    await session.commit()


async def _simulated_env(session: AsyncSession) -> tuple[HomeEnv, UUID, int, int]:
    """An org, a plot with a soil profile, and a claimed node whose calibrated
    sensor sits at the plot's representative depth (D-T2.1: `root_depth_cm=20`
    puts Zr/2 at 10 cm, where `sm_10` is installed), plus a second sensor left
    uncalibrated on purpose.

    `last_seen_at=None` is a node nobody has heard from yet: `status` is
    `provisioned` until a flush marks it seen, which is what the no-flush
    negative reads.
    """
    env = await make_env(session)
    await add_soil(session, env, root_depth_cm=20.0)
    node_id = await add_node(session, env, claim_code="SIM-T7", last_seen_at=None)
    moisture = await add_sensor(
        session, node_id, metric="soil_moisture", depth_cm=10, channel_key="sm_10"
    )
    await _set_unit(session, moisture, "%")
    uncalibrated = await add_sensor(session, node_id, metric="air_temp", channel_key="t_air")
    return env, node_id, moisture, uncalibrated


async def _calibrate_like_the_simulator(
    session: AsyncSession, *, org_id: UUID, sensor_id: int
) -> Calibration:
    """The calibration `ensure_calibrations` posts for a `%` sensor, stored
    through the real repository (`docs/03-modelo-datos.md:461`: versioned,
    never edited). `valid_from` opens on the earliest backfilled second, the
    `R3-calibration-valid-from-after-backfill` rule in the simulator CLI."""
    valid_from = (NOW - timedelta(days=DAYS)).replace(microsecond=0)
    payload = _calibration_payload({"unit": "%"}, valid_from=valid_from)
    created = await SqlAlchemyCalibrationRepository(session).add_version(
        org_id=org_id,
        sensor_id=sensor_id,
        version=1,
        method=CalibrationMethod(payload["method"]),
        kind=CalibrationKind(payload["kind"]),
        params=payload["params"],
        rmse_pct=None,
        valid_from=valid_from,
    )
    assert created is not None
    return created


class _RecordingPublisher:
    """The `UplinkPublisher` port's double (ADR-0002: external I/O needs one,
    and Mosquitto is not assumed running for a test). It records what the
    backfill publishes so the test reads the payloads `publish_backfill` really
    built, instead of rebuilding that loop here and proving nothing about it.
    """

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    async def publish_uplink(self, node_id: UUID, payload: dict[str, Any]) -> None:
        self.payloads.append(payload)

    async def publish_status(self, node_id: UUID, status: str) -> None:
        raise AssertionError("the backfill publishes no status message")


async def _simulated_uplinks(node_id: UUID) -> tuple[list[RawUplink], list[int]]:
    """The uplinks `publish_backfill` sends for this node's two channels: one
    per trajectory point, `seq = i + 1`, sensor `j` on `seed + j` (`docs/06
    §10`). Driving the real function is the point — its point count, its `seq`
    and its per-sensor seed offset are what the assertions below read."""
    publisher = _RecordingPublisher()
    published = await publish_backfill(
        publisher,
        node_id,
        sensors=[{"channel_key": "sm_10"}, {"channel_key": "t_air"}],
        days=DAYS,
        interval_s=INTERVAL_S,
        seed=SEED,
        now=NOW,
    )
    timestamps = backfill_timestamps(days=DAYS, interval_s=INTERVAL_S, now=NOW)
    assert published == len(timestamps) == len(publisher.payloads)
    messages = [
        RawUplink(node_id=node_id, payload=json.dumps(payload).encode(), received_at=NOW)
        for payload in publisher.payloads
    ]
    return messages, timestamps


def _ingest_ports(session: AsyncSession) -> dict[str, Any]:
    """The ports one ingest flush takes, real adapters on one session."""
    return {
        "nodes": SqlAlchemyNodeRepository(session),
        "sensors": SqlAlchemySensorRepository(session),
        "calibrations": SqlAlchemyCalibrationRepository(session),
        "readings": SqlAlchemyReadingRepository(session),
        "plots": SqlAlchemyPlotRepository(session),
        "events": SqlAlchemyPlotEventsNotifier(session),
    }


async def test_status_shows_the_newest_calibrated_simulator_reading(
    db_session: AsyncSession,
    client: TestClient,
) -> None:
    env, node_id, moisture, uncalibrated = await _simulated_env(db_session)
    calibration = await _calibrate_like_the_simulator(
        db_session, org_id=env.org_id, sensor_id=moisture
    )
    messages, timestamps = await _simulated_uplinks(node_id)

    stats = await ingest_uplinks(messages, **_ingest_ports(db_session))

    body = _get(client, env)
    newest_raw = raw_value_at(len(timestamps) - 1, seed=SEED)
    newest_at = datetime.fromtimestamp(timestamps[-1], tz=UTC)

    assert stats.inserted == 2 * len(timestamps)
    assert body["latest"]["soil_moisture_pct"] == pytest.approx(
        apply_calibration(calibration, newest_raw)
    )
    assert body["latest"]["at"] == _iso(newest_at)
    assert [node["node_id"] for node in body["nodes"]] == [str(node_id)]
    assert body["nodes"][0]["status"] == "online"
    assert body["nodes"][0]["last_seen_at"] == _iso(NOW)

    # Negative: the uncalibrated channel is stored raw and never becomes a
    # value — the home shows `null` for it, not 0 and not the raw ADC count.
    assert body["latest"]["air_temp_c"] is None
    assert body["latest"]["air_temp_c"] != 0
    assert stats.counts["uncalibrated"] == len(timestamps)
    stored = (
        await db_session.execute(
            select(ReadingRow.value, ReadingRow.raw_value)
            .where(ReadingRow.sensor_id == uncalibrated)
            .order_by(ReadingRow.time)
            .limit(1)
        )
    ).one()
    assert stored.value is None
    assert stored.raw_value == pytest.approx(raw_value_at(0, seed=SEED + 1))


async def test_before_any_flush_the_latest_metrics_are_null_and_the_node_is_not_online(
    db_session: AsyncSession,
    client: TestClient,
) -> None:
    """The same environment, one ingest flush short: every source of `latest`
    exists and is empty, which is the state a phone opens into before the
    simulator's first uplink lands."""
    env, node_id, moisture, _uncalibrated = await _simulated_env(db_session)
    await _calibrate_like_the_simulator(db_session, org_id=env.org_id, sensor_id=moisture)

    body = _get(client, env)

    assert body["latest"] == {
        "soil_moisture_pct": None,
        "air_temp_c": None,
        "air_rh_pct": None,
        "at": None,
    }
    assert [node["node_id"] for node in body["nodes"]] == [str(node_id)]
    assert body["nodes"][0]["status"] == "provisioned"
    assert body["nodes"][0]["last_seen_at"] is None

    # Negative: missing evidence is `null`, never a zero or a false, and the
    # node is never reported online without an uplink.
    assert body["latest"]["soil_moisture_pct"] not in (0, 0.0, False)
    assert body["latest"]["at"] is None
    assert body["nodes"][0]["status"] != "online"
    assert await db_session.scalar(text("SELECT count(*) FROM reading")) == 0, (
        "no flush wrote no reading row"
    )
