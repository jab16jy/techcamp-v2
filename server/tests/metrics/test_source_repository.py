"""The source repository's two organization-scoped methods (D-T7.2).

E11 T7b. `test_source_views.py` proves the SQL of the two views this lane adds;
this file proves the adapter over them: that a row becomes the right read-model
dataclass, that `org_id` and the requested month are both enforced in the query
rather than left to the caller, and that the order is deterministic.

A double at the port would prove none of it, so these run against the real
database, like every other view test in this module.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

import pytest
from home.conftest import HomeEnv
from metrics.conftest import (
    MONTH,
    add_alert,
    add_claimed_node,
    add_cycle,
    add_logbook_entry,
    add_reading,
    add_sensor,
    bogota_midnight,
    make_env,
)
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.metrics.adapters.source_repository import SqlAlchemyMetricsSourceRepository
from techcamp.metrics.application.ports import NodeFirstReading, OrgMonthCycle

pytestmark = pytest.mark.anyio

_SOWN_ON = date(2026, 8, 1)


async def _closed_harvest(
    db_session: AsyncSession, env: HomeEnv, *, on: date = date(2026, 9, 28)
) -> UUID:
    """A harvested cycle whose `harvest` entry of `on` registers its closure."""
    cycle_id = await add_cycle(db_session, env, sown_on=_SOWN_ON, status="harvested")
    await add_logbook_entry(
        db_session,
        env,
        kind="harvest",
        occurred_on=on,
        yield_kg=500,
        crop_cycle_id=cycle_id,
    )
    return cycle_id


async def _answering_node(
    db_session: AsyncSession,
    env: HomeEnv,
    *,
    code: str,
    channel: str,
    claim_on: date = MONTH,
    hours: int = 6,
) -> UUID:
    """A node claimed on the first local day of `claim_on` that answers `hours`
    later with one valid reading."""
    claimed_at = bogota_midnight(claim_on)
    node_id = await add_claimed_node(db_session, env, claim_code=code, claimed_at=claimed_at)
    sensor_id = await add_sensor(db_session, node_id, channel_key=channel)
    await add_reading(db_session, sensor_id, at=claimed_at + timedelta(hours=hours), value=22.5)
    return node_id


async def test_the_month_cycles_carry_their_closure_day_and_their_harvest(
    db_session: AsyncSession,
) -> None:
    """One harvested and one lost cycle, both closed in the asked month.

    `has_harvest` is the numerator's evidence and `closed_on` is the day that put
    the cycle in this month at all (D-T7.2), so both travel on the row.
    """
    env = await make_env(db_session)
    harvested = await _closed_harvest(db_session, env)
    lost = await add_cycle(db_session, env, sown_on=_SOWN_ON, status="lost")
    alert_id = await add_alert(db_session, env, rule_code="water_stress")
    await add_logbook_entry(
        db_session,
        env,
        kind="observation",
        occurred_on=date(2026, 9, 20),
        quantity=40,
        alert_id=alert_id,
        crop_cycle_id=lost,
    )
    repo = SqlAlchemyMetricsSourceRepository(db_session)

    rows = await repo.org_month_cycles(env.org_id, month=MONTH)

    assert sorted(rows, key=lambda row: row.closed_on) == [
        OrgMonthCycle(
            crop_cycle_id=lost,
            plot_id=env.plot_id,
            closed_on=date(2026, 9, 20),
            has_harvest=False,
        ),
        OrgMonthCycle(
            crop_cycle_id=harvested,
            plot_id=env.plot_id,
            closed_on=date(2026, 9, 28),
            has_harvest=True,
        ),
    ]


async def test_a_cycle_closed_in_another_month_is_not_returned(
    db_session: AsyncSession,
) -> None:
    """The month reaches the view from this method, not from the caller
    re-filtering: a cycle whose closure was written in August is not a September
    cycle, however recent the data behind it is."""
    env = await make_env(db_session)
    await _closed_harvest(db_session, env, on=date(2026, 8, 30))
    repo = SqlAlchemyMetricsSourceRepository(db_session)

    assert await repo.org_month_cycles(env.org_id, month=MONTH) == []


async def test_the_first_readings_carry_the_claim_and_its_first_valid_instant(
    db_session: AsyncSession,
) -> None:
    """docs/11-metricas.md:73 — the pair the hours are computed from.

    The node is claimed at local midnight and answers 6 h later. A second node
    claimed in October must not appear in September's answer, because the
    population is the nodes **claimed in the asked month**.
    """
    env = await make_env(db_session)
    node_id = await _answering_node(db_session, env, code="node-a", channel="sm-a-20")
    await _answering_node(
        db_session, env, code="node-b", channel="sm-b-20", claim_on=date(2026, 10, 1), hours=1
    )
    repo = SqlAlchemyMetricsSourceRepository(db_session)

    rows = await repo.node_first_readings(env.org_id, month=MONTH)

    claimed_at = bogota_midnight(MONTH)
    assert rows == [
        NodeFirstReading(
            node_id=node_id,
            plot_id=env.plot_id,
            claimed_at=claimed_at,
            first_reading_at=claimed_at + timedelta(hours=6),
        )
    ]


async def test_a_node_with_no_valid_reading_is_returned_without_an_instant(
    db_session: AsyncSession,
) -> None:
    """The row survives with a null instant: the node is part of the median's
    population and contributes no value, which is not the same as a zero."""
    env = await make_env(db_session)
    node_id = await add_claimed_node(
        db_session, env, claim_code="node-silent", claimed_at=bogota_midnight(MONTH)
    )
    repo = SqlAlchemyMetricsSourceRepository(db_session)

    rows = await repo.node_first_readings(env.org_id, month=MONTH)

    assert [(row.node_id, row.first_reading_at) for row in rows] == [(node_id, None)]


async def test_both_methods_return_nothing_for_another_organization(
    db_session: AsyncSession,
) -> None:
    """docs/09-cuellos-de-botella.md §Seguridad: a foreign organization's closures
    and enrollment latencies are absent, never folded into ours."""
    env_a = await make_env(db_session)
    env_b = await make_env(db_session)
    await _closed_harvest(db_session, env_b)
    await _answering_node(db_session, env_b, code="node-b", channel="sm-b-20", hours=5)
    repo = SqlAlchemyMetricsSourceRepository(db_session)

    assert await repo.org_month_cycles(env_a.org_id, month=MONTH) == []
    assert await repo.node_first_readings(env_a.org_id, month=MONTH) == []


async def test_the_month_cycles_come_back_in_a_stable_order(
    db_session: AsyncSession,
) -> None:
    """Ordered by cycle id, so two reads of the same month return the same
    sequence — a re-run must not reshuffle what the caller counted."""
    env = await make_env(db_session)
    await _closed_harvest(db_session, env, on=date(2026, 9, 5))
    await _closed_harvest(db_session, env, on=date(2026, 9, 6))
    repo = SqlAlchemyMetricsSourceRepository(db_session)

    rows = await repo.org_month_cycles(env.org_id, month=MONTH)

    assert [row.crop_cycle_id for row in rows] == sorted(row.crop_cycle_id for row in rows)
