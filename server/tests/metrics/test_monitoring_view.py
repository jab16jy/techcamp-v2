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

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.metrics.adapters.source_repository import SqlAlchemyMetricsSourceRepository

pytestmark = pytest.mark.anyio

_FULL_MONTH_SECONDS = 2_592_000
"""September 2026 has 30 days: 30 x 86400."""
_HALF_MONTH_SECONDS = 1_296_000
"""From local midnight of Sep 16 to local midnight of Oct 1: 15 x 86400."""
_WEST_OF_BOGOTA = "America/Los_Angeles"
"""A session zone seven hours west of UTC in September (PDT), three hours west of
Bogota. The only zones that move the label are those west of UTC-5: under UTC or
UTC-5 the instant of a Bogota month start lands on the same date, so the bug is
invisible on this worktree's own container and only appears on a host configured
further west."""


async def _set_session_timezone(session: AsyncSession, tz: str) -> None:
    """Move the session's `TimeZone` and read it back the same way.

    `set_config` takes a bound parameter where `SET TIME ZONE` takes a literal,
    and the GUC it sets is exactly the one `timestamptz::date` resolves in, so
    this is the seam the finding is about rather than a lookalike. `false` is
    session scope, not `SET LOCAL`, because the fixture commits and a local
    setting would be discarded by the first commit inside the test.
    """
    applied = await session.execute(text("SELECT set_config('TimeZone', :tz, false)"), {"tz": tz})
    assert applied.scalar_one() == tz


async def _session_timezone(session: AsyncSession) -> str:
    """The session's current `TimeZone`, so the test can put it back."""
    return (await session.execute(text("SHOW TIME ZONE"))).scalar_one()


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


async def test_monitoring_view_counts_one_reading_per_uplink_not_per_sensor(
    db_session: AsyncSession,
) -> None:
    """Closes `R3-reliability.monitoring.duplicate-count`: the numerator counts
    distinct uplink instants, so two sensors of one node reporting at the same
    instant is ONE reading, not two.

    That is the whole reason the view reads `COUNT(DISTINCT r.time)`: every sensor
    of a node shares one `reading.time` per uplink, which is the unit
    `interval_s` is expressed in. Two sensors at one instant must not make the
    plot look better monitored than it is — the component measures that the plot
    is being measured, and one uplink is one measurement.

    The negative is in the same assertion: a second sensor reporting at a
    *different* instant is a real second reading and must be counted.
    """
    env = await make_env(db_session)
    node_id = await add_claimed_node(
        db_session, env, claim_code="node-a", claimed_at=bogota_midnight(MONTH), interval_s=3600
    )
    soil = await add_sensor(db_session, node_id, channel_key="sm-a-20", metric="soil_moisture")
    air = await add_sensor(db_session, node_id, channel_key="air-rh-150", metric="air_rh")
    shared = bogota_midnight(date(2026, 9, 2))
    # Both sensors report the SAME uplink instant: one reading.
    await add_reading(db_session, soil, at=shared, value=21.0)
    await add_reading(db_session, air, at=shared, value=68.0)
    # A genuinely later uplink is a second reading.
    await add_reading(db_session, soil, at=shared + timedelta(hours=1), value=21.4)

    rows = await SqlAlchemyMetricsSourceRepository(db_session).node_month_readings(
        env.org_id, env.plot_id, month=MONTH
    )

    assert [(r.received_readings, r.claimed_seconds) for r in rows] == [(2, _FULL_MONTH_SECONDS)]


async def test_the_month_label_does_not_follow_the_session_timezone(
    db_session: AsyncSession,
) -> None:
    """The window arithmetic was never the problem; the `month` **label** was.

    `bdc469490565` selected `bounds.month_start::date`, and `bounds.month_start`
    is a `timestamptz`: casting one to `date` resolves it in the session's
    `TimeZone`, while the module docstring claims month boundaries are built
    "never from the session's `TimeZone`". A node claimed at Bogota midnight on
    Sep 1 is the instant `2026-09-01T05:00Z`, which is 22:00 on **Aug 31** in
    Los Angeles, so the row was labelled August and a September query found
    nothing while an August query found a September's worth of evidence.

    The window itself cannot move: `month_start`/`month_end`/`claimed_at` are
    subtracted as absolute instants, so `claimed_seconds` is identical in either
    zone. That is why the assertion is about the label alone, and why the same
    full month of seconds is the correct figure for the September row.

    The negative is the other side of the same single label: a node that was never
    claimed in August must not appear under August just because the session ran
    west of UTC-5.

    The zone is set explicitly rather than read from the ambient configuration,
    so the test proves the view is immune to it instead of passing on a host that
    happens to run at UTC.
    """
    source = SqlAlchemyMetricsSourceRepository(db_session)
    env = await make_env(db_session)
    node_id = await add_claimed_node(
        db_session, env, claim_code="node-tz", claimed_at=bogota_midnight(MONTH), interval_s=3600
    )
    original = await _session_timezone(db_session)

    try:
        await _set_session_timezone(db_session, _WEST_OF_BOGOTA)

        september = await source.node_month_readings(env.org_id, env.plot_id, month=MONTH)
        august = await source.node_month_readings(env.org_id, env.plot_id, month=date(2026, 8, 1))
    finally:
        await _set_session_timezone(db_session, original)

    assert [(r.node_id, r.month, r.claimed_seconds) for r in september] == [
        (node_id, MONTH, _FULL_MONTH_SECONDS)
    ]
    assert august == []


async def test_monitoring_view_caps_the_current_month_at_now(
    db_session: AsyncSession,
) -> None:
    """Closes `R3-reliability.monitoring.now-dependence`: the `LEAST(month end,
    now())` cap exists so a month still in flight is not charged for days that
    have not happened yet.

    `now()` lives in SQL, so the current month cannot be pinned to an exact
    second without a clock seam. What IS assertable is the bound the cap exists
    to guarantee: a node claimed inside the current month is charged from its
    claim to *now*, so its seconds can never exceed the wall-clock time actually
    elapsed, and never reach the month's full length.

    The negative is the same bound from below: a node claimed at the start of
    the current month is charged at least the seconds since that moment.
    """
    source = SqlAlchemyMetricsSourceRepository(db_session)
    env = await make_env(db_session)
    today = datetime.now(UTC).astimezone(ZoneInfo("America/Bogota")).date()
    month_start = today.replace(day=1)
    claimed_now = datetime.now(UTC)
    month_open = bogota_midnight(month_start)

    early = await add_claimed_node(
        db_session, env, claim_code="node-early", claimed_at=month_open, interval_s=3600
    )
    late = await add_claimed_node(
        db_session, env, claim_code="node-late", claimed_at=claimed_now, interval_s=3600
    )

    rows = {
        r.node_id: r.claimed_seconds
        for r in await source.node_month_readings(env.org_id, env.plot_id, month=month_start)
    }

    now = datetime.now(UTC)
    elapsed_full_month = (now - claimed_now).total_seconds()
    assert elapsed_full_month < float(_FULL_MONTH_SECONDS), "the month is still in flight"
    # A node claimed now cannot be credited with more than the seconds since.
    assert 0 <= rows[late] <= elapsed_full_month
    # A node claimed when the month opened is credited the whole elapsed month,
    # which is more than the late node and still no more than a full month.
    assert rows[early] >= rows[late] >= 0
    assert rows[early] <= _FULL_MONTH_SECONDS
