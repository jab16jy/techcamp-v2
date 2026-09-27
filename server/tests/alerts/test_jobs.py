"""The node-health sweep in the worker (docs/06 §3 "Salud del nodo";
docs/10 §3 `m[cada 5 min: salud de nodos]`; D21, ADR-0012).

One job per organization per 5 minutes, then one job per organization that
pages its own nodes. The job coroutines are called directly, never through the
scheduler: what matters is the work the queue is asked to do and the alert it
lands. The rows are leaner than in `test_node_health.py` — no people, no
sensor: the sweep only needs claimed nodes, and the technician of the farm is
resolved by `open_alert` (D4) whether or not a test person is attached.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.jobs import (
    EVALUATE_ORG_TASK_NAME,
    QUEUE_NAME,
    evaluate_org_node_health,
    sweep_node_health,
)
from techcamp.alerts.adapters.orm import AlertRow, AlertRuleRow
from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import OrganizationRow
from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.orm import NodeRow

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)
_SILENT = timedelta(hours=2)  # past 3 × `interval_s` of 300 s
_RECENT = timedelta(minutes=1)


@pytest.fixture(autouse=True)
async def _clear_jobs(db_session: AsyncSession):
    """`procrastinate_jobs` is keyed by org id and `db_session`'s own cleanup
    truncates only the organization tables, so a `todo` job left by one test
    would make the next test's per-org defer a no-op (the `queueing_lock`
    refuses a duplicate) and hide a broken fan-out."""
    yield
    await db_session.execute(text("DELETE FROM procrastinate_jobs"))
    await db_session.commit()


@dataclass(frozen=True, slots=True)
class Org:
    org_id: UUID
    farm_id: UUID
    plot_id: UUID


async def _make_org(db_session: AsyncSession) -> Org:
    """One organization with a farm and a plot, the two a claimed node needs."""
    org_id, farm_id, plot_id = uuid7(), uuid7(), uuid7()
    db_session.add(OrganizationRow(id=org_id, name="Test Org", kind="individual"))
    await db_session.commit()
    db_session.add(
        FarmRow(
            id=farm_id,
            org_id=org_id,
            name="Finca Principal",
            municipality_code="47001",
            location=_POINT,
        )
    )
    db_session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=_BOUNDARY,
            irrigation_system="drip",
        )
    )
    await db_session.commit()
    return Org(org_id, farm_id, plot_id)


async def _add_node(db_session: AsyncSession, org: Org, *, last_seen_at: datetime) -> UUID:
    """A node claimed onto that org's plot (`ck_node_ownership_all_or_nothing`
    makes org, plot and `claimed_at` all or nothing)."""
    node_id = uuid7()
    db_session.add(
        NodeRow(
            id=node_id,
            org_id=org.org_id,
            plot_id=org.plot_id,
            transport="wifi",
            claim_code=f"claim-{uuid7().hex}",
            credential_hash="hash",
            interval_s=300,
            claimed_at=last_seen_at - timedelta(days=1),
            last_seen_at=last_seen_at,
            status="online",
        )
    )
    await db_session.commit()
    return node_id


async def _jobs(db_session: AsyncSession) -> list[Any]:
    return (
        await db_session.execute(
            text(
                "SELECT args, lock, queueing_lock, status, queue_name, task_name "
                "FROM procrastinate_jobs ORDER BY id"
            )
        )
    ).all()


async def _node_alerts(db_session: AsyncSession, org_id: UUID) -> list[tuple[str, str]]:
    """`(rule_code, state)` of the org's node alerts."""
    rows = await db_session.execute(
        select(AlertRuleRow.code, AlertRow.state)
        .join(AlertRuleRow, AlertRuleRow.id == AlertRow.rule_id)
        .where(AlertRow.org_id == org_id)
    )
    return [(code, state) for code, state in rows]


async def test_the_five_minute_sweep_defers_one_job_per_org_with_its_locks(
    db_session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    first_org = await _make_org(db_session)
    second_org = await _make_org(db_session)
    await _add_node(db_session, first_org, last_seen_at=now - _SILENT)
    await _add_node(db_session, second_org, last_seen_at=now - _SILENT)
    # An organization with no claimed node has no node to judge, so it must not
    # cost a job: the sweep reads the orgs of the nodes, not the orgs.
    empty = uuid7()
    db_session.add(OrganizationRow(id=empty, name="Empty Org", kind="individual"))
    await db_session.commit()

    await sweep_node_health(timestamp=0)

    jobs = await _jobs(db_session)
    assert {job.args["org_id"] for job in jobs} == {
        str(first_org.org_id),
        str(second_org.org_id),
    }
    assert {job.task_name for job in jobs} == {EVALUATE_ORG_TASK_NAME}
    assert all(job.queue_name == QUEUE_NAME and job.status == "todo" for job in jobs)
    # One organization never sweeps twice at once: the per-org lock serializes
    # its jobs and the queueing lock refuses a duplicate while one is `todo`.
    assert {job.lock for job in jobs} == {
        f"alerts:org:{first_org.org_id}",
        f"alerts:org:{second_org.org_id}",
    }
    assert {job.queueing_lock for job in jobs} == {job.lock for job in jobs}
    assert str(empty) not in {job.args["org_id"] for job in jobs}


async def test_the_org_job_opens_the_alert_of_a_silent_node_and_skips_a_recent_one(
    db_session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    org = await _make_org(db_session)
    await _add_node(db_session, org, last_seen_at=now - _SILENT)
    # The same page covers both nodes, so the negative case is a node of the
    # same org that was heard from a minute ago: it must open nothing.
    await _add_node(db_session, org, last_seen_at=now - _RECENT)

    await evaluate_org_node_health(org_id=str(org.org_id))

    alerts = await _node_alerts(db_session, org.org_id)
    assert len(alerts) == 1
    assert alerts[0] == ("node_offline", "open")
