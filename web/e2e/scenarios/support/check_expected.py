"""The T4 `expected` checker, judged against the live stack for one plot.

The harness (web/e2e/scenarios, E16 T5) spawns this under the server's own
environment: the checker reads through the repositories the stack already
serves, and that composition exists only inside the server package — there is
no HTTP surface for it, and the oracle must stay the one T4 reviewed, not a
re-implementation. The runner is glue, nothing more.

Prints `ExpectedVerdict.as_dict()` as the last stdout line; the harness asserts
on it. The day defaults to the server's own local today — the same helper the
irrigation job defaults to — so the harness never computes a calendar day of
its own.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import uuid

from sqlalchemy import select

from techcamp.alerts.adapters.repositories import (
    SqlAlchemyAlertRepository,
    SqlAlchemyAlertRuleRepository,
)
from techcamp.farms.adapters.orm import PlotRow
from techcamp.irrigation.adapters.repositories import (
    SqlAlchemyIrrigationRecommendationRepository,
)
from techcamp.shared.db import async_session_factory
from techcamp.shared.dates import local_today
from techcamp.simulator.application.expected_checker import (
    check_expected,
    collect_observed_state,
)
from techcamp.simulator.scenario_schema import load_scenario


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True, help="Scenario name in the library.")
    parser.add_argument("--plot-id", required=True, type=uuid.UUID)
    parser.add_argument(
        "--day",
        type=datetime.date.fromisoformat,
        default=None,
        help="The recommendation day (defaults to the server's local today).",
    )
    parser.add_argument(
        "--node-ids",
        default="",
        help="Comma-separated node UUIDs the scenario claims (node-scoped alerts).",
    )
    return parser.parse_args(argv)


async def verdict_dict(args: argparse.Namespace) -> dict[str, object]:
    scenario = load_scenario(args.scenario)
    day = args.day if args.day is not None else local_today()
    node_ids = [uuid.UUID(value) for value in args.node_ids.split(",") if value]
    async with async_session_factory() as session:
        org_id = (
            await session.execute(select(PlotRow.org_id).where(PlotRow.id == args.plot_id))
        ).scalar_one()
        observed = await collect_observed_state(
            org_id=org_id,
            plot_id=args.plot_id,
            day=day,
            node_ids=node_ids,
            alerts=SqlAlchemyAlertRepository(session),
            rules=SqlAlchemyAlertRuleRepository(session),
            recommendations=SqlAlchemyIrrigationRecommendationRepository(session),
        )
    return check_expected(scenario, observed).as_dict()


def main() -> None:
    print(json.dumps(asyncio.run(verdict_dict(parse_args()))))


if __name__ == "__main__":
    main()
