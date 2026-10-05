"""`plot_metric_monthly` against the real database (E11 T5, D-T0.2).

The upsert is proven here rather than over a fake because its whole promise is a
database behavior: docs/03:442 requires that running the monthly job twice leaves
the same result, and only a real `ON CONFLICT` on the `(plot_id, month)` key
can show that.

The security-relevant part is the `org_id` predicate on every read. Nothing in
the API can exercise it — a foreign plot is rejected earlier by the facade — so
these tests ask for another organization's row by a known `plot_id`
(docs/09-cuellos-de-botella.md#seguridad).

The whole use case also runs end to end over real adapters in
`test_adoption_usecase_db.py`-style wiring below, so the numbers a job stores are
the numbers the views produced rather than hand-written fixtures.
"""

from __future__ import annotations

import datetime
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from metrics.conftest import MONTH, add_claimed_node, add_reading_row, add_sensor, make_env
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.metrics.adapters.monthly_repository import SqlAlchemyMonthlyMetricRepository
from techcamp.metrics.adapters.orm import PlotMetricMonthlyRow
from techcamp.metrics.domain.adoption import AdoptionComponents, PlotMonthlyMetric
from techcamp.metrics.domain.errors import PlotMonthOwnedByAnotherOrganizationError

pytestmark = pytest.mark.anyio

COMPUTED_AT = datetime.datetime(2026, 10, 1, 7, 0, tzinfo=datetime.UTC)
"""Frozen, not `now()`: a test clock must not drift (E11 lessons, #12)."""

OTHER_MONTH = datetime.date(2026, 8, 1)


def _metric(
    env,  # type: ignore[no-untyped-def]
    *,
    month: datetime.date = MONTH,
    monitoring: Decimal | None = Decimal("0.5"),
    record_keeping: Decimal | None = Decimal("1"),
    decision: Decimal | None = Decimal("1"),
    risk_management: Decimal | None = None,
    index: Decimal | None = Decimal(75),
) -> PlotMonthlyMetric:
    return PlotMonthlyMetric(
        plot_id=env.plot_id,
        org_id=env.org_id,
        month=month,
        components=AdoptionComponents(
            monitoring=monitoring,
            record_keeping=record_keeping,
            decision=decision,
            risk_management=risk_management,
        ),
        digital_adoption_index=index,
        computed_at=COMPUTED_AT,
    )


async def test_upsert_stores_the_components_and_the_index(db_session: AsyncSession) -> None:
    env = await make_env(db_session)

    stored = await SqlAlchemyMonthlyMetricRepository(db_session).upsert(_metric(env))

    assert stored.plot_id == env.plot_id
    assert stored.org_id == env.org_id
    assert stored.month == MONTH
    assert stored.components == AdoptionComponents(
        monitoring=Decimal("0.5"),
        record_keeping=Decimal(1),
        decision=Decimal(1),
        risk_management=None,
    )
    assert stored.digital_adoption_index == Decimal(75)
    assert stored.computed_at == COMPUTED_AT


async def test_upsert_twice_leaves_one_row_with_the_second_values(
    db_session: AsyncSession,
) -> None:
    env = await make_env(db_session)
    repo = SqlAlchemyMonthlyMetricRepository(db_session)

    await repo.upsert(_metric(env, monitoring=Decimal("0.5"), index=Decimal(75)))
    await repo.upsert(_metric(env, monitoring=Decimal(1), index=Decimal(100)))

    rows = (await db_session.execute(select(PlotMetricMonthlyRow))).scalars().all()
    assert len(rows) == 1
    assert rows[0].monitoring == Decimal(1)
    assert rows[0].digital_adoption_index == Decimal(100)


async def test_upsert_of_a_null_component_writes_null_not_zero(
    db_session: AsyncSession,
) -> None:
    # A re-run that finds no alerts must clear the previous month's value
    # instead of leaving the old one behind: `set_` writes the nulls too.
    env = await make_env(db_session)
    repo = SqlAlchemyMonthlyMetricRepository(db_session)

    await repo.upsert(_metric(env, risk_management=Decimal(1)))
    await repo.upsert(_metric(env, risk_management=None, index=Decimal(75)))

    assert await repo.get(env.org_id, env.plot_id, month=MONTH) is not None
    row = (
        await db_session.execute(
            select(PlotMetricMonthlyRow).where(PlotMetricMonthlyRow.plot_id == env.plot_id)
        )
    ).scalar_one()
    assert row.risk_management is None


async def test_upsert_keeps_two_months_of_the_same_plot_apart(
    db_session: AsyncSession,
) -> None:
    env = await make_env(db_session)
    repo = SqlAlchemyMonthlyMetricRepository(db_session)

    await repo.upsert(_metric(env, month=OTHER_MONTH, index=Decimal(10)))
    await repo.upsert(_metric(env, month=MONTH, index=Decimal(90)))

    assert (
        await repo.get(env.org_id, env.plot_id, month=OTHER_MONTH)
    ).digital_adoption_index == Decimal(10)
    assert (await repo.get(env.org_id, env.plot_id, month=MONTH)).digital_adoption_index == Decimal(
        90
    )


async def test_upsert_of_two_plots_keeps_both_rows(db_session: AsyncSession) -> None:
    first = await make_env(db_session, name="Finca Uno")
    second = await make_env(db_session, name="Finca Dos")

    repo = SqlAlchemyMonthlyMetricRepository(db_session)
    await repo.upsert(_metric(first, index=Decimal(10)))
    await repo.upsert(_metric(second, index=Decimal(90)))

    rows = (await db_session.execute(select(PlotMetricMonthlyRow))).scalars().all()
    assert {row.plot_id for row in rows} == {first.plot_id, second.plot_id}


async def test_get_of_another_organizations_plot_is_none(db_session: AsyncSession) -> None:
    env = await make_env(db_session)
    await SqlAlchemyMonthlyMetricRepository(db_session).upsert(_metric(env))

    assert (
        await SqlAlchemyMonthlyMetricRepository(db_session).get(uuid4(), env.plot_id, month=MONTH)
        is None
    )


async def test_upsert_does_not_take_over_another_organizations_month(
    db_session: AsyncSession,
) -> None:
    # `(plot_id, month)` is unique across organizations, so a foreign `org_id`
    # collides with a month the caller does not own. The upsert must refuse it
    # instead of transferring the row to the new tenant
    # (docs/09-cuellos-de-botella.md#seguridad).
    env = await make_env(db_session)
    repo = SqlAlchemyMonthlyMetricRepository(db_session)
    await repo.upsert(_metric(env, index=Decimal(90)))

    with pytest.raises(PlotMonthOwnedByAnotherOrganizationError):
        await repo.upsert(replace(_metric(env, index=Decimal(10)), org_id=uuid4()))

    rows = (await db_session.execute(select(PlotMetricMonthlyRow))).scalars().all()
    assert len(rows) == 1
    assert rows[0].org_id == env.org_id
    assert rows[0].digital_adoption_index == Decimal(90)


async def test_latest_returns_the_most_recent_month(db_session: AsyncSession) -> None:
    env = await make_env(db_session)
    repo = SqlAlchemyMonthlyMetricRepository(db_session)
    await repo.upsert(_metric(env, month=datetime.date(2026, 7, 1)))
    await repo.upsert(_metric(env, month=datetime.date(2026, 9, 1)))
    await repo.upsert(_metric(env, month=datetime.date(2026, 8, 1)))

    latest = await repo.latest_for_plot(env.org_id, env.plot_id)

    assert latest is not None
    assert latest.month == datetime.date(2026, 9, 1)


async def test_latest_is_not_changed_by_rerunning_an_older_month(
    db_session: AsyncSession,
) -> None:
    # A retry of July must not make July the latest month: `month` orders, not
    # `computed_at` (D-T0.13).
    env = await make_env(db_session)
    repo = SqlAlchemyMonthlyMetricRepository(db_session)
    await repo.upsert(_metric(env, month=datetime.date(2026, 7, 1)))
    await repo.upsert(_metric(env, month=datetime.date(2026, 9, 1)))
    await repo.upsert(_metric(env, month=datetime.date(2026, 7, 1)))

    latest = await repo.latest_for_plot(env.org_id, env.plot_id)

    assert latest is not None
    assert latest.month == datetime.date(2026, 9, 1)


async def test_latest_of_another_organizations_plot_is_none(db_session: AsyncSession) -> None:
    env = await make_env(db_session)
    await SqlAlchemyMonthlyMetricRepository(db_session).upsert(_metric(env))

    assert (
        await SqlAlchemyMonthlyMetricRepository(db_session).latest_for_plot(uuid4(), env.plot_id)
        is None
    )


async def test_latest_of_a_plot_with_no_stored_month_is_none(db_session: AsyncSession) -> None:
    env = await make_env(db_session)

    assert (
        await SqlAlchemyMonthlyMetricRepository(db_session).latest_for_plot(env.org_id, env.plot_id)
        is None
    )


async def test_list_for_org_month_returns_only_that_organizations_rows(
    db_session: AsyncSession,
) -> None:
    mine = await make_env(db_session, name="Finca Mía")
    other = await make_env(db_session, name="Finca Ajena")
    repo = SqlAlchemyMonthlyMetricRepository(db_session)
    await repo.upsert(_metric(mine, index=Decimal(10)))
    await repo.upsert(_metric(mine, month=OTHER_MONTH, index=Decimal(20)))
    await repo.upsert(_metric(other, index=Decimal(90)))

    rows = await repo.list_for_org_month(mine.org_id, month=MONTH)

    assert [row.plot_id for row in rows] == [mine.plot_id]
    assert rows[0].digital_adoption_index == Decimal(10)


async def test_store_keeps_the_fractions_the_domain_computed(
    db_session: AsyncSession,
) -> None:
    # No quantization anywhere: `Numeric` has no scale and no document fixes one,
    # so a repeating fraction has to survive the round trip as computed. The
    # components stay inside 0-1 (their CHECK) and only the index is 0-100.
    env = await make_env(db_session)
    third = Decimal(1) / Decimal(3)
    index_thirds = Decimal(100) * third

    stored = await SqlAlchemyMonthlyMetricRepository(db_session).upsert(
        _metric(
            env,
            monitoring=third,
            record_keeping=third,
            decision=third,
            risk_management=third,
            index=index_thirds,
        )
    )

    assert stored.components.monitoring == third
    assert stored.components.risk_management == third
    assert stored.digital_adoption_index == index_thirds


async def test_use_case_over_real_adapters_stores_what_the_views_produced(
    db_session: AsyncSession,
) -> None:
    """The end-to-end shape the monthly job has: real views, real store, real
    facade. A drip plot with a node reading every 300 s and a logbook entry in one
    week scores `monitoring` and `record_keeping`, and the store keeps the
    resulting fractions."""
    from techcamp.farms.adapters.repositories import SqlAlchemyPlotRepository
    from techcamp.metrics.adapters.source_repository import SqlAlchemyMetricsSourceRepository
    from techcamp.metrics.application.adoption import compute_plot_month

    env = await make_env(db_session, irrigation_system="drip")
    node_id = await add_claimed_node(
        db_session,
        env,
        claim_code="node-a",
        claimed_at=datetime.datetime(2026, 9, 1, tzinfo=datetime.UTC),
    )
    sensor_id = await add_sensor(db_session, node_id, channel_key="soil-1")
    await add_reading_row(
        db_session, sensor_id, at=datetime.datetime(2026, 9, 2, tzinfo=datetime.UTC), value=12.0
    )

    metric = await compute_plot_month(
        org_id=env.org_id,
        plot_id=env.plot_id,
        month=MONTH,
        plots=SqlAlchemyPlotRepository(db_session),
        sources=SqlAlchemyMetricsSourceRepository(db_session),
        metrics=SqlAlchemyMonthlyMetricRepository(db_session),
        computed_at=COMPUTED_AT,
    )

    stored = await SqlAlchemyMonthlyMetricRepository(db_session).latest_for_plot(
        env.org_id, env.plot_id
    )
    assert stored is not None
    assert stored == metric
    assert stored.components.monitoring is not None
    assert stored.components.record_keeping is not None
