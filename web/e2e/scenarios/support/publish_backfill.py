"""Publishes the scenario backfill to the LOADER's nodes (D-T16.11).

`sim run --scenario X` (docs/06:408) provisions and publishes its own
organization; the loader (`POST /dev/scenarios/{name}:load`, docs/04:263)
prepares the plot "Escenario X <ancla>" with its own already-claimed nodes —
the 202 carries their MQTT credentials, and no calibration exists for them yet,
so raw ADC uplinks would land uncalibrated. This runner closes that gap for the
harness (web/e2e/scenarios, E16 T6): for each node of the 202, in order, it
reads the node's sensors and posts the calibrations the ingest path needs
(`valid_from` covering the retroactive window on the whole second — the R3 rule
`sim run` itself follows: the earliest backfilled uplink's `ts` is a whole epoch
second, and microsecond-bearing `valid_from` would leave that first uplink a
fraction of a second before its own calibration window), then publishes the
scenario's backfill reusing the simulator's own `publish_scenario_backfill`
over ONE MQTT connection per node — the broker ACL authenticates each node as
its own user (docs/04:294), the same contract a real node gets.

The caller (the harness) logs the scenario member in over the dev OTP and hands
this runner the bearer token; everything below runs inside the server's own
environment, like the other runners in this directory.

Prints `{"ticks": ..., "nodes": ...}` as the last stdout line.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import aiomqtt
import httpx

from techcamp.simulator.node_client import ClaimedNode, ensure_calibrations
from techcamp.simulator.publisher import MqttUplinkPublisher
from techcamp.simulator.runner import (
    LIVE_INTERVAL_S,
    NodePlan,
    publish_scenario_backfill,
    publish_scenario_live,
)
from techcamp.simulator.scenario_schema import load_scenario
from techcamp.simulator.trajectory import soil_two_point_params


class PacedPublisher:
    """A publisher that breathes: one small delay per uplink.

    The ingestor decides per FLUSH (500 uplinks or 1 s), and the reading branch
    of `water_stress` opens and upgrades (D5's 48 h) at flush boundaries
    measured on the trajectory's own clock. A backfill pumped as fast as the
    broker takes it lands its boundaries 2.6 trajectory-days apart, which
    pushed the opening past the upgrade's reach on the recorded pipeline. A
    50 ms breath per uplink gives the ingestor ~1 s flushes — about 0.1
    trajectory-days — the granularity a real 15-minute node stream has, so the
    alert opens at the documented crossing and the 48 h upgrade fits inside
    the window. Pure compression repair: the same uplinks, the same order, a
    realistic arrival cadence.
    """

    def __init__(self, inner: MqttUplinkPublisher, delay_s: float) -> None:
        self._inner = inner
        self._delay_s = delay_s

    async def publish_uplink(self, node_id: UUID, payload: dict[str, Any]) -> None:
        if self._delay_s > 0:
            await asyncio.sleep(self._delay_s)
        await self._inner.publish_uplink(node_id, payload)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--api", default="http://localhost:8000/api/v1")
    parser.add_argument("--broker-host", default="localhost")
    parser.add_argument("--broker-port", type=int, default=1883)
    parser.add_argument("--token", required=True, help="The scenario member's bearer token.")
    parser.add_argument(
        "--load-json",
        required=True,
        help="The load's 202 body (scenario, plot_ids, node_ids, node_credentials).",
    )
    parser.add_argument("--days", type=float, required=True, help="Backfill duration, in days.")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--tick-delay-ms",
        type=float,
        default=0,
        help="Delay per published uplink, in milliseconds: the pacing that keeps "
        "the ingestor's flush boundaries fine-grained against the trajectory "
        "(the D5 upgrade measures 48 h of it).",
    )
    parser.add_argument(
        "--live-seconds",
        type=int,
        default=0,
        help="Keep publishing every 5 s for this long after the backfill "
        "(docs/06:408's --live, bounded): the healthy node keeps reporting while "
        "a fault keeps a failed one silent (D-T16.13).",
    )
    return parser.parse_args(argv)


async def publish(token: str, args: argparse.Namespace) -> dict[str, int]:
    load = json.loads(args.load_json)
    scenario = load_scenario(str(load["scenario"]))
    credentials = list(load["node_credentials"])
    if not credentials:
        raise SystemExit("the 202 body carries no node credentials: nothing to publish for")

    now = datetime.now(UTC)
    earliest_backfill_at = (now - timedelta(days=args.days)).replace(microsecond=0)
    delay_s = args.tick_delay_ms / 1000.0

    plans: list[NodePlan] = []
    ticks = 0
    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(httpx.AsyncClient(base_url=args.api))
        for ordinal, credential in enumerate(credentials, start=1):
            sensors_response = await client.get(
                f"/nodes/{credential['node_id']}/sensors",
                headers={"Authorization": f"Bearer {token}"},
            )
            sensors_response.raise_for_status()
            sensors: list[dict[str, Any]] = sensors_response.json()
            await ensure_calibrations(
                client,
                token=token,
                node=ClaimedNode(
                    node_id=UUID(str(credential["node_id"])),
                    mqtt_username=str(credential["mqtt_username"]),
                    mqtt_password=str(credential["mqtt_password"]),
                    sensors=sensors,
                ),
                valid_from=earliest_backfill_at,
                # The same per-node coefficients the runner inverts: what
                # calibrates the readings is exactly what produced them.
                soil_params=soil_two_point_params(ordinal),
            )
            mqtt_client = await stack.enter_async_context(
                aiomqtt.Client(
                    args.broker_host,
                    args.broker_port,
                    username=str(credential["mqtt_username"]),
                    password=str(credential["mqtt_password"]),
                )
            )
            plans.append(
                NodePlan(
                    node_id=UUID(str(credential["node_id"])),
                    ordinal=ordinal,
                    sensors=sensors,
                    publisher=PacedPublisher(MqttUplinkPublisher(mqtt_client), delay_s),
                )
            )
        ticks = await publish_scenario_backfill(
            plans, scenario=scenario, days=args.days, seed=args.seed, now=now
        )
        if args.live_seconds > 0:
            # Bounded live (docs/06:408's --live): the trajectory and every seq
            # continue from the backfill's tick count, the faults pausing the
            # nodes they name — the failed node stays silent in real time while
            # the healthy one keeps reporting.
            await publish_scenario_live(
                plans,
                scenario=scenario,
                seed=args.seed,
                after=ticks,
                iterations=args.live_seconds // LIVE_INTERVAL_S,
            )
    return {"ticks": ticks, "nodes": len(plans)}


def main() -> None:
    args = parse_args()
    print(json.dumps(asyncio.run(publish(args.token, args))))


if __name__ == "__main__":
    main()
