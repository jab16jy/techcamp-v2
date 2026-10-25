"""Defers one per-organization alerts job that no HTTP route triggers.

Three alerts jobs decide on evidence no `/dev/jobs` name reaches: the node
health sweep (`node_offline`, every 5 min), the fungal risk sweep (its own
04:45 periodic, docs/10 §3), and the escalation sweep (a critical's 2 h clock).
The harness needs the running worker — not an in-process call — to execute the
real job path, so this runner defers the named task with the org the harness
names, on the queue the worker listens to. The sweep's own `source` only lives
in its lock (alerts/adapters/jobs.py), never in the args: every one of these
takes `org_id` alone.

Prints `{"job_id": ...}` as the last stdout line.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable

from techcamp.alerts.adapters.jobs import (
    QUEUE_NAME,
    escalate_org_alerts,
    evaluate_org_fungal_risk,
    evaluate_org_node_health,
)
from techcamp.shared.jobs import app as jobs_app

Task = Callable[..., Awaitable[object]]

TASKS: dict[str, Task] = {
    evaluate_org_node_health.name: evaluate_org_node_health,
    evaluate_org_fungal_risk.name: evaluate_org_fungal_risk,
    escalate_org_alerts.name: escalate_org_alerts,
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, choices=sorted(TASKS))
    parser.add_argument("--org-id", required=True, type=uuid.UUID)
    return parser.parse_args(argv)


async def defer(task_name: str, org_id: uuid.UUID) -> int:
    task = TASKS[task_name]
    async with jobs_app.open_async():
        job_id = await task.configure(queue=QUEUE_NAME).defer_async(org_id=str(org_id))
    return int(job_id)


def main() -> None:
    args = parse_args()
    print(json.dumps({"job_id": asyncio.run(defer(args.task, args.org_id))}))


if __name__ == "__main__":
    main()
