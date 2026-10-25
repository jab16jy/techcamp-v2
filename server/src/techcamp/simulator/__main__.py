"""`python -m techcamp.simulator`: the seminar-profile node simulator
(docs/06-diseno-detallado.md §10; ADR-0021).

Three entry shapes share this module. The E4 single-node path keeps its flat
flags (`--phone/--plot-id/--claim-code/--provision`). The E16 T2 `run`
subcommand (docs/06:408: `sim run --scenario el-nino --backfill 14d --live`)
provisions everything the scenario declares (D-T16.5) and publishes
multi-node trajectories with faults, seed and truth export. The E16 T3
`record-weather` capture (docs/06:490; D-T16.3) records a scenario's weather
fixture from Open-Meteo once; it dispatches in `main()` ahead of the
subparsers, as its own reviewed lane shaped it."""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from contextlib import AsyncExitStack
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import aiomqtt
import httpx

from techcamp.shared.config import is_seminar_profile
from techcamp.shared.db import async_session_factory
from techcamp.simulator.node_client import claim_node, ensure_calibrations, request_otp, verify_otp
from techcamp.simulator.provision import provision_scenario, provision_unclaimed_node
from techcamp.simulator.publisher import MqttUplinkPublisher
from techcamp.simulator.recorder import (
    RECORD_COMMAND,
    RecorderError,
    fixture_cell,
    record_weather,
    scenario_fixture_name,
)
from techcamp.simulator.runner import (
    LIVE_INTERVAL_S,
    NodePlan,
    publish_backfill,
    publish_live,
    publish_scenario_backfill,
    publish_scenario_live,
)
from techcamp.simulator.scenario_library import SCENARIO_NAMES, SCENARIOS_DIR
from techcamp.simulator.scenario_schema import ScenarioSchemaError, load_scenario
from techcamp.simulator.trajectory import soil_two_point_params


def _duration_days(value: str) -> float:
    """`--backfill <days>d` (docs/06:408,450 write `14d` and `21d`): a plain
    positive number of days, nothing else — an ambiguous duration is a CLI
    error, not a guess."""
    if not re.fullmatch(r"\d+(\.\d+)?d", value):
        raise argparse.ArgumentTypeError(f"{value!r} must be a duration like 14d")
    days = float(value.removesuffix("d"))
    if days <= 0:
        raise argparse.ArgumentTypeError(f"{value!r} must be greater than 0")
    return days


def _common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--api", default="http://localhost:8000/api/v1", help="Base URL of the running api process."
    )
    parser.add_argument("--broker-host", default="localhost")
    parser.add_argument("--broker-port", type=int, default=1883)
    parser.add_argument(
        "--otp-code", default=None, help="Skip the interactive prompt (scripted runs)."
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m techcamp.simulator",
        description="TechCamp node simulator (docs/06 §10): single node, or `run` for a scenario.",
    )
    _common_arguments(parser)
    parser.add_argument(
        "--phone", default=None, help="Phone of an existing org member (dev OTP, single-node path)."
    )
    parser.add_argument("--plot-id", default=None, type=UUID)
    parser.add_argument("--claim-code", default=None, help="Claim an existing unclaimed node.")
    parser.add_argument(
        "--provision",
        action="store_true",
        help="Provision a fresh unclaimed node first (dev-only DB shortcut, see provision.py).",
    )
    parser.add_argument("--backfill-days", type=float, default=1.0)
    parser.add_argument("--interval-s", type=int, default=900, help="Backfill spacing, in seconds.")
    parser.add_argument(
        "--live", action="store_true", help="Keep publishing every 5s after backfill."
    )
    parser.add_argument("--seed", type=int, default=0)

    subcommands = parser.add_subparsers(dest="command")
    run_parser = subcommands.add_parser(
        "run", help="Run a scenario from the library (docs/06 §10, E16)."
    )
    _common_arguments(run_parser)
    run_parser.add_argument("--scenario", required=True, choices=None, help="Scenario name.")
    run_parser.add_argument(
        "--backfill",
        required=True,
        dest="backfill_days",
        type=_duration_days,
        help="Backfill duration, e.g. 14d or 21d.",
    )
    run_parser.add_argument("--live", action="store_true", help="Keep publishing every 5s.")
    run_parser.add_argument(
        "--seed", type=int, default=0, help="Deterministic seed (D-T16.7; default 0)."
    )
    run_parser.add_argument(
        "--truth-export",
        default=None,
        metavar="PATH",
        help="Noiseless-truth export path (default: <scenario>-truth.json in the cwd).",
    )

    args = parser.parse_args(argv)
    if args.command is None:
        # Single-node path: its required flags are checked here, not on the
        # parser, so the `run` subcommand does not have to carry them too.
        if args.phone is None:
            parser.error("--phone is required")
        if args.plot_id is None:
            parser.error("--plot-id is required")
        if args.claim_code is None and not args.provision:
            parser.error("either --claim-code or --provision is required")
        if args.backfill_days <= 0:
            parser.error("--backfill-days must be greater than 0")
        if args.interval_s <= 0:
            parser.error("--interval-s must be greater than 0")
    return args


async def _login(client: httpx.AsyncClient, *, phone: str, otp_code: str | None) -> str:
    """docs/04-api.md:173: the OTP code is printed to the `api` process
    console, not returned by any endpoint — this simulator has no remote way
    to read it, so `--otp-code` (scripted runs) or an interactive prompt
    (demo runs, reading it off the visible api terminal) fills the gap. See
    the task report's Gaps.

    A code is only requested when the caller has none: `otp_store.issue`
    overwrites the previous code for that phone, so requesting one
    unconditionally invalidated the very code `--otp-code` was given."""
    if otp_code is None:
        await request_otp(client, phone)
        otp_code = input(f"OTP for {phone} (printed to the api process console): ").strip()
    return await verify_otp(client, phone, otp_code)


async def _run_scenario(args: argparse.Namespace) -> None:
    """The `run` subcommand (docs/06:408-419): load the scenario, provision
    org/farm/plot/cycle/nodes (D-T16.5), claim every node over HTTP with its
    own calibration, then backfill (and optionally go live) publishing through
    one MQTT connection per node. The runner never touches `weather_daily`;
    T3's `/dev/scenarios/{name}:load` loads the fixture."""
    try:
        scenario = load_scenario(args.scenario)
    except FileNotFoundError:
        raise SystemExit(
            f"unknown scenario {args.scenario!r}; available: {', '.join(SCENARIO_NAMES)}"
        ) from None
    except ScenarioSchemaError as error:
        raise SystemExit(f"invalid scenario {args.scenario!r}: {error}") from error

    now = datetime.now(UTC)
    async with async_session_factory() as session:
        provision = await provision_scenario(session, scenario, now=now)
    print(f"[sim] scenario {scenario.name}: member {provision.phone}, plot {provision.plot_id}")
    if not scenario.nodes.reads_soil:
        print("[sim] node-less scenario: plot and cycle provisioned, nothing to publish")
        return

    async with httpx.AsyncClient(base_url=args.api) as client:
        token = await _login(client, phone=provision.phone, otp_code=args.otp_code)
        # R3-calibration-valid-from-after-backfill, as in the single-node path:
        # the window opens on the earliest backfilled uplink's whole second.
        earliest_backfill_at = (now - timedelta(days=args.backfill_days)).replace(microsecond=0)
        claimed = []
        for provisioned in provision.nodes:
            node = await claim_node(
                client, token=token, claim_code=provisioned.claim_code, plot_id=provision.plot_id
            )
            await ensure_calibrations(
                client,
                token=token,
                node=node,
                valid_from=earliest_backfill_at,
                soil_params=soil_two_point_params(provisioned.ordinal),
            )
            claimed.append((provisioned.ordinal, node))
            print(f"[sim] claimed node {node.node_id} (node {provisioned.ordinal})")

    truth_path = (
        Path(args.truth_export) if args.truth_export else Path(f"{scenario.name}-truth.json")
    )
    # One connection per node: the broker ACL authenticates each node as its
    # own user, allowed only its own topics (docs/04:294) — the same contract
    # a real node gets (H2).
    async with AsyncExitStack() as stack:
        plans = []
        for ordinal, node in claimed:
            mqtt_client = await stack.enter_async_context(
                aiomqtt.Client(
                    args.broker_host,
                    args.broker_port,
                    username=node.mqtt_username,
                    password=node.mqtt_password,
                )
            )
            plans.append(
                NodePlan(
                    node_id=node.node_id,
                    ordinal=ordinal,
                    sensors=node.sensors,
                    publisher=MqttUplinkPublisher(mqtt_client),
                )
            )
        ticks = await publish_scenario_backfill(
            plans,
            scenario=scenario,
            days=args.backfill_days,
            seed=args.seed,
            now=now,
            truth_path=truth_path,
        )
        print(f"[sim] backfilled {ticks} tick(s) per node for {len(plans)} node(s)")
        print(f"[sim] noiseless truth exported to {truth_path}")
        if args.live:
            print(f"[sim] live: publishing every {LIVE_INTERVAL_S}s (Ctrl+C to stop)")
            await publish_scenario_live(plans, scenario=scenario, seed=args.seed, after=ticks)


async def run(args: argparse.Namespace) -> None:
    if not is_seminar_profile():
        raise SystemExit("the simulator is a seminar-profile-only dev tool (ADR-0021)")

    if args.command == "run":
        await _run_scenario(args)
        return

    claim_code = args.claim_code
    if args.provision:
        async with async_session_factory() as session:
            claim_code = await provision_unclaimed_node(session)
        print(f"[sim] provisioned node, claim_code={claim_code}")
    assert claim_code is not None  # parse_args() already enforced this

    async with httpx.AsyncClient(base_url=args.api) as client:
        token = await _login(client, phone=args.phone, otp_code=args.otp_code)
        node = await claim_node(client, token=token, claim_code=claim_code, plot_id=args.plot_id)
        now = datetime.now(UTC)
        # R3-calibration-valid-from-after-backfill: every backfilled uplink has
        # ts < now, so valid_from must cover the earliest one or ingest finds
        # no calibration for it (docs/06 §10; node_client.ensure_calibrations).
        # An uplink `ts` is whole epoch seconds (docs/04-api.md:218) and the
        # earliest one is `int((now - backfill_days).timestamp())`, so the window
        # opens on that same whole second: keeping `now`'s microseconds would
        # leave the first backfilled uplink a fraction of a second *before* it,
        # and ingest would store it uncalibrated.
        earliest_backfill_at = (now - timedelta(days=args.backfill_days)).replace(microsecond=0)
        await ensure_calibrations(client, token=token, node=node, valid_from=earliest_backfill_at)

    async with aiomqtt.Client(
        args.broker_host, args.broker_port, username=node.mqtt_username, password=node.mqtt_password
    ) as mqtt_client:
        publisher = MqttUplinkPublisher(mqtt_client)
        backfilled = await publish_backfill(
            publisher,
            node.node_id,
            sensors=node.sensors,
            days=args.backfill_days,
            interval_s=args.interval_s,
            seed=args.seed,
            now=now,
        )
        print(f"[sim] backfilled {backfilled} uplink(s) for node {node.node_id}")
        if args.live:
            print(f"[sim] live: publishing every {LIVE_INTERVAL_S}s (Ctrl+C to stop)")
            # `after` carries the trajectory position and the `seq` counter at
            # once, so the live loop cannot drift from the backfill it follows.
            await publish_live(
                publisher, node.node_id, sensors=node.sensors, seed=args.seed, after=backfilled
            )


def parse_record_weather_args(argv: list[str] | None = None) -> argparse.Namespace:
    """`sim record-weather`: the capture that leaves a fixture behind
    (docs/06-diseno-detallado.md:490; D-T16.3)."""
    parser = argparse.ArgumentParser(
        prog=f"python -m techcamp.simulator {RECORD_COMMAND}",
        description="Record a scenario's weather fixture from Open-Meteo (docs/06 §10).",
    )
    parser.add_argument("--scenario", required=True, help="Scenario the fixture belongs to.")
    parser.add_argument(
        "--from",
        dest="from_day",
        required=True,
        type=date.fromisoformat,
        help="First day of the capture (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--days",
        required=True,
        type=int,
        help="Days to capture: one observed day plus the seven-day forecast window.",
    )
    parser.add_argument(
        "--name", default=None, help="Fixture name (defaults to the one the scenario pins)."
    )
    parser.add_argument(
        "--lat", type=float, default=None, help="Cell latitude (defaults to the recorded cell)."
    )
    parser.add_argument(
        "--lon", type=float, default=None, help="Cell longitude (defaults to the recorded cell)."
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Where to write the fixture (defaults to the library's weather directory).",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Write a synthetic capture instead of recording one (D-T16.3: last resort).",
    )
    parser.add_argument(
        "--synthetic-reason",
        default=None,
        help="Why the recording could not reproduce the scenario's arithmetic.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace the fixture that is already recorded (it breaks the scenario's pin).",
    )
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days must be greater than 0")
    if args.synthetic and not (args.synthetic_reason or "").strip():
        parser.error("--synthetic requires --synthetic-reason")
    return args


async def record_weather_command(args: argparse.Namespace) -> None:
    """Capture the window and write the fixture the scenario library reads."""
    try:
        name = args.name or scenario_fixture_name(args.scenario)
        if args.lat is None or args.lon is None:
            lat, lon = fixture_cell(name)
        else:
            lat, lon = args.lat, args.lon
        out_dir = Path(args.out_dir) if args.out_dir else SCENARIOS_DIR / "weather"

        recorded = await record_weather(
            scenario=args.scenario,
            name=name,
            from_day=args.from_day,
            days=args.days,
            lat=lat,
            lon=lon,
            out_dir=out_dir,
            synthetic=args.synthetic,
            synthetic_reason=args.synthetic_reason,
            force=args.force,
        )
    except RecorderError as error:
        # Every refusal the recorder states — the scenario is not in the
        # library, it pins no fixture, there is no cell to read, the fixture is
        # already recorded, the window is too short — is a message the
        # operator reads, not a stack trace.
        raise SystemExit(str(error)) from error
    print(f"[sim] recorded {recorded.path}")
    print(f"[sim] data_sha256={recorded.data_sha256}")
    print(f"[sim] file_sha256={recorded.file_sha256}")
    print(f"[sim] update {args.scenario}.yaml's weather_fixture_sha256 with {recorded.file_sha256}")


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == RECORD_COMMAND:
        asyncio.run(record_weather_command(parse_record_weather_args(argv[1:])))
        return
    asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    main()
