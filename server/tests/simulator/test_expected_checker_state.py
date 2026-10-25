"""E16 T4: the observed-state adapter over the real repositories.

`collect_observed_state` gathers what the stack actually serves for one scenario
plot: the non-resolved alerts (plot-scoped ones through the plot listing, and the
node-scoped ones — `node_offline` lives on the node, docs/06:477 — through the
same per-target read the evaluators use), and the stored recommendation for one
day. Everything is org-scoped; another org's state never comes back (docs/09).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.orm import AlertRow
from techcamp.alerts.adapters.repositories import (
    SqlAlchemyAlertRepository,
    SqlAlchemyAlertRuleRepository,
)
from techcamp.alerts.adapters.seed import FACTORY_RULES, seed_factory_rules
from techcamp.irrigation.adapters.orm import IrrigationRecommendationRow
from techcamp.irrigation.adapters.repositories import (
    SqlAlchemyIrrigationRecommendationRepository,
)
from techcamp.shared.ids import uuid7
from techcamp.simulator.application.expected_checker import collect_observed_state
from techcamp.telemetry.adapters.orm import NodeRow

from .helpers import make_plot, member

pytestmark = pytest.mark.anyio

_AT = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
_DAY = date(2026, 10, 24)


def _rule_id(code: str) -> UUID:
    return next(rule["id"] for rule in FACTORY_RULES if rule["code"] == code)


async def _seed_rules(db_session: AsyncSession) -> None:
    await seed_factory_rules(await db_session.connection())


async def _claimed_node(db_session: AsyncSession, org_id: UUID, plot_id: UUID) -> UUID:
    node_id = uuid7()
    db_session.add(
        NodeRow(
            id=node_id,
            org_id=org_id,
            plot_id=plot_id,
            transport="wifi",
            claim_code=f"claim-{node_id.hex}",
            credential_hash="hash",
            interval_s=900,
            claimed_at=_AT,
            status="online",
        )
    )
    await db_session.commit()
    return node_id


async def _open_alert(
    db_session: AsyncSession,
    *,
    org_id: UUID,
    code: str,
    plot_id: UUID | None,
    node_id: UUID | None,
    state: str = "open",
) -> None:
    db_session.add(
        AlertRow(
            id=uuid7(),
            org_id=org_id,
            rule_id=_rule_id(code),
            plot_id=plot_id,
            node_id=node_id,
            state=state,
            severity="warning",
            evidence={},
            opened_at=_AT,
        )
    )
    await db_session.commit()


async def _recommendation(
    db_session: AsyncSession, *, plot_id: UUID, day: date, kind: str, advice: list[str]
) -> None:
    db_session.add(
        IrrigationRecommendationRow(
            id=uuid7(),
            plot_id=plot_id,
            day=day,
            kind=kind,
            depth_mm=None,
            duration_min=None,
            advice=advice,
            rationale={},
        )
    )
    await db_session.commit()


def _repos(
    db_session: AsyncSession,
) -> tuple[
    SqlAlchemyAlertRepository,
    SqlAlchemyAlertRuleRepository,
    SqlAlchemyIrrigationRecommendationRepository,
]:
    return (
        SqlAlchemyAlertRepository(db_session),
        SqlAlchemyAlertRuleRepository(db_session),
        SqlAlchemyIrrigationRecommendationRepository(db_session),
    )


async def test_plot_scoped_and_node_scoped_alerts_both_come_back(
    db_session: AsyncSession,
) -> None:
    """Scenario A asserts [water_stress, heat_stress, node_offline]: the third is
    node-scoped, so the plot listing alone can never produce it (docs/06:437-440)."""
    org_id, _phone = await member(db_session)
    plot_id = await make_plot(db_session, org_id)
    node_id = await _claimed_node(db_session, org_id, plot_id)
    await _open_alert(db_session, org_id=org_id, code="water_stress", plot_id=plot_id, node_id=None)
    await _open_alert(db_session, org_id=org_id, code="node_offline", plot_id=None, node_id=node_id)

    alerts, rules, recommendations = _repos(db_session)
    observed = await collect_observed_state(
        org_id=org_id,
        plot_id=plot_id,
        node_ids=(node_id,),
        day=_DAY,
        alerts=alerts,
        rules=rules,
        recommendations=recommendations,
    )

    assert observed.alert_codes == ("water_stress", "node_offline")


async def test_resolved_alerts_are_not_observed(db_session: AsyncSession) -> None:
    org_id, _phone = await member(db_session)
    plot_id = await make_plot(db_session, org_id)
    await _open_alert(
        db_session,
        org_id=org_id,
        code="water_stress",
        plot_id=plot_id,
        node_id=None,
        state="resolved",
    )

    alerts, rules, recommendations = _repos(db_session)
    observed = await collect_observed_state(
        org_id=org_id,
        plot_id=plot_id,
        day=_DAY,
        alerts=alerts,
        rules=rules,
        recommendations=recommendations,
    )

    assert observed.alert_codes == ()


async def test_another_orgs_alert_never_comes_back(db_session: AsyncSession) -> None:
    """docs/09:71 — a cross-org read is an absent row, never a leak."""
    org_id, _phone = await member(db_session)
    other_org_id, _other = await member(db_session)
    plot_id = await make_plot(db_session, org_id)
    other_plot_id = await make_plot(db_session, other_org_id)
    await _open_alert(
        db_session, org_id=other_org_id, code="water_stress", plot_id=other_plot_id, node_id=None
    )

    alerts, rules, recommendations = _repos(db_session)
    observed = await collect_observed_state(
        org_id=org_id,
        plot_id=plot_id,
        day=_DAY,
        alerts=alerts,
        rules=rules,
        recommendations=recommendations,
    )

    assert observed.alert_codes == ()


async def test_a_node_of_another_org_is_not_asked(db_session: AsyncSession) -> None:
    """The node ids are the scenario's own; a foreign node id finds nothing."""
    org_id, _phone = await member(db_session)
    plot_id = await make_plot(db_session, org_id)

    alerts, rules, recommendations = _repos(db_session)
    observed = await collect_observed_state(
        org_id=org_id,
        plot_id=plot_id,
        node_ids=(uuid7(),),
        day=_DAY,
        alerts=alerts,
        rules=rules,
        recommendations=recommendations,
    )

    assert observed.alert_codes == ()


async def test_the_recommendation_of_the_asked_day_comes_back(db_session: AsyncSession) -> None:
    org_id, _phone = await member(db_session)
    plot_id = await make_plot(db_session, org_id)
    await _recommendation(
        db_session, plot_id=plot_id, day=_DAY, kind="rainfed", advice=["conserve_moisture"]
    )

    alerts, rules, recommendations = _repos(db_session)
    observed = await collect_observed_state(
        org_id=org_id,
        plot_id=plot_id,
        day=_DAY,
        alerts=alerts,
        rules=rules,
        recommendations=recommendations,
    )

    assert observed.irrigation_kind == "rainfed"
    assert observed.advice == ("conserve_moisture",)


async def test_no_recommendation_for_the_day_is_none(db_session: AsyncSession) -> None:
    """A missing stored recommendation is the checker's `None`, never another
    day's row — the caller names the day, the adapter asks exactly that one."""
    org_id, _phone = await member(db_session)
    plot_id = await make_plot(db_session, org_id)
    await _recommendation(
        db_session,
        plot_id=plot_id,
        day=date(2026, 10, 23),
        kind="rainfed",
        advice=["conserve_moisture"],
    )

    alerts, rules, recommendations = _repos(db_session)
    observed = await collect_observed_state(
        org_id=org_id,
        plot_id=plot_id,
        day=_DAY,
        alerts=alerts,
        rules=rules,
        recommendations=recommendations,
    )

    assert observed.irrigation_kind is None
    assert observed.advice == ()
