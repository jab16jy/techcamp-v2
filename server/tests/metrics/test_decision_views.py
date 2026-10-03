"""The `record_keeping` and `decision` read views.

E11 T3 (D-T0.3, D-T0.5; docs/11-metricas.md §2). Both views are proven against
the real database with hand-computed expected rows, because what T5 later
divides by lives in SQL: the ISO week decides the `record_keeping` denominator,
and a null `irrigation_mm` decides whether a `postpone` day counts as followed.

Every test carries its negative assertion: a tombstoned entry never counts, a
week whose only entry was discarded produces no row at all, another plot of the
same organization and another organization's plot both read empty
(docs/09-cuellos-de-botella.md §Seguridad), and the kinds docs/11 §2 does not
count stay visible so the domain code can tell "no countable day" from "no
evidence".
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from metrics.conftest import (
    MONTH,
    add_logbook_entry,
    add_plot,
    add_recommendation,
    make_env,
)
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.metrics.adapters.source_repository import SqlAlchemyMetricsSourceRepository

pytestmark = pytest.mark.anyio

_SEPTEMBER = {"from_day": date(2026, 9, 1), "to_day": date(2026, 9, 30)}


async def test_logbook_view_counts_one_row_per_week_with_entries(db_session: AsyncSession) -> None:
    """D-T0.3: the `record_keeping` numerator is the ISO weeks of the month
    holding at least one non-deleted entry.

    September 2026 opens on a Tuesday, so five ISO weeks overlap it and the
    first starts on Aug 31. Sep 2 and Sep 3 share the week of Aug 31 (two
    entries); Sep 15 sits in the week of Sep 14; the tombstone of Sep 16 lands
    in that same week and adds nothing to it.
    """
    env = await make_env(db_session)
    await add_logbook_entry(
        db_session, env, kind="task", occurred_on=date(2026, 9, 2), labor_days=2
    )
    await add_logbook_entry(
        db_session, env, kind="task", occurred_on=date(2026, 9, 3), labor_days=1
    )
    await add_logbook_entry(
        db_session, env, kind="input", occurred_on=date(2026, 9, 15), cost_cop=90_000
    )
    await add_logbook_entry(
        db_session, env, kind="task", occurred_on=date(2026, 9, 16), labor_days=1, deleted=True
    )
    # A tombstone in a week of its own: that week must not appear at all, since
    # a zero row would read as `0` evidence instead of as none (D-T0.3).
    await add_logbook_entry(
        db_session, env, kind="task", occurred_on=date(2026, 9, 22), labor_days=1, deleted=True
    )

    rows = await SqlAlchemyMetricsSourceRepository(db_session).logbook_weeks(
        env.org_id, env.plot_id, month=MONTH
    )

    assert [(r.plot_id, r.month, r.week_start, r.entry_count) for r in rows] == [
        (env.plot_id, MONTH, date(2026, 8, 31), 2),
        (env.plot_id, MONTH, date(2026, 9, 14), 1),
    ]


async def test_logbook_view_reads_only_the_plot_it_was_asked_for(
    db_session: AsyncSession,
) -> None:
    """docs/09 §Seguridad, for the week view."""
    env = await make_env(db_session)
    await add_logbook_entry(
        db_session, env, kind="task", occurred_on=date(2026, 9, 2), labor_days=2
    )
    sibling = await add_plot(db_session, org_id=env.org_id, farm_id=env.farm_id)
    other = await make_env(db_session, name="Finca Otra")
    source = SqlAlchemyMetricsSourceRepository(db_session)

    assert await source.logbook_weeks(env.org_id, sibling, month=MONTH) == []
    assert await source.logbook_weeks(other.org_id, other.plot_id, month=MONTH) == []
    # A month with nothing in it is no evidence too, not a zero.
    assert await source.logbook_weeks(env.org_id, env.plot_id, month=date(2026, 8, 1)) == []


async def test_decision_view_joins_each_day_to_its_applied_depth(
    db_session: AsyncSession,
) -> None:
    """D-T0.5: every recommendation is paired with that day's applied depth.

    Sep 10 recommends 20 mm and 18 mm were applied (inside the 25 % band); Sep
    12 recommends 10 mm across two entries of 4 mm, which sum to 8. Sep 13 has
    only a discarded irrigation entry and Sep 11 is a `postpone` with nothing
    applied: both read `None`, never `0`, because "the producer did not irrigate"
    and "the producer applied no millimetres" are different facts and only the
    first one means the day was followed.
    """
    env = await make_env(db_session)
    await add_recommendation(db_session, env, day=date(2026, 9, 10), kind="irrigate", depth_mm=20.0)
    await add_logbook_entry(
        db_session, env, kind="irrigation", occurred_on=date(2026, 9, 10), irrigation_mm=18.0
    )
    await add_recommendation(
        db_session, env, day=date(2026, 9, 11), kind="postpone", depth_mm=None, duration_min=None
    )
    await add_recommendation(db_session, env, day=date(2026, 9, 12), kind="irrigate", depth_mm=10.0)
    for _ in range(2):
        await add_logbook_entry(
            db_session, env, kind="irrigation", occurred_on=date(2026, 9, 12), irrigation_mm=4.0
        )
    await add_recommendation(db_session, env, day=date(2026, 9, 13), kind="irrigate", depth_mm=5.0)
    await add_logbook_entry(
        db_session,
        env,
        kind="irrigation",
        occurred_on=date(2026, 9, 13),
        irrigation_mm=5.0,
        deleted=True,
    )

    rows = await SqlAlchemyMetricsSourceRepository(db_session).decision_days(
        env.org_id, env.plot_id, **_SEPTEMBER
    )

    assert [(r.day, r.kind, r.depth_mm, r.irrigation_mm) for r in rows] == [
        (date(2026, 9, 10), "irrigate", Decimal("20"), Decimal("18.0")),
        (date(2026, 9, 11), "postpone", None, None),
        (date(2026, 9, 12), "irrigate", Decimal("10"), Decimal("8.0")),
        (date(2026, 9, 13), "irrigate", Decimal("5"), None),
    ]


async def test_decision_view_keeps_the_kinds_the_component_does_not_count(
    db_session: AsyncSession,
) -> None:
    """The negative of "the denominator counts days that carried a
    recommendation": `rainfed` and `no_kc` are stored decisions docs/11 §2 does
    not count, so the view returns them and the domain code of T5 filters them.
    Hiding them here would make "no countable day" and "no evidence"
    indistinguishable, and on a rainfed plot the index gives the other three
    components 100/3 each (docs/11:52)."""
    env = await make_env(db_session, irrigation_system="none")
    await add_recommendation(
        db_session, env, day=date(2026, 9, 10), kind="rainfed", depth_mm=None, duration_min=None
    )
    await add_recommendation(
        db_session, env, day=date(2026, 9, 11), kind="no_kc", depth_mm=None, duration_min=None
    )

    rows = await SqlAlchemyMetricsSourceRepository(db_session).decision_days(
        env.org_id, env.plot_id, **_SEPTEMBER
    )

    assert [(r.kind, r.depth_mm, r.irrigation_mm) for r in rows] == [
        ("rainfed", None, None),
        ("no_kc", None, None),
    ]


async def test_decision_view_reads_only_the_plot_it_was_asked_for(
    db_session: AsyncSession,
) -> None:
    """docs/09 §Seguridad, for the day view: irrigation applied on a sibling
    plot of the same organization is not this plot's applied depth, and another
    organization's plot is absent."""
    env = await make_env(db_session)
    sibling = await add_plot(db_session, org_id=env.org_id, farm_id=env.farm_id)
    other = await make_env(db_session, name="Finca Otra")
    await add_recommendation(db_session, env, day=date(2026, 9, 10), kind="irrigate", depth_mm=20.0)
    await add_logbook_entry(
        db_session, env, kind="irrigation", occurred_on=date(2026, 9, 10), irrigation_mm=18.0
    )
    source = SqlAlchemyMetricsSourceRepository(db_session)

    assert await source.decision_days(env.org_id, sibling, **_SEPTEMBER) == []
    assert await source.decision_days(other.org_id, other.plot_id, **_SEPTEMBER) == []
    assert [
        r.irrigation_mm for r in await source.decision_days(env.org_id, env.plot_id, **_SEPTEMBER)
    ] == [Decimal("18.0")]
    # Outside the window there is nothing to decide about.
    assert (
        await source.decision_days(
            env.org_id, env.plot_id, from_day=date(2026, 8, 1), to_day=date(2026, 8, 31)
        )
        == []
    )
