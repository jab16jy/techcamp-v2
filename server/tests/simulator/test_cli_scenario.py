"""E16 T2: `sim run --scenario <name> --backfill <dur> [--live]`
(docs/06-diseno-detallado.md:408): the `run` subcommand beside the legacy
single-node flags, duration parsing, the clean unknown-scenario error, and the
one-command flow D-T16.5 promises — provisioning absorbed, nodes claimed
through the same HTTP path a real claim uses, per-node calibrations posted,
multi-node backfill published over the same MQTT contract (H2, docs/06:396).

The api is the real FastAPI app over an in-process ASGI transport and the
broker is a fake (as in test_cli_run.py), so MQTT stays the only external I/O
doubled (ADR-0002)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.farms.adapters.orm import CropCycleRow
from techcamp.identity.adapters.security.otp_store import otp_store
from techcamp.main import app
from techcamp.simulator import __main__ as cli
from techcamp.simulator.trajectory import soil_two_point_params
from techcamp.telemetry.adapters.orm import CalibrationRow, NodeRow, SensorRow

pytestmark = pytest.mark.anyio


def _in_process_client(base_url: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url)


class _FakeMqttConnection:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, topic: str, payload: bytes, qos: int = 0, retain: bool = False) -> None:
        self.published.append((topic, json.loads(payload)))


class _FakeMqttClient:
    """Stands in for `aiomqtt.Client`: MQTT is external I/O, so it gets a
    double (ADR-0002). One connection per node, as the broker ACL demands
    (docs/04:294)."""

    def __init__(
        self, host: str, port: int, username: str | None = None, password: str | None = None
    ) -> None:
        self.credentials = (username, password)
        self.connection = _FakeMqttConnection()

    async def __aenter__(self) -> _FakeMqttConnection:
        return self.connection

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _OpenedMqtt:
    def __init__(self) -> None:
        self.clients: list[_FakeMqttClient] = []

    def __call__(
        self, host: str, port: int, username: str | None = None, password: str | None = None
    ) -> _FakeMqttClient:
        client = _FakeMqttClient(host, port, username=username, password=password)
        self.clients.append(client)
        return client


def _otp_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scenario flow provisions its own member, so the test cannot pre-issue
    the OTP: `input` answers with the code the api just printed for whatever
    phone appeared in the store."""
    before = set(otp_store._codes)

    def _fake_input(prompt: str) -> str:
        new_phones = [phone for phone in otp_store._codes if phone not in before]
        assert len(new_phones) == 1, "the run requested exactly one OTP"
        return otp_store._codes[new_phones[0]][0]

    monkeypatch.setattr("builtins.input", _fake_input)


def test_parse_args_run_accepts_the_documented_invocation() -> None:
    args = cli.parse_args(["run", "--scenario", "el-nino", "--backfill", "14d", "--live"])

    assert args.command == "run"
    assert args.scenario == "el-nino"
    assert args.backfill_days == 14.0
    assert args.live is True
    assert args.seed == 0  # D-T16.7: a fixed default seed
    assert args.truth_export is None


def test_parse_args_run_requires_scenario_and_backfill() -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["run", "--backfill", "14d"])
    with pytest.raises(SystemExit):
        cli.parse_args(["run", "--scenario", "el-nino"])


@pytest.mark.parametrize("value,expected", [("14d", 14.0), ("21d", 21.0), ("0.5d", 0.5)])
def test_parse_args_run_parses_backfill_durations(value: str, expected: float) -> None:
    """docs/06:408,450 write `--backfill 14d` / `--backfill 21d`; the grammar
    is `<days>d` and nothing else."""

    args = cli.parse_args(["run", "--scenario", "el-nino", "--backfill", value])

    assert args.backfill_days == expected


@pytest.mark.parametrize("value", ["14", "d14", "14dd", "0d", "-3d", "1.4w", ""])
def test_parse_args_run_rejects_malformed_backfill_durations(value: str) -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["run", "--scenario", "el-nino", "--backfill", value])


def test_parse_args_legacy_flags_still_work_without_a_subcommand() -> None:
    """The single-node path (docs/06 §10, E4 T8) keeps its flat flags beside
    the new subcommand, so existing scripts and tests do not move."""

    args = cli.parse_args(
        ["--phone", "+573001112233", "--plot-id", str(UUID(int=3)), "--claim-code", "CODE1"]
    )

    assert args.command is None
    assert args.claim_code == "CODE1"
    assert args.backfill_days == 1.0


def test_parse_args_legacy_validations_still_reject_missing_claim_source() -> None:
    with pytest.raises(SystemExit):
        cli.parse_args(["--phone", "+573001112233", "--plot-id", str(UUID(int=3))])


async def test_run_reports_an_unknown_scenario_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo at the keyboard must read as a CLI error naming the library, not
    as a FileNotFoundError traceback (docs/06:408)."""
    monkeypatch.setattr(cli, "httpx", SimpleNamespace(AsyncClient=_in_process_client))

    with pytest.raises(SystemExit, match="no-such-scenario") as excinfo:
        await cli.run(
            cli.parse_args(["run", "--scenario", "no-such-scenario", "--backfill", "14d"])
        )

    assert "el-nino" in str(excinfo.value)  # the error names the library


async def test_run_scenario_provisions_claims_calibrates_and_publishes(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The one-command A–E promise (docs/06:408-419): from a healthy stack, the
    run creates the org/farm/plot/cycle/nodes (D-T16.5), claims every node over
    HTTP, posts each node its own calibration (H3), and backfills multi-node
    raw-ADC trajectories over the same MQTT contract as a real node (H2)."""
    truth_path = tmp_path / "truth.json"
    opened = _OpenedMqtt()
    monkeypatch.setattr(cli, "httpx", SimpleNamespace(AsyncClient=_in_process_client))
    monkeypatch.setattr(cli, "aiomqtt", SimpleNamespace(Client=opened))
    _otp_input(monkeypatch)

    await cli.run(
        cli.parse_args(
            [
                "run",
                "--scenario",
                "el-nino",
                "--backfill",
                "0.05d",  # 4320 s -> 4 uplinks per node at 900 s
                "--truth-export",
                str(truth_path),
            ]
        )
    )

    # both provisioned nodes were claimed onto the provisioned plot
    nodes = (
        (await db_session.execute(select(NodeRow).where(NodeRow.plot_id.is_not(None))))
        .scalars()
        .all()
    )
    assert len(nodes) == 2
    assert len({node.plot_id for node in nodes}) == 1

    # one MQTT connection per node, authenticated as that node
    assert len(opened.clients) == 2
    assert {client.credentials[0] for client in opened.clients} == {str(node.id) for node in nodes}

    # each node published its own seq series with raw soil ADC
    by_username = {client.credentials[0]: client.connection.published for client in opened.clients}
    for node in nodes:
        uplinks = [payload for _, payload in by_username[str(node.id)]]
        assert [topic for topic, _ in by_username[str(node.id)]] == [f"tc/v1/{node.id}/up"] * 4
        assert [u["seq"] for u in uplinks] == [1, 2, 3, 4]
        assert all(set(u["m"]) == {"sm_30", "at", "rh"} for u in uplinks)

    # every soil sensor carries its node's own two-point calibration (H3)
    rows = (await db_session.execute(select(CalibrationRow, SensorRow).join(SensorRow))).all()
    soil_calibrations = [row for row in rows if row[1].metric == "soil_moisture"]
    assert len(soil_calibrations) == 2
    # params distinct per node and matching the runner's inversion source
    params_list = [calibration.params for calibration, _ in soil_calibrations]
    assert params_list[0] != params_list[1]
    assert {json.dumps(p, sort_keys=True) for p in params_list} == {
        json.dumps(soil_two_point_params(o), sort_keys=True) for o in (1, 2)
    }

    # the noiseless truth export was written separately (D-T16.7, docs/06:487)
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    assert truth["scenario"] == "el-nino"
    assert len(truth["nodes"]) == 2
    assert len(truth["nodes"][0]["channels"]["soil_moisture_30cm"]) == 4


async def test_run_scenario_without_nodes_provisions_the_plot_and_publishes_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario E (docs/06:450,457): `--scenario dry-spell-rainfed` provisions
    the rainfed plot and its cycle, then has nothing to publish — the balance
    runs on the weather fixture T3's route loads, not on any node."""
    opened = _OpenedMqtt()
    monkeypatch.setattr(cli, "httpx", SimpleNamespace(AsyncClient=_in_process_client))
    monkeypatch.setattr(cli, "aiomqtt", SimpleNamespace(Client=opened))
    _otp_input(monkeypatch)

    await cli.run(cli.parse_args(["run", "--scenario", "dry-spell-rainfed", "--backfill", "1d"]))

    assert opened.clients == []
    nodes = (await db_session.execute(select(NodeRow))).scalars().all()
    assert nodes == []
    cycle = (await db_session.execute(select(CropCycleRow))).scalar_one()
    assert cycle is not None  # the cycle the balance anchors on exists
