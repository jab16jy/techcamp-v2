"""The `monitoring` read view: expected versus received readings per node-month.

E11 T3 (D-T0.3, D-T0.4; docs/11-metricas.md §2). The view is proven against the
real database with hand-computed expected rows, because the arithmetic T5 later
divides by lives in SQL and nothing else would catch a drift of a single second:
`claimed_seconds` is the whole denominator of the component.

Every test carries its negative assertion: an out-of-range reading still counts,
a node claimed mid-month is charged only from its claim, a sibling plot and
another organization both read empty (docs/09-cuellos-de-botella.md §Seguridad),
and a month the node was never claimed in has no row at all.
"""

from __future__ import annotations

from datetime import date

import pytest
from metrics.conftest import (
    MONTH,
    add_claimed_node,
    add_node,
    add_plot,
    add_reading,
    add_reading_row,
    add_sensor,
    bogota_midnight,
    make_env,
)
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.metrics.adapters.source_repository import SqlAlchemyMetricsSourceRepository

pytestmark = pytest.mark.anyio

_FULL_MONTH_SECONDS = 2_592_000
"""September 2026 has 30 days: 30 x 86400."""
_HALF_MONTH_SECONDS = 1_296_000
"""From local midnight of Sep 16 to local midnight of Oct 1: 15 x 86400."""


async def test_monitoring_view_reports_claimed_seconds_and_received_readings(
    db_session: AsyncSession,
) -> None:
    """D-T0.4: expected readings are the claimed seconds inside the month
    divided by `interval_s`, and every received reading counts (docs/11 §2).

    A node claimed on the first local day at an hourly interval is charged its
    whole month: 2592000 / 3600 = 720 expected. It sent three uplinks, the last
    one out of range (`value IS NULL`, `quality = 2`), which still counts: the
    component measures that the plot is being measured, and an out-of-range
    value is already covered by the node alerts.
    """
    env = await make_env(db_session)
    node_id = await add_claimed_node(
        db_session, env, claim_code="node-a", claimed_at=bogota_midnight(MONTH), interval_s=3600
    )
    sensor_id = await add_sensor(db_session, node_id, channel_key="sm-a-20")
    await add_reading(db_session, sensor_id, at=bogota_midnight(date(2026, 9, 2)), value=21.0)
    await add_reading(db_session, sensor_id, at=bogota_midnight(date(2026, 9, 9)), value=22.5)
    await add_reading_row(
        db_session, sensor_id, at=bogota_midnight(date(2026, 9, 20)), value=None, quality=2
    )

    rows = await SqlAlchemyMetricsSourceRepository(db_session).node_month_readings(
        env.org_id, env.plot_id, month=MONTH
    )

    assert [
        (r.node_id, r.month, r.interval_s, r.claimed_seconds, r.received_readings) for r in rows
    ] == [(node_id, MONTH, 3600, _FULL_MONTH_SECONDS, 3)]


async def test_monitoring_view_charges_a_mid_month_node_only_from_its_claim(
    db_session: AsyncSession,
) -> None:
    """The negative of the same rule: a node claimed on Sep 16 is charged 15
    days, not 30 — and it still appears, with zero readings, so a silent node
    reads as `0 %` monitored instead of as "no evidence" (D-T0.3)."""
    env = await make_env(db_session)
    node_id = await add_claimed_node(
        db_session,
        env,
        claim_code="node-late",
        claimed_at=bogota_midnight(date(2026, 9, 16)),
        interval_s=3600,
    )

    rows = await SqlAlchemyMetricsSourceRepository(db_session).node_month_readings(
        env.org_id, env.plot_id, month=MONTH
    )

    assert [(r.node_id, r.claimed_seconds, r.received_readings) for r in rows] == [
        (node_id, _HALF_MONTH_SECONDS, 0)
    ]


async def test_monitoring_view_reads_only_the_plot_it_was_asked_for(
    db_session: AsyncSession,
) -> None:
    """docs/09 §Seguridad, twice: a sibling plot of the same organization and a
    plot of another organization both come back empty rather than leaking the
    node's evidence. A month the node was never claimed in has no row either."""
    env = await make_env(db_session)
    node_id = await add_node(db_session, env, claim_code="node-a", interval_s=3600)
    sensor_id = await add_sensor(db_session, node_id, channel_key="sm-a-20")
    await add_reading(db_session, sensor_id, at=bogota_midnight(date(2026, 9, 2)), value=21.0)
    sibling = await add_plot(db_session, org_id=env.org_id, farm_id=env.farm_id)
    other = await make_env(db_session, name="Finca Otra")
    source = SqlAlchemyMetricsSourceRepository(db_session)

    assert await source.node_month_readings(env.org_id, sibling, month=MONTH) == []
    assert await source.node_month_readings(other.org_id, other.plot_id, month=MONTH) == []
    # The shared `add_node` claims at 2026-01-01T00:00Z, which is 2025-12-31
    # 19:00 in Bogota, so the claim belongs to December 2025 and that month is
    # charged its last five hours — the calendar month is the local one
    # (D-T0.7), not the UTC one. November precedes the claim and has no row at
    # all, which is missing evidence rather than a month of silence (D-T0.3).
    partial = await source.node_month_readings(env.org_id, env.plot_id, month=date(2025, 12, 1))
    assert [(r.month, r.claimed_seconds, r.received_readings) for r in partial] == [
        (date(2025, 12, 1), 18_000, 0)
    ]
    assert await source.node_month_readings(env.org_id, env.plot_id, month=date(2025, 11, 1)) == []
