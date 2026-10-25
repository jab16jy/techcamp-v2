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
from techcamp.simulator.runner import NodePlan, publish_scenario_backfill
from techcamp.simulator.scenario_schema import load_scenario
from techcamp.simulator.trajectory import soil_two_point_params


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
    return parser.parse_args(argv)


async def publish(token: str, args: argparse.Namespace) -> dict[str, int]:
    load = json.loads(args.load_json)
    scenario = load_scenario(str(load["scenario"]))
    credentials = list(load["node_credentials"])
    if not credentials:
        raise SystemExit("the 202 body carries no node credentials: nothing to publish for")

    now = datetime.now(UTC)
    earliest_backfill_at = (now - timedelta(days=args.days)).replace(microsecond=0)

    plans: list[NodePlan] = []
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
                    publisher=MqttUplinkPublisher(mqtt_client),
                )
            )
        ticks = await publish_scenario_backfill(
            plans, scenario=scenario, days=args.days, seed=args.seed, now=now
        )
    return {"ticks": ticks, "nodes": len(plans)}


def main() -> None:
    args = parse_args()
    print(json.dumps(asyncio.run(publish(args.token, args))))


if __name__ == "__main__":
    main()
