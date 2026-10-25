"""A clean slate for the scenario e2e: every org a run left, gone.

The scenario runs' identity is per run — `sim run` names its organization
"Simulador <scenario>" (D-T16.5) and the loader loads into the caller's own —
but the loader's plot name ("Escenario <scenario> <ancla>") is GLOBAL
(scenario_data.py), so a rerun of the same scenario on the same day answers
404 in-another-org unless the last run's organizations are gone. CI never
needs this (a fresh database per job); a worktree does, and the SQL this
replaces lived in /tmp through T5 — this file is where it stops being
ephemeral.

`TRUNCATE ... CASCADE` reaches every table the organizations reference into
(alerts, notifications, readings, balances, nodes, memberships — the same
cascade the test teardown relies on), which also wipes the factory alert
rules; they are re-seeded right after, the way `tests/alerts` does it. The
procrastinate queue joins the truncate so a killed run's leftover jobs never
fire against the empty orgs.

Prints `{"organizations": <n>}` — how many scenario orgs this was — as the
last stdout line.
"""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import text

from techcamp.alerts.adapters.seed import seed_factory_rules
from techcamp.shared.db import async_session_factory

TRUNCATE_SQL = text("TRUNCATE organization, app_user, procrastinate_jobs CASCADE")
"""`procrastinate_events` and `procrastinate_periodic_defers` reference the
jobs with `ON DELETE CASCADE`, so truncating the jobs reaches them."""


async def clean() -> dict[str, int]:
    async with async_session_factory() as session:
        count = int(
            (
                await session.execute(
                    text("SELECT count(*) FROM organization WHERE name LIKE 'Simulador %'")
                )
            ).scalar_one()
        )
        await session.execute(TRUNCATE_SQL)
        await seed_factory_rules(await session.connection())
        await session.commit()
    return {"organizations": count}


def main() -> None:
    print(json.dumps(asyncio.run(clean())))


if __name__ == "__main__":
    main()
