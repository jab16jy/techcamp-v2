"""The two organization-scoped read views behind D-T7.1's locked figures.

E11 T7b (D-T7.2; docs/11-metricas.md:69-75). `metrics_org_month_cycles` and
`metrics_org_node_first_reading` are proven against the real database with
hand-computed expected rows, because each one exists to answer a question no
stored table can: *which month does a closed cycle belong to* (the model has no
cycle end date) and *when did a claimed node first answer*.

Every test carries its negative assertion, and the absences are the point rather
than an afterthought (docs/03-modelo-datos.md:441 — missing evidence is `null`,
never `0`):

- an `active` cycle is in neither side of the ratio, even with a harvest record;
- a `lost` cycle with no loss observation, and a cycle closed by `PATCH` with no
  logbook entry at all, belong to no month;
- a tombstoned entry registers no closure;
- a node whose readings are all invalid still gets a row, with no instant;
- a reading timestamped before the claim is not the first reading;
- a sibling organization is absent from both views (docs/09 §Seguridad).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from metrics.conftest import (
    MONTH,
    add_alert,
    add_claimed_node,
    add_cycle,
    add_logbook_entry,
    add_reading,
    add_reading_row,
    add_sensor,
    bogota_midnight,
    make_env,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio

_SOWN_ON = date(2026, 8, 1)
"""August sowing, so a cycle that closes in September 2026 is a normal season."""


async def _cycles(db_session: AsyncSession, org_id: UUID) -> list[tuple]:
    """The cycle view read for one organization, as plain rows.

    Read with `text()` on purpose: this file proves the *SQL* of the migration,
    so it must not go through the repository methods that layer on top of it
    (those are proven separately, in `test_source_repository.py`).
    """
    result = await db_session.execute(
        text(
            "SELECT month, crop_cycle_id, plot_id, closed_on, has_harvest "
            "FROM metrics_org_month_cycles WHERE org_id = :org_id "
            "ORDER BY month, closed_on, crop_cycle_id"
        ),
        {"org_id": org_id},
    )
    return [tuple(row) for row in result]


async def _first_readings(db_session: AsyncSession, org_id: UUID) -> list[tuple]:
    """The node view read for one organization, as plain rows."""
    result = await db_session.execute(
        text(
            "SELECT month, node_id, plot_id, claimed_at, first_reading_at "
            "FROM metrics_org_node_first_reading WHERE org_id = :org_id "
            "ORDER BY node_id"
        ),
        {"org_id": org_id},
    )
    return [tuple(row) for row in result]


async def test_the_cycle_view_counts_the_closures_registered_in_the_month(
    db_session: AsyncSession,
) -> None:
    """D-T7.2: a cycle belongs to the month of the logbook entry that registers
    its close, read by its own `occurred_on`.

    Two September closures: a harvested cycle whose `harvest` entry of Sep 28
    registers a close **with** a harvest, and a lost cycle whose `observation`
    carrying an `alert_id` of Sep 20 registers a close **without** one. So the
    month holds two terminated cycles, one of them harvested. A `task` entry of
    Sep 12 and a discarded `harvest` entry of Sep 25 on the harvested cycle
    change nothing: the first does not register a closure, and a tombstone is not
    evidence (docs/03-modelo-datos.md:237).
    """
    env = await make_env(db_session)
    harvested = await add_cycle(db_session, env, sown_on=_SOWN_ON, status="harvested")
    lost = await add_cycle(db_session, env, sown_on=_SOWN_ON, status="lost")
    alert_id = await add_alert(db_session, env, rule_code="water_stress")
    await add_logbook_entry(
        db_session,
        env,
        kind="task",
        occurred_on=date(2026, 9, 12),
        labor_days=2,
        crop_cycle_id=harvested,
    )
    await add_logbook_entry(
        db_session,
        env,
        kind="harvest",
        occurred_on=date(2026, 9, 28),
        yield_kg=500,
        crop_cycle_id=harvested,
    )
    await add_logbook_entry(
        db_session,
        env,
        kind="harvest",
        occurred_on=date(2026, 9, 25),
        yield_kg=999,
        crop_cycle_id=harvested,
        deleted=True,
    )
    await add_logbook_entry(
        db_session,
        env,
        kind="observation",
        occurred_on=date(2026, 9, 20),
        quantity=40,
        alert_id=alert_id,
        crop_cycle_id=lost,
    )

    rows = await _cycles(db_session, env.org_id)

    assert rows == [
        (MONTH, lost, env.plot_id, date(2026, 9, 20), False),
        (MONTH, harvested, env.plot_id, date(2026, 9, 28), True),
    ]


async def test_a_cycle_counts_in_exactly_one_month_when_two_entries_qualify(
    db_session: AsyncSession,
) -> None:
    """Owner ruling, 2026-10-05 (Option A): a closed cycle belongs to **one**
    month, the month of the entry that registers its closure.

    `d2c8f4a1b3e7` grouped by `date_trunc('month', e.occurred_on)`, so the
    closure entries themselves decided the grain: this lost cycle carries a
    September `harvest` and an October loss observation, it satisfied the join
    twice, and it emitted **two** rows -- September with `has_harvest` and
    October without. Both months then counted the same cycle in their
    denominator, so a ratio "sobre el mes pedido" could not be a fraction of
    cycles at all.

    What a reader cannot know from the log is *which* of two qualifying entries
    closed the cycle, so the closure is chosen by the cycle's own status, the
    same rule D-T7.2 states: a `harvest` entry closes a harvested cycle, an
    `observation` with an `alert_id` closes a lost one. This one is `lost`, so
    October owns it and September must say nothing about it -- including its
    harvest, which is why the row carries `has_harvest = False`.

    The harvested cycle closes in September and is unaffected, so the month total
    is two terminated cycles in two months, not three cycles in two months.
    """
    env = await make_env(db_session)
    harvested = await add_cycle(db_session, env, sown_on=_SOWN_ON, status="harvested")
    lost = await add_cycle(db_session, env, sown_on=_SOWN_ON, status="lost")
    alert_id = await add_alert(db_session, env, rule_code="water_stress")
    await add_logbook_entry(
        db_session,
        env,
        kind="harvest",
        occurred_on=date(2026, 9, 28),
        yield_kg=500,
        crop_cycle_id=harvested,
    )
    await add_logbook_entry(
        db_session,
        env,
        kind="harvest",
        occurred_on=date(2026, 9, 5),
        yield_kg=300,
        crop_cycle_id=lost,
    )
    await add_logbook_entry(
        db_session,
        env,
        kind="observation",
        occurred_on=date(2026, 10, 3),
        quantity=40,
        alert_id=alert_id,
        crop_cycle_id=lost,
    )

    rows = await _cycles(db_session, env.org_id)

    assert rows == [
        (MONTH, harvested, env.plot_id, date(2026, 9, 28), True),
        (date(2026, 10, 1), lost, env.plot_id, date(2026, 10, 3), False),
    ]


async def test_the_earliest_entry_of_the_closing_kind_is_the_one_that_closed_it(
    db_session: AsyncSession,
) -> None:
    """Closes `R3-closure-entry-minimum-untested` (RDD review-888d4b51618ad801,
    WARNING): `MIN(occurred_on)` is documented as the tie-break when a cycle has
    several entries of the kind that registers its closure, and nothing pinned it.

    This harvested cycle has two harvest entries, Sep 28 and Oct 5. The earliest
    is when the closure was registered, so September owns the cycle -- and
    October does not see it at all, which is the same one-cycle-one-month rule as
    the qualifying-entries case with the extra step that here *both* entries are
    of the closing kind, so the choice between them is the only thing under test.

    It also pins that the September harvest makes it a numerator in September:
    the earliest of two qualifying entries is still a qualifying entry.
    """
    env = await make_env(db_session)
    cycle_id = await add_cycle(db_session, env, sown_on=_SOWN_ON, status="harvested")
    await add_logbook_entry(
        db_session,
        env,
        kind="harvest",
        occurred_on=date(2026, 10, 5),
        yield_kg=120,
        crop_cycle_id=cycle_id,
    )
    await add_logbook_entry(
        db_session,
        env,
        kind="harvest",
        occurred_on=date(2026, 9, 28),
        yield_kg=500,
        crop_cycle_id=cycle_id,
    )

    rows = await _cycles(db_session, env.org_id)

    assert rows == [(MONTH, cycle_id, env.plot_id, date(2026, 9, 28), True)]
    assert await _cycles(db_session, env.org_id) == rows, "the view is stable on re-read"


async def test_a_lost_cycle_harvested_in_its_own_month_still_counts_as_harvested(
    db_session: AsyncSession,
) -> None:
    """D-T7.3 preserved verbatim: the numerator has **no status filter**.

    This cycle is `lost`, so its closure is the October loss observation and
    October owns it. September's harvest entry is a different month and does not
    reach it.

    The denominator asked for `status IN ('harvested', 'lost')` and the numerator
    asked only for a `harvest` entry in the month, and that asymmetry is
    deliberate: against contradictory data -- a producer who registered a harvest
    and then recorded the loss -- the log is the strongest evidence available,
    so the month reports the harvest rather than second-guessing the status. The
    filter stays on `status` for the denominator and nowhere else.
    """
    env = await make_env(db_session)
    cycle_id = await add_cycle(db_session, env, sown_on=_SOWN_ON, status="lost")
    alert_id = await add_alert(db_session, env, rule_code="water_stress")
    await add_logbook_entry(
        db_session,
        env,
        kind="harvest",
        occurred_on=date(2026, 10, 12),
        yield_kg=250,
        crop_cycle_id=cycle_id,
    )
    await add_logbook_entry(
        db_session,
        env,
        kind="observation",
        occurred_on=date(2026, 10, 20),
        quantity=40,
        alert_id=alert_id,
        crop_cycle_id=cycle_id,
    )

    rows = await _cycles(db_session, env.org_id)

    assert rows == [(date(2026, 10, 1), cycle_id, env.plot_id, date(2026, 10, 20), True)]


async def test_a_harvest_closes_its_cycle_in_the_month_it_was_written(
    db_session: AsyncSession,
) -> None:
    """The negative of the anchor: the same cycle written in August is an August
    cycle and September says nothing about it. A figure counted "sobre el mes
    pedido" cannot answer a different month than the one asked."""
    env = await make_env(db_session)
    cycle_id = await add_cycle(db_session, env, sown_on=_SOWN_ON, status="harvested")
    await add_logbook_entry(
        db_session,
        env,
        kind="harvest",
        occurred_on=date(2026, 8, 30),
        yield_kg=300,
        crop_cycle_id=cycle_id,
    )

    rows = await _cycles(db_session, env.org_id)

    assert rows == [(date(2026, 8, 1), cycle_id, env.plot_id, date(2026, 8, 30), True)]


async def test_an_active_cycle_is_in_neither_side_of_the_ratio(
    db_session: AsyncSession,
) -> None:
    """D-T7.2: docs/11-metricas.md:72's denominator is "ciclos **terminados**".

    A producer who registered a harvest but never closed the cycle leaves it
    `active`, and the figure must not read that harvest as a numerator over a
    denominator it is not part of: the ratio is a fraction of closed and
    registered cycles, not of cycles with harvest records.
    """
    env = await make_env(db_session)
    cycle_id = await add_cycle(db_session, env, sown_on=_SOWN_ON, status="active")
    await add_logbook_entry(
        db_session,
        env,
        kind="harvest",
        occurred_on=date(2026, 9, 28),
        yield_kg=500,
        crop_cycle_id=cycle_id,
    )

    assert await _cycles(db_session, env.org_id) == []


async def test_a_closed_cycle_with_no_logbook_entry_is_invisible(
    db_session: AsyncSession,
) -> None:
    """D-T7.2: the missing-evidence rule, on both closure paths.

    A harvested cycle closed through `PATCH` with nothing written in the logbook,
    and a lost cycle whose loss was never observed, have no entry to date the
    closure with. Neither is a zero numerator: they are absent, so the month
    answers `null` rather than "nothing was harvested".
    """
    env = await make_env(db_session)
    await add_cycle(db_session, env, sown_on=_SOWN_ON, status="harvested")
    await add_cycle(db_session, env, sown_on=date(2026, 6, 1), status="lost")

    assert await _cycles(db_session, env.org_id) == []


async def test_the_cycle_view_ignores_another_organization(
    db_session: AsyncSession,
) -> None:
    """docs/09-cuellos-de-botella.md §Seguridad: `org_id` comes from the plot the
    cycle belongs to, so a foreign organization's closures are absent rows and
    never counted into ours."""
    env_a = await make_env(db_session)
    env_b = await make_env(db_session)
    cycle_id = await add_cycle(db_session, env_b, sown_on=_SOWN_ON, status="harvested")
    await add_logbook_entry(
        db_session,
        env_b,
        kind="harvest",
        occurred_on=date(2026, 9, 28),
        yield_kg=500,
        crop_cycle_id=cycle_id,
    )

    assert await _cycles(db_session, env_a.org_id) == []
    assert len(await _cycles(db_session, env_b.org_id)) == 1


async def test_the_first_reading_is_the_earliest_valid_one_after_the_claim(
    db_session: AsyncSession,
) -> None:
    """docs/11-metricas.md:73: hours between the node's alta and its first valid
    reading. The view carries the two instants and the month of the claim, and
    leaves the hours to the application.

    The node is claimed at local midnight of Sep 1 and answers 6 h later, with a
    second valid reading 48 h later that must not win.
    """
    env = await make_env(db_session)
    claimed_at = bogota_midnight(MONTH)
    node_id = await add_claimed_node(db_session, env, claim_code="node-a", claimed_at=claimed_at)
    sensor_id = await add_sensor(db_session, node_id, channel_key="sm-a-20")
    await add_reading(db_session, sensor_id, at=claimed_at + timedelta(hours=48), value=21.0)
    await add_reading(db_session, sensor_id, at=claimed_at + timedelta(hours=6), value=22.5)

    rows = await _first_readings(db_session, env.org_id)

    assert rows == [
        (
            MONTH,
            node_id,
            env.plot_id,
            claimed_at,
            claimed_at + timedelta(hours=6),
        )
    ]


async def test_a_node_with_no_valid_reading_keeps_its_row_without_an_instant(
    db_session: AsyncSession,
) -> None:
    """docs/04-api.md:83: a valid reading is calibrated and not out of range.

    The node answered three times and none of it is valid: one out of range
    (`quality = 2`), one uncalibrated (`value IS NULL`, `quality = 0`) and one
    both (`quality = 3`). The row is still there, because a node claimed in the
    month is part of the median's population whether or not it has answered —
    it contributes no value, which is not the same as contributing zero.
    """
    env = await make_env(db_session)
    claimed_at = bogota_midnight(MONTH)
    node_id = await add_claimed_node(
        db_session, env, claim_code="node-silent", claimed_at=claimed_at
    )
    sensor_id = await add_sensor(db_session, node_id, channel_key="sm-silent-20")
    await add_reading_row(
        db_session, sensor_id, at=claimed_at + timedelta(hours=2), value=None, quality=2
    )
    await add_reading_row(
        db_session, sensor_id, at=claimed_at + timedelta(hours=3), value=None, quality=0
    )
    await add_reading_row(
        db_session, sensor_id, at=claimed_at + timedelta(hours=4), value=None, quality=3
    )

    rows = await _first_readings(db_session, env.org_id)

    assert rows == [(MONTH, node_id, env.plot_id, claimed_at, None)]


async def test_a_node_whose_sensor_never_reported_keeps_its_row(
    db_session: AsyncSession,
) -> None:
    """A claimed node with no sensor at all is the same missing evidence: the row
    exists with a null instant."""
    env = await make_env(db_session)
    claimed_at = bogota_midnight(MONTH)
    node_id = await add_claimed_node(db_session, env, claim_code="node-bare", claimed_at=claimed_at)

    assert await _first_readings(db_session, env.org_id) == [
        (MONTH, node_id, env.plot_id, claimed_at, None)
    ]


async def test_a_reading_before_the_claim_is_not_the_first_reading(
    db_session: AsyncSession,
) -> None:
    """D-T7.2: the first reading is looked for from the claim onwards.

    A reading timestamped 2 h *before* the alta was not received after the node
    was enrolled, so it cannot show that the claimed node answered; counting it
    would report a negative latency. The valid reading 3 h after the claim wins.
    """
    env = await make_env(db_session)
    claimed_at = bogota_midnight(MONTH)
    node_id = await add_claimed_node(
        db_session, env, claim_code="node-early", claimed_at=claimed_at
    )
    sensor_id = await add_sensor(db_session, node_id, channel_key="sm-early-20")
    await add_reading(db_session, sensor_id, at=claimed_at - timedelta(hours=2), value=18.0)
    await add_reading(db_session, sensor_id, at=claimed_at + timedelta(hours=3), value=19.0)

    rows = await _first_readings(db_session, env.org_id)

    assert rows[0][4] == claimed_at + timedelta(hours=3)


async def test_a_timestamp_corrected_reading_is_still_valid(
    db_session: AsyncSession,
) -> None:
    """docs/04-api.md:83 excludes the out-of-range bit only, and
    `ReadingRepository.query_valid_raw` says the same: a `ts` corrected from
    `received_at` (bit 1) is still evidence a node answered. Filtering bit 1 as
    well would quietly drop the readings of every node with a drifting clock."""
    env = await make_env(db_session)
    claimed_at = bogota_midnight(MONTH)
    node_id = await add_claimed_node(db_session, env, claim_code="node-ts", claimed_at=claimed_at)
    sensor_id = await add_sensor(db_session, node_id, channel_key="sm-ts-20")
    await add_reading_row(
        db_session, sensor_id, at=claimed_at + timedelta(hours=9), value=25.0, quality=1
    )

    rows = await _first_readings(db_session, env.org_id)

    assert rows[0][4] == claimed_at + timedelta(hours=9)


async def test_the_month_of_a_node_is_the_bogota_month_of_its_claim(
    db_session: AsyncSession,
) -> None:
    """D-T0.7: the claim is an instant, so its month is built in
    America/Bogota. A node claimed at 02:00 UTC on Oct 1 was claimed at 21:00 on
    Sep 30 in the field, and belongs to September: a session running under UTC
    would move it a month back if the view read the instant without the zone.
    """
    env = await make_env(db_session)
    claimed_at = datetime(2026, 10, 1, 2, 0, tzinfo=UTC)
    node_id = await add_claimed_node(db_session, env, claim_code="node-edge", claimed_at=claimed_at)
    sensor_id = await add_sensor(db_session, node_id, channel_key="sm-edge-20")
    await add_reading(db_session, sensor_id, at=claimed_at + timedelta(hours=1), value=20.0)

    rows = await _first_readings(db_session, env.org_id)

    assert rows[0][0] == MONTH


async def test_the_node_view_ignores_another_organization(db_session: AsyncSession) -> None:
    """docs/09-cuellos-de-botella.md §Seguridad: a foreign organization's nodes
    are absent, so its enrollment latency never enters our median."""
    env_a = await make_env(db_session)
    env_b = await make_env(db_session)
    claimed_at = bogota_midnight(MONTH)
    node_id = await add_claimed_node(db_session, env_b, claim_code="node-b", claimed_at=claimed_at)
    sensor_id = await add_sensor(db_session, node_id, channel_key="sm-b-20")
    await add_reading(db_session, sensor_id, at=claimed_at + timedelta(hours=5), value=20.0)

    assert await _first_readings(db_session, env_a.org_id) == []
    assert len(await _first_readings(db_session, env_b.org_id)) == 1
