"""#40: `run()`'s orchestration — the wiring no unit test around its parts can
see (docs/06-diseno-detallado.md §10): the seminar-profile guard (ADR-0021),
provisioning before claiming, the calibration window that must cover the whole
backfill, and `seq` continuing into the live loop.

The api is the real FastAPI app over an in-process ASGI transport (as in
test_node_client.py) and the broker is a fake, so MQTT stays the only external
I/O doubled here (ADR-0002).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.identity.adapters.security.otp_store import otp_store
from techcamp.main import app
from techcamp.simulator import __main__ as cli
from techcamp.simulator.runner import publish_live
from techcamp.simulator.trajectory import raw_value_at
from techcamp.telemetry.adapters.orm import CalibrationRow, NodeRow, SensorRow
from techcamp.telemetry.adapters.repositories import SqlAlchemyCalibrationRepository

from .test_node_client import _make_plot, _member

pytestmark = pytest.mark.anyio

_BACKFILL_DAYS = 0.1
_INTERVAL_S = 300
_BACKFILL_COUNT = int(_BACKFILL_DAYS * 86400 / _INTERVAL_S)  # 28


def _in_process_client(base_url: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url)


class _FakeMqttConnection:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, topic: str, payload: bytes, qos: int = 0, retain: bool = False) -> None:
        self.published.append((topic, json.loads(payload)))


class _FakeMqttClient:
    """Stands in for `aiomqtt.Client`: MQTT is external I/O, so it gets a
    double (ADR-0002)."""

    def __init__(
        self, host: str, port: int, username: str | None = None, password: str | None = None
    ) -> None:
        self.endpoint = (host, port)
        self.credentials = (username, password)
        self.connection = _FakeMqttConnection()

    async def __aenter__(self) -> _FakeMqttConnection:
        return self.connection

    async def __aexit__(self, *exc_info: object) -> None:
        return None


async def _no_sleep(seconds: float) -> None:
    pass


_OPENED_MQTT: list[_FakeMqttClient] = []


def _opened_mqtt(
    host: str, port: int, username: str | None = None, password: str | None = None
) -> _FakeMqttClient:
    client = _FakeMqttClient(host, port, username=username, password=password)
    _OPENED_MQTT.append(client)
    return client


def _recording_live(
    calls: list[dict[str, Any]],
) -> Callable[..., Awaitable[None]]:
    """The CLI default runs the live loop until Ctrl+C, so a test cannot let it
    loose: this records the arguments `run()` wired and publishes a single
    point through the real loop."""

    async def _publish_live(publisher: Any, node_id: UUID, **kwargs: Any) -> None:
        calls.append(kwargs)
        await publish_live(publisher, node_id, **kwargs, iterations=1, sleep=_no_sleep)

    return _publish_live


async def test_run_refuses_to_publish_outside_the_seminar_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0021: the simulator replaces physical nodes in the seminar profile
    only; against a production api it must not run at all."""

    monkeypatch.setattr(cli, "is_seminar_profile", lambda: False)

    with pytest.raises(SystemExit, match="seminar"):
        await cli.run(
            cli.parse_args(
                ["--phone", "+573001112233", "--plot-id", str(UUID(int=0)), "--provision"]
            )
        )


async def test_run_provisions_then_claims_calibrates_the_backfill_and_continues_seq_into_live(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org_id, phone = await _member(db_session)
    plot_id = await _make_plot(db_session, org_id)
    otp_store.issue(phone)  # the scripted-run path: `--otp-code` (docs/04-api.md:173-174)
    otp_code = otp_store._codes[phone][0]

    live_calls: list[dict[str, Any]] = []
    _OPENED_MQTT.clear()
    monkeypatch.setattr(cli, "httpx", SimpleNamespace(AsyncClient=_in_process_client))
    monkeypatch.setattr(cli, "aiomqtt", SimpleNamespace(Client=_opened_mqtt))
    monkeypatch.setattr(cli, "publish_live", _recording_live(live_calls))

    await cli.run(
        cli.parse_args(
            [
                "--phone",
                phone,
                "--otp-code",
                otp_code,
                "--plot-id",
                str(plot_id),
                "--provision",
                "--backfill-days",
                str(_BACKFILL_DAYS),
                "--interval-s",
                str(_INTERVAL_S),
                "--live",
            ]
        )
    )

    # The provisioned node is the claimed one: this test never creates an
    # unclaimed node itself, so the only node on the plot is the one
    # `--provision` made and `run()` claimed with its claim code.
    node = (
        await db_session.execute(select(NodeRow).where(NodeRow.plot_id == plot_id))
    ).scalar_one()
    assert node.claim_code is not None and node.claim_code.startswith("SIM-")
    assert node.org_id == org_id
    mqtt = _OPENED_MQTT[-1]
    assert mqtt.credentials[0]
    assert mqtt.endpoint == ("localhost", 1883)

    uplinks = [payload for _, payload in mqtt.connection.published]
    assert [topic for topic, _ in mqtt.connection.published] == [f"tc/v1/{node.id}/up"] * (
        _BACKFILL_COUNT + 1
    )
    assert [u["seq"] for u in uplinks] == list(range(1, _BACKFILL_COUNT + 2))

    # The live loop continues the same trajectory and the same counter, instead
    # of replaying the backfill's first point and restarting `seq`.
    assert live_calls[0]["start_seq"] == _BACKFILL_COUNT + 1
    assert live_calls[0]["start_index"] == _BACKFILL_COUNT
    assert uplinks[-1]["m"]["sm_10"] == pytest.approx(raw_value_at(_BACKFILL_COUNT, seed=0))
    assert uplinks[-1]["m"]["sm_10"] != pytest.approx(raw_value_at(0, seed=0))

    # The calibration's window opens exactly where the backfill opens, so every
    # backfilled uplink is calibrated: valid at the earliest timestamp, not one
    # second before it, and still valid at the last one.
    sensor = (
        await db_session.execute(select(SensorRow).where(SensorRow.node_id == node.id))
    ).scalar_one()
    valid_from = (
        await db_session.execute(
            select(CalibrationRow.valid_from).where(CalibrationRow.sensor_id == sensor.id)
        )
    ).scalar_one()
    calibrations = SqlAlchemyCalibrationRepository(db_session)
    first_backfill_at = datetime.fromtimestamp(uplinks[0]["ts"], UTC)
    assert int(valid_from.timestamp()) == uplinks[0]["ts"]
    assert await calibrations.get_latest_valid_at(sensor.id, org_id, first_backfill_at) is not None
    assert (
        await calibrations.get_latest_valid_at(
            sensor.id, org_id, first_backfill_at - timedelta(seconds=1)
        )
        is None
    )
    last_backfill_at = datetime.fromtimestamp(uplinks[-2]["ts"], UTC)
    assert await calibrations.get_latest_valid_at(sensor.id, org_id, last_backfill_at) is not None
