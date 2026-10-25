"""Defers the balance sweep of `water_stress` for one plot's organization.

The balance branch of `water_stress` has no HTTP trigger: it is the 04:50
sweep's own procrastinate task (docs/06 §3, docs/10 §3 — the irrigation job
never calls it, docs/05 wires no `irrigation → alerts` edge). The harness
needs the running worker — not an in-process call — to execute the real job
path, so this runner resolves the organization the plot belongs to and defers
the same task the sweep defers, on the queue the worker listens to.

Prints `{"job_id": ...}` as the last stdout line.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid

from sqlalchemy import select

from techcamp.alerts.adapters.jobs import QUEUE_NAME, evaluate_org_balance_rules
from techcamp.farms.adapters.orm import PlotRow
from techcamp.shared.db import async_session_factory
from techcamp.shared.jobs import app as jobs_app


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plot-id", required=True, type=uuid.UUID)
    return parser.parse_args(argv)


async def defer_for_plot(plot_id: uuid.UUID) -> int:
    async with async_session_factory() as session:
        org_id = (
            await session.execute(select(PlotRow.org_id).where(PlotRow.id == plot_id))
        ).scalar_one()
    async with jobs_app.open_async():
        job_id = await evaluate_org_balance_rules.configure(queue=QUEUE_NAME).defer_async(
            org_id=str(org_id)
        )
    return job_id


def main() -> None:
    print(json.dumps({"job_id": asyncio.run(defer_for_plot(parse_args().plot_id))}))


if __name__ == "__main__":
    main()
