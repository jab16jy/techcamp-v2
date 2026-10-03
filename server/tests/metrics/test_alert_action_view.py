"""The `risk_management` read view: plot alerts and whether they were acted on.

E11 T3 (D-T0.6; docs/11-metricas.md §2). The view is proven against the real
database because the whole question is a window: the logbook records dates, not
hours, so "timely" is a comparison between local days that only the test can pin.

Every test carries its negative assertion: a node alert counts for nothing, an
action dated past the window is late, a tombstoned entry is not an action, a
rainfed plot cannot be answered by an irrigation, and another organization's plot
reads empty (docs/09-cuellos-de-botella.md §Seguridad).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from metrics.conftest import (
    add_alert,
    add_cycle,
    add_logbook_entry,
    add_node,
    add_node_alert,
    bogota_midnight,
    make_env,
)
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.metrics.adapters.source_repository import SqlAlchemyMetricsSourceRepository

pytestmark = pytest.mark.anyio

_OPENED_AT = bogota_midnight(date(2026, 9, 10)) + timedelta(hours=8)
"""Sep 10 at 08:00 in Bogota, so the timely window is the local days Sep 10 to
Sep 12 (D-T0.6): the logbook records dates, not hours, so an entry dated Sep 13
is already past 48 h."""
_SEPTEMBER = {"from_day": date(2026, 9, 1), "to_day": date(2026, 9, 30)}

# Three review findings about this view are closed by documents and the data
# model rather than by a test, because there is no behaviour left to pin. They
# are recorded here so the next review does not re-raise them.
#
# - `R3-reliability.alert-action.rule-window` asked for a per-rule timely window.
#   The window is one constant for the whole component by decision D-T0.6
#   (docs/11-metricas.md §2: "el día local de `opened_at + 48 h`"), and
#   `alert_rule` carries no action-window column — only `min_duration_min`, which
#   governs how long a *violation* must last before the rule fires, not how long
#   the producer has to answer. There is nothing per-rule to read.
# - `R3-reliability.alert-action.entry-scope` asked the alert-linked branch to
#   filter by `kind`. docs/03-modelo-datos.md:421 assigns that role to `alert_id`
#   on *any* entry ("cualquiera con `alert_id`: la entrada es la acción
#   registrada tras la alerta"), so no `kind` filter is the documented rule. The
#   observation-kind case is already asserted in
#   `test_plot_alert_view_scores_timely_actions_and_excludes_node_alerts`.
# - `R3-reliability.alert-action.repeat-entry` worried one entry satisfies every
#   alert whose window contains it. The alert-linked branch is keyed on
#   `e.alert_id = a.id`, so an entry answers exactly the one alert it names; only
#   the plot-scoped irrigation clause can serve two alerts at once, and
#   `uq_alert_non_resolved_plot` allows just one non-resolved alert per
#   (rule, plot), so two same-rule windows cannot overlap.


async def test_plot_alert_view_scores_timely_actions_and_excludes_node_alerts(
    db_session: AsyncSession,
) -> None:
    """D-T0.6: a `water_stress` alert on an irrigated plot is answered by a
    registered irrigation inside the window, any other plot alert by an entry
    carrying its `alert_id`, and a node alert is not in the view at all.

    One alert per rule, because `alert` allows a single non-resolved alert per
    (rule, target). `heat_stress` is only answered by an entry dated Sep 13, one
    day past the window, so it is late.
    """
    env = await make_env(db_session, irrigation_system="drip")
    node_id = await add_node(db_session, env, claim_code="node-a")
    water_stress = await add_alert(db_session, env, rule_code="water_stress", at=_OPENED_AT)
    fungal = await add_alert(db_session, env, rule_code="fungal_risk", at=_OPENED_AT)
    heat = await add_alert(db_session, env, rule_code="heat_stress", at=_OPENED_AT)
    await add_node_alert(db_session, org_id=env.org_id, node_id=node_id, at=_OPENED_AT)
    await add_logbook_entry(
        db_session, env, kind="irrigation", occurred_on=date(2026, 9, 12), irrigation_mm=12.0
    )
    await add_logbook_entry(
        db_session, env, kind="observation", occurred_on=date(2026, 9, 12), alert_id=fungal
    )
    await add_logbook_entry(
        db_session, env, kind="observation", occurred_on=date(2026, 9, 13), alert_id=heat
    )

    rows = await SqlAlchemyMetricsSourceRepository(db_session).plot_alert_actions(
        env.org_id, env.plot_id, **_SEPTEMBER
    )

    assert [(r.alert_id, r.rule_code, r.opened_at, r.has_timely_action) for r in rows] == [
        (water_stress, "water_stress", _OPENED_AT, True),
        (fungal, "fungal_risk", _OPENED_AT, True),
        (heat, "heat_stress", _OPENED_AT, False),
    ]


async def test_plot_alert_view_rejects_a_discarded_or_impossible_action(
    db_session: AsyncSession,
) -> None:
    """The negatives of the same rule: a tombstoned entry is not an action, and
    on a rainfed plot the `water_stress` irrigation clause cannot apply at all,
    because there is no irrigation to register (ADR-0023)."""
    rainfed = await make_env(db_session, name="Finca Secana", irrigation_system="none")
    stress = await add_alert(db_session, rainfed, rule_code="water_stress", at=_OPENED_AT)
    await add_logbook_entry(
        db_session, rainfed, kind="irrigation", occurred_on=date(2026, 9, 12), irrigation_mm=12.0
    )
    drip = await make_env(db_session, name="Finca Riego")
    heat = await add_alert(db_session, drip, rule_code="heat_stress", at=_OPENED_AT)
    await add_logbook_entry(
        db_session,
        drip,
        kind="observation",
        occurred_on=date(2026, 9, 12),
        alert_id=heat,
        deleted=True,
    )

    source = SqlAlchemyMetricsSourceRepository(db_session)

    assert [
        (r.alert_id, r.has_timely_action)
        for r in await source.plot_alert_actions(rainfed.org_id, rainfed.plot_id, **_SEPTEMBER)
    ] == [(stress, False)]
    assert [
        (r.alert_id, r.has_timely_action)
        for r in await source.plot_alert_actions(drip.org_id, drip.plot_id, **_SEPTEMBER)
    ] == [(heat, False)]


async def test_plot_alert_view_counts_an_irrigation_of_any_cycle_as_the_action(
    db_session: AsyncSession,
) -> None:
    """The bounded correction for `R3-reliability.alert-action.cross-plot`: the
    `water_stress` irrigation clause is scoped to the **plot**, not to a crop
    cycle, and that is now proven rather than incidental.

    docs/11:50 defines the action as "un riego registrado" with no cycle qualifier,
    and `logbook_entry.crop_cycle_id` is nullable (docs/03-modelo-datos.md:410),
    because the phone sends the cycle it has cached and often sends none. A
    cycle-scoped clause would therefore *discard* real actions. Both shapes score
    as acted on: an irrigation carrying a cycle that has nothing to do with the
    alert, and an irrigation carrying no cycle at all.

    The negative: neither counts outside the 48-hour window, so the clause stays a
    window and not a blanket "the plot ever irrigated".
    """
    dated = await make_env(db_session, name="Finca Con Ciclo")
    cycle_id = await add_cycle(
        db_session,
        dated,
        sown_on=date(2026, 9, 1),
        status="harvested",
        expected_harvest_on=date(2026, 9, 30),
    )
    dated_alert = await add_alert(db_session, dated, rule_code="water_stress", at=_OPENED_AT)
    await add_logbook_entry(
        db_session,
        dated,
        kind="irrigation",
        occurred_on=date(2026, 9, 12),
        irrigation_mm=12.0,
        crop_cycle_id=cycle_id,
    )
    undated = await make_env(db_session, name="Finca Sin Ciclo")
    undated_alert = await add_alert(db_session, undated, rule_code="water_stress", at=_OPENED_AT)
    await add_logbook_entry(
        db_session, undated, kind="irrigation", occurred_on=date(2026, 9, 12), irrigation_mm=9.0
    )
    late = await make_env(db_session, name="Finca Tardía")
    late_alert = await add_alert(db_session, late, rule_code="water_stress", at=_OPENED_AT)
    await add_logbook_entry(
        db_session, late, kind="irrigation", occurred_on=date(2026, 9, 13), irrigation_mm=9.0
    )

    source = SqlAlchemyMetricsSourceRepository(db_session)
    scored = {
        r.alert_id: r.has_timely_action
        for env, _ in ((dated, None), (undated, None), (late, None))
        for r in await source.plot_alert_actions(env.org_id, env.plot_id, **_SEPTEMBER)
    }

    assert scored == {
        dated_alert: True,  # irrigation tagged with a cycle
        undated_alert: True,  # irrigation with no cycle at all
        late_alert: False,  # one day past the window
    }


async def test_plot_alert_view_reads_only_the_plot_and_month_it_was_asked_for(
    db_session: AsyncSession,
) -> None:
    """docs/09 §Seguridad, for the alert view: another organization's plot reads
    empty, and an alert opened in October is outside a September answer."""
    env = await make_env(db_session)
    await add_alert(db_session, env, rule_code="water_stress", at=_OPENED_AT)
    other = await make_env(db_session, name="Finca Otra")
    source = SqlAlchemyMetricsSourceRepository(db_session)

    assert await source.plot_alert_actions(other.org_id, other.plot_id, **_SEPTEMBER) == []
    assert (
        await source.plot_alert_actions(
            env.org_id, env.plot_id, from_day=date(2026, 10, 1), to_day=date(2026, 10, 31)
        )
        == []
    )
