"""Node health decided on the absence of evidence (docs/06 §3, "Salud del nodo";
D18, D21).

`node_offline` is not a threshold: a rule with no `operator` answers
`NO_ACTION` in `decide_alert`, so the decision is its own pure function. The
domain tests below are pure; the use-case tests after them run the real
Postgres evaluator over real nodes and readings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.orm import AlertRow, AlertRuleRow
from techcamp.alerts.adapters.repositories import (
    SqlAlchemyAlertRepository,
    SqlAlchemyAlertRuleRepository,
)
from techcamp.alerts.application import evaluate_node_health
from techcamp.alerts.domain import (
    Alert,
    AlertAction,
    AlertState,
    Severity,
    decide_node_health,
)
from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.domain.models import Role
from techcamp.notifications.adapters.orm import NotificationRow
from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.orm import NodeRow, SensorRow
from techcamp.telemetry.adapters.repositories import (
    SqlAlchemyNodeRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemySensorRepository,
)
from techcamp.telemetry.domain.models import ReadingRecord

pytestmark = pytest.mark.anyio

_AT = datetime(2026, 9, 26, 15, 0, tzinfo=UTC)  # 10:00 Bogotá, outside quiet hours
_INTERVAL_S = 300
_MARGIN = timedelta(seconds=3 * _INTERVAL_S)
"""3 × `interval_s`, the margin docs/06 §3 gives `node_offline` and `max_gap`."""

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)
_STEP = timedelta(minutes=5)
"""One reading every `interval_s`, so the series has no gap past the margin."""


@dataclass(frozen=True, slots=True)
class Node:
    org_id: UUID
    farm_id: UUID
    node_id: UUID
    sensor_id: int
    owner_id: UUID
    technician_id: UUID | None


async def _make_node(
    db_session: AsyncSession,
    *,
    last_seen_at: datetime | None,
    interval_s: int = _INTERVAL_S,
    with_technician: bool = True,
) -> Node:
    """One organization with a farm, a plot, a claimed node and its sensor, plus
    the two people D4 chooses between: the farm's technician and an org owner."""
    org_id, farm_id, plot_id, node_id = uuid7(), uuid7(), uuid7(), uuid7()
    owner_id = uuid7()
    db_session.add(
        AppUserRow(id=owner_id, phone=f"+57{uuid7().int % 10**13:013d}", full_name="Owner")
    )
    technician_id: UUID | None = None
    if with_technician:
        technician_id = uuid7()
        db_session.add(
            AppUserRow(
                id=technician_id, phone=f"+57{uuid7().int % 10**13:013d}", full_name="Technician"
            )
        )
    db_session.add(OrganizationRow(id=org_id, name="Test Org", kind="individual"))
    await db_session.commit()
    db_session.add(MembershipRow(org_id=org_id, user_id=owner_id, role=Role.OWNER.value))
    if technician_id is not None:
        db_session.add(
            MembershipRow(org_id=org_id, user_id=technician_id, role=Role.TECHNICIAN.value)
        )
    db_session.add(
        FarmRow(
            id=farm_id,
            org_id=org_id,
            name="Finca Principal",
            municipality_code="47001",
            location=_POINT,
            technician_id=technician_id,
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
    db_session.add(
        NodeRow(
            id=node_id,
            org_id=org_id,
            plot_id=plot_id,
            transport="wifi",
            claim_code=f"claim-{uuid7().hex}",
            credential_hash="hash",
            interval_s=interval_s,
            claimed_at=_AT - timedelta(days=1),
            last_seen_at=last_seen_at,
            status="online" if last_seen_at else "provisioned",
        )
    )
    sensor = SensorRow(node_id=node_id, channel_key="sm_0", metric="soil_moisture", unit="pct")
    db_session.add(sensor)
    await db_session.commit()
    await db_session.refresh(sensor)
    return Node(org_id, farm_id, node_id, sensor.id, owner_id, technician_id)


async def _evaluate(db_session: AsyncSession, *, org_id: UUID, at: datetime) -> None:
    await evaluate_node_health(
        org_id=org_id,
        at=at,
        rules=SqlAlchemyAlertRuleRepository(db_session),
        nodes=SqlAlchemyNodeRepository(db_session),
        sensors=SqlAlchemySensorRepository(db_session),
        readings=SqlAlchemyReadingRepository(db_session),
        alerts=SqlAlchemyAlertRepository(db_session),
    )


async def _alerts(db_session: AsyncSession, node: Node) -> list[tuple[str, AlertRow]]:
    """`(rule_code, row)` of the node's alerts, oldest first. The code lives on
    `alert_rule` (the repository joins it), so the read joins it too."""
    rows = await db_session.execute(
        select(AlertRuleRow.code, AlertRow)
        .join(AlertRuleRow, AlertRuleRow.id == AlertRow.rule_id)
        .where(AlertRow.node_id == node.node_id)
        .order_by(AlertRow.opened_at)
    )
    return [(code, row) for code, row in rows]


async def _notified(db_session: AsyncSession, alert_id: UUID) -> set[UUID]:
    return set(
        (
            await db_session.execute(
                select(NotificationRow.user_id).where(NotificationRow.alert_id == alert_id)
            )
        ).scalars()
    )


async def _hear_from(
    db_session: AsyncSession, node: Node, *, start: datetime, minutes: int
) -> None:
    """Backdated readings every `interval_s` for `minutes`, so the node is heard
    from continuously over that span (D1: the evaluation is stateless over the
    stored readings)."""
    count = minutes // 5
    await SqlAlchemyReadingRepository(db_session).insert_batch(
        [
            ReadingRecord(
                sensor_id=node.sensor_id,
                time=start + _STEP * index,
                raw_value=20.0,
                value=20.0,
                received_at=start + _STEP * index,
                quality=0,
            )
            for index in range(count + 1)
        ]
    )


def _open_alert() -> Alert:
    return Alert(
        state=AlertState.OPEN,
        severity=Severity.WARNING,
        opened_at=_AT - timedelta(hours=1),
        id=uuid7(),
        org_id=uuid7(),
        rule_id=uuid7(),
        rule_code="node_offline",
        node_id=uuid7(),
    )


def test_the_node_health_decision_opens_only_past_three_intervals_of_silence() -> None:
    silent = decide_node_health(
        last_seen_at=_AT - _MARGIN - timedelta(seconds=1), at=_AT, interval_s=_INTERVAL_S
    )
    assert silent.action is AlertAction.OPEN
    assert silent.alert is None

    # Exactly three intervals is the margin itself, not past it: `>` is strict.
    on_margin = decide_node_health(last_seen_at=_AT - _MARGIN, at=_AT, interval_s=_INTERVAL_S)
    assert on_margin.action is AlertAction.NO_ACTION

    # A node that never reported is the absence of evidence in its purest form.
    never = decide_node_health(last_seen_at=None, at=_AT, interval_s=_INTERVAL_S)
    assert never.action is AlertAction.OPEN


def test_the_node_health_decision_resolves_only_after_the_sixty_minute_clear_run() -> None:
    alert = _open_alert()

    # Back for 30 min: not the 60 min the resolution window demands (docs/06 §3).
    flapping = decide_node_health(
        last_seen_at=_AT,
        at=_AT,
        interval_s=_INTERVAL_S,
        current_alert=alert,
        heard_run=timedelta(minutes=30),
    )
    assert flapping.action is AlertAction.NO_ACTION
    assert flapping.alert is alert

    silent_again = decide_node_health(
        last_seen_at=_AT - _MARGIN - timedelta(seconds=1),
        at=_AT,
        interval_s=_INTERVAL_S,
        current_alert=alert,
        heard_run=None,
    )
    assert silent_again.action is AlertAction.NO_ACTION
    assert silent_again.alert is alert

    cleared = decide_node_health(
        last_seen_at=_AT,
        at=_AT,
        interval_s=_INTERVAL_S,
        current_alert=alert,
        heard_run=timedelta(minutes=60),
    )
    assert cleared.action is AlertAction.RESOLVE
    assert cleared.alert is not None
    assert cleared.alert.state is AlertState.RESOLVED
    assert cleared.alert.resolved_at == _AT


# -- the use case over real nodes: who is notified, and what is not (D4, D21) --


async def test_a_silent_node_opens_a_node_offline_alert_for_the_farms_technician(
    db_session: AsyncSession,
) -> None:
    node = await _make_node(db_session, last_seen_at=_AT - _MARGIN - timedelta(seconds=1))

    await _evaluate(db_session, org_id=node.org_id, at=_AT)

    alerts = await _alerts(db_session, node)
    assert len(alerts) == 1
    code, alert = alerts[0]
    assert code == "node_offline"
    assert alert.state == AlertState.OPEN.value
    assert alert.severity == Severity.WARNING.value
    assert alert.org_id == node.org_id
    assert alert.plot_id is None
    assert alert.opened_at.replace(tzinfo=UTC) == _AT
    # docs/06 §3: a node alert goes to the technician, not to the producer.
    assert await _notified(db_session, alert.id) == {node.technician_id}


async def test_a_silent_node_reaches_the_org_owners_when_the_farm_has_no_technician(
    db_session: AsyncSession,
) -> None:
    node = await _make_node(db_session, last_seen_at=None, with_technician=False)

    await _evaluate(db_session, org_id=node.org_id, at=_AT)

    alerts = await _alerts(db_session, node)
    assert [code for code, _ in alerts] == ["node_offline"]
    assert await _notified(db_session, alerts[0][1].id) == {node.owner_id}


async def test_a_node_heard_from_inside_the_margin_opens_nothing(db_session: AsyncSession) -> None:
    node = await _make_node(db_session, last_seen_at=_AT - _MARGIN)

    await _evaluate(db_session, org_id=node.org_id, at=_AT)

    assert await _alerts(db_session, node) == []


async def test_another_orgs_node_is_never_evaluated(db_session: AsyncSession) -> None:
    mine = await _make_node(db_session, last_seen_at=_AT - timedelta(hours=2))
    other = await _make_node(db_session, last_seen_at=_AT - timedelta(hours=2))

    await _evaluate(db_session, org_id=mine.org_id, at=_AT)

    assert [code for code, _ in await _alerts(db_session, mine)] == ["node_offline"]
    # The other org's node is just as silent, and stays that way: the sweep
    # pages one org at a time, so no other org is ever read.
    assert await _alerts(db_session, other) == []


async def test_an_open_alert_resolves_only_after_sixty_minutes_of_being_heard_from(
    db_session: AsyncSession,
) -> None:
    node = await _make_node(db_session, last_seen_at=_AT - _MARGIN - timedelta(seconds=1))
    await _evaluate(db_session, org_id=node.org_id, at=_AT)
    opened = (await _alerts(db_session, node))[0][1]
    # The node came back 30 min before the next sweep: heard from, but not for
    # the 60 min docs/06 §3 asks for.
    await _hear_from(db_session, node, start=_AT - timedelta(minutes=30), minutes=30)

    await _evaluate(db_session, org_id=node.org_id, at=_AT)

    assert (await _alerts(db_session, node))[0][1].state == AlertState.OPEN.value

    # Sixty minutes of continuous presence: the open alert resolves.
    await _hear_from(db_session, node, start=_AT - timedelta(minutes=60), minutes=60)
    later = _AT + timedelta(minutes=5)

    await _evaluate(db_session, org_id=node.org_id, at=later)

    resolved = (await _alerts(db_session, node))[0][1]
    assert resolved.id == opened.id
    assert resolved.state == AlertState.RESOLVED.value
    assert resolved.resolved_at.replace(tzinfo=UTC) == later
