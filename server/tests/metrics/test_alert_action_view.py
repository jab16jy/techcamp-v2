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
