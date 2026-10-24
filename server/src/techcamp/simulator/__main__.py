"""`python -m techcamp.simulator`: the single-node MQTT publish path for the
seminar profile (docs/06-diseno-detallado.md §10; ADR-0021), plus the
`record-weather` subcommand that records a scenario's weather fixture
(docs/06-diseno-detallado.md:490; D-T16.3). Scenario YAML, faults,
`/dev/scenarios`/`/dev/jobs` calls and multi-node runs are E16 — out of scope
here."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import aiomqtt
import httpx

from techcamp.shared.config import is_seminar_profile
from techcamp.shared.db import async_session_factory
from techcamp.simulator.node_client import claim_node, ensure_calibrations, request_otp, verify_otp
from techcamp.simulator.provision import provision_unclaimed_node
from techcamp.simulator.publisher import MqttUplinkPublisher
from techcamp.simulator.recorder import (
    RECORD_COMMAND,
    RecorderError,
    fixture_cell,
    record_weather,
    scenario_fixture_name,
)
from techcamp.simulator.runner import LIVE_INTERVAL_S, publish_backfill, publish_live
from techcamp.simulator.scenario_library import SCENARIOS_DIR


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m techcamp.simulator",
        description="TechCamp node simulator: single-node MQTT publish path (docs/06 §10).",
    )
    parser.add_argument(
        "--api", default="http://localhost:8000/api/v1", help="Base URL of the running api process."
    )
    parser.add_argument("--broker-host", default="localhost")
    parser.add_argument("--broker-port", type=int, default=1883)
    parser.add_argument("--phone", required=True, help="Phone of an existing org member (dev OTP).")
    parser.add_argument(
        "--otp-code", default=None, help="Skip the interactive prompt (scripted runs)."
    )
    parser.add_argument("--plot-id", required=True, type=UUID)
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
    args = parser.parse_args(argv)
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


async def run(args: argparse.Namespace) -> None:
    if not is_seminar_profile():
        raise SystemExit("the simulator is a seminar-profile-only dev tool (ADR-0021)")

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
