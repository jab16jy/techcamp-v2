"""`python -m techcamp.simulator`: the single-node MQTT publish path for the
seminar profile (docs/06-diseno-detallado.md §10; ADR-0021). Scenario YAML,
weather fixtures, faults, `/dev/scenarios`/`/dev/jobs` calls and multi-node
runs are E16 — out of scope here."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import aiomqtt
import httpx

from techcamp.shared.config import is_seminar_profile
from techcamp.shared.db import async_session_factory
from techcamp.simulator.node_client import claim_node, ensure_calibrations, request_otp, verify_otp
from techcamp.simulator.provision import provision_unclaimed_node
from techcamp.simulator.publisher import MqttUplinkPublisher
from techcamp.simulator.runner import LIVE_INTERVAL_S, publish_backfill, publish_live


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
        next_seq = await publish_backfill(
            publisher,
            node.node_id,
            sensors=node.sensors,
            days=args.backfill_days,
            interval_s=args.interval_s,
            seed=args.seed,
            now=now,
        )
        print(f"[sim] backfilled {next_seq - 1} uplink(s) for node {node.node_id}")
        if args.live:
            print(f"[sim] live: publishing every {LIVE_INTERVAL_S}s (Ctrl+C to stop)")
            await publish_live(
                publisher,
                node.node_id,
                sensors=node.sensors,
                seed=args.seed,
                start_seq=next_seq,
                # `publish_backfill` starts at seq 1, so the count of points it
                # published is where the live loop has to continue the
                # trajectory (docs/06 §10: "la lectura siguiente de la
                # trayectoria") instead of replaying its first one.
                start_index=next_seq - 1,
            )


def main() -> None:
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
