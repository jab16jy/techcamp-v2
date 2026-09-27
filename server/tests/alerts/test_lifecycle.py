"""Alert lifecycle and its outbox rows (docs/06-diseno-detallado.md §3, §4; ADR-0015, ADR-0016).

Real Postgres, no test doubles: the repository is the only implementation of
its own port here, and what matters is the rows and the `NOTIFY` it commits.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.orm import AlertRow, AlertRuleRow
from techcamp.alerts.adapters.repositories import SqlAlchemyAlertRepository
from techcamp.alerts.application import (
    acknowledge,
    list_alerts,
    open_alert,
    resolve_automatically,
    resolve_manually,
    upgrade_to_critical,
)
from techcamp.alerts.domain import (
    Alert,
    AlertNotFoundError,
    AlertRule,
    AlertState,
    InsufficientRoleError,
    InvalidAlertTransitionError,
    Severity,
)
from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.adapters.repositories import SqlAlchemyMembershipRepository
from techcamp.identity.domain.models import Role
from techcamp.notifications.adapters.orm import NotificationRow
from techcamp.notifications.adapters.repositories import SqlAlchemyNotificationRepository
from techcamp.shared.config import database_url
from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.orm import NodeRow
from techcamp.telemetry.adapters.sse_hub import PlotEventsHub

pytestmark = pytest.mark.anyio

_BOGOTA = ZoneInfo("America/Bogota")
_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)
_MORNING = datetime(2026, 9, 26, 15, 0, tzinfo=UTC)  # 10:00 Bogotá, outside quiet hours


def _dsn() -> str:
    return database_url().replace("postgresql+asyncpg://", "postgresql://")


@dataclass(frozen=True, slots=True)
class Org:
    org_id: UUID
    farm_id: UUID
    plot_id: UUID
    node_id: UUID
    owner: UUID
    producer: UUID
    technician: UUID
    viewer: UUID


@pytest.fixture
def alerts(db_session: AsyncSession) -> SqlAlchemyAlertRepository:
    return SqlAlchemyAlertRepository(db_session)


@pytest.fixture
def memberships(db_session: AsyncSession) -> SqlAlchemyMembershipRepository:
    return SqlAlchemyMembershipRepository(db_session)


async def _make_org(session: AsyncSession, *, with_technician: bool = True) -> Org:
    org_id = uuid7()
    owner, producer, technician, viewer = uuid7(), uuid7(), uuid7(), uuid7()
    for user_id, name in (
        (owner, "Owner"),
        (producer, "Producer"),
        (technician, "Technician"),
        (viewer, "Viewer"),
    ):
        session.add(AppUserRow(id=user_id, phone=f"+57{uuid7().int % 10**13:013d}", full_name=name))
    session.add(OrganizationRow(id=org_id, name="Test Org", kind="individual"))
    await session.commit()
    for user_id, role in (
        (owner, Role.OWNER),
        (producer, Role.PRODUCER),
        (technician, Role.TECHNICIAN),
        (viewer, Role.VIEWER),
    ):
        session.add(MembershipRow(org_id=org_id, user_id=user_id, role=role.value))
    await session.commit()

    farm_id, plot_id, node_id = uuid7(), uuid7(), uuid7()
    session.add(
        FarmRow(
            id=farm_id,
            org_id=org_id,
            name="Finca Principal",
            municipality_code="47001",
            location=_POINT,
            technician_id=technician if with_technician else None,
        )
    )
    await session.commit()
    session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=_BOUNDARY,
            irrigation_system="drip",
        )
    )
    await session.commit()
    session.add(
        NodeRow(
            id=node_id,
            org_id=org_id,
            plot_id=plot_id,
            transport="wifi",
            claim_code=f"claim-{uuid7().hex}",
            credential_hash="hash",
            interval_s=300,
            claimed_at=datetime.now(UTC),
            status="online",
        )
    )
    await session.commit()
    return Org(
        org_id=org_id,
        farm_id=farm_id,
        plot_id=plot_id,
        node_id=node_id,
        owner=owner,
        producer=producer,
        technician=technician,
        viewer=viewer,
    )


async def _rule(session: AsyncSession, code: str) -> AlertRule:
    """A factory rule as the evaluator holds it (docs/06 §3, `org_id = null`)."""
    row = (
        await session.execute(select(AlertRuleRow).where(AlertRuleRow.code == code))
    ).scalar_one()
    return AlertRule(
        code=row.code,
        id=row.id,
        org_id=row.org_id,
        metric=row.metric,
        operator=row.operator,
        threshold=row.threshold,
        hysteresis=row.hysteresis,
        min_duration=timedelta(minutes=row.min_duration_min),
        severity=Severity(row.severity),
        crop_id=row.crop_id,
    )


async def _rows(session: AsyncSession, alert_id: UUID) -> list[NotificationRow]:
    result = await session.execute(
        select(NotificationRow).where(NotificationRow.alert_id == alert_id)
    )
    return list(result.scalars())


async def _open_plot_alert(
    session: AsyncSession, alerts: SqlAlchemyAlertRepository, org: Org, code: str, at: datetime
) -> Alert:
    return await open_alert(
        rule=await _rule(session, code),
        plot_id=org.plot_id,
        evidence={"at": at.isoformat()},
        at=at,
        alerts=alerts,
    )


async def test_opening_a_warning_plot_alert_notifies_owners_and_producers_only(
    db_session: AsyncSession, alerts: SqlAlchemyAlertRepository
) -> None:
    org = await _make_org(db_session)

    alert = await _open_plot_alert(db_session, alerts, org, "water_stress", _MORNING)

    assert (alert.state, alert.severity) == (AlertState.OPEN, Severity.WARNING)
    assert (alert.org_id, alert.plot_id, alert.node_id) == (org.org_id, org.plot_id, None)
    assert alert.rule_code == "water_stress"
    row = (await db_session.execute(select(AlertRow).where(AlertRow.id == alert.id))).scalar_one()
    assert row.state == "open"

    rows = await _rows(db_session, alert.id)
    assert {row.user_id for row in rows} == {org.owner, org.producer}
    assert {row.channel for row in rows} == {"push"}
    assert {row.status for row in rows} == {"pending"}
    assert {row.next_attempt_at for row in rows} == {_MORNING}


async def test_opening_an_info_alert_writes_no_notification_row(
    db_session: AsyncSession, alerts: SqlAlchemyAlertRepository
) -> None:
    """`info` is in-app only: no outbox row (docs/06 §4, D5)."""
    org = await _make_org(db_session)

    alert = await open_alert(
        rule=await _rule(db_session, "node_battery_low"),
        node_id=org.node_id,
        evidence={"battery_v": 3.2},
        at=_MORNING,
        alerts=alerts,
    )

    assert alert.severity == Severity.INFO
    assert await _rows(db_session, alert.id) == []


async def test_opening_a_critical_alert_writes_push_rows_due_now(
    db_session: AsyncSession, alerts: SqlAlchemyAlertRepository
) -> None:
    """A critical ignores quiet hours and is due now (docs/06 §4, D6)."""
    org = await _make_org(db_session)
    at_night = datetime(2026, 9, 26, 22, 0, tzinfo=_BOGOTA).astimezone(UTC)
    # `heavy_rain_forecast` is critical when the soil is already saturated.
    rule = replace(await _rule(db_session, "heavy_rain_forecast"), severity=Severity.CRITICAL)

    alert = await open_alert(
        rule=rule, plot_id=org.plot_id, evidence={"rain_mm": 55.0}, at=at_night, alerts=alerts
    )

    rows = await _rows(db_session, alert.id)
    assert {row.user_id for row in rows} == {org.owner, org.producer}
    assert {row.next_attempt_at for row in rows} == {at_night}


async def test_opening_twice_returns_the_same_alert_without_extra_rows(
    db_session: AsyncSession, alerts: SqlAlchemyAlertRepository
) -> None:
    """One non-resolved alert per (rule, target), so no second notice (docs/06 §3)."""
    org = await _make_org(db_session)

    first = await _open_plot_alert(db_session, alerts, org, "water_stress", _MORNING)
    again = await _open_plot_alert(
        db_session, alerts, org, "water_stress", _MORNING + timedelta(minutes=5)
    )

    assert again.id == first.id
    assert again.opened_at == _MORNING
    assert len(await _rows(db_session, first.id)) == 2


async def test_node_alert_goes_to_the_farm_technician(
    db_session: AsyncSession, alerts: SqlAlchemyAlertRepository
) -> None:
    """Node alerts notify the technician, not the producer (docs/06 §3, D4)."""
    org = await _make_org(db_session)

    alert = await open_alert(
        rule=await _rule(db_session, "node_offline"),
        node_id=org.node_id,
        evidence={"missing_intervals": 3},
        at=_MORNING,
        alerts=alerts,
    )

    rows = await _rows(db_session, alert.id)
    assert [row.user_id for row in rows] == [org.technician]


async def test_node_alert_without_a_technician_falls_back_to_the_owners(
    db_session: AsyncSession, alerts: SqlAlchemyAlertRepository
) -> None:
    org = await _make_org(db_session, with_technician=False)

    alert = await open_alert(
        rule=await _rule(db_session, "node_offline"),
        node_id=org.node_id,
        evidence={"missing_intervals": 3},
        at=_MORNING,
        alerts=alerts,
    )

    assert {row.user_id for row in await _rows(db_session, alert.id)} == {org.owner}


async def test_a_second_warning_of_the_same_farm_joins_the_pending_group(
    db_session: AsyncSession, alerts: SqlAlchemyAlertRepository
) -> None:
    """Grouping: a non-critical row within 15 min takes the pending row's time (D6)."""
    org = await _make_org(db_session)
    now = datetime.now(UTC)

    first = await _open_plot_alert(db_session, alerts, org, "water_stress", now)
    second = await _open_plot_alert(
        db_session, alerts, org, "heat_stress", now + timedelta(minutes=1)
    )

    first_due = {row.next_attempt_at for row in await _rows(db_session, first.id)}
    second_due = {row.next_attempt_at for row in await _rows(db_session, second.id)}
    assert second_due == first_due


async def test_a_failure_after_the_alert_insert_rolls_back_everything(
    db_session: AsyncSession, alerts: SqlAlchemyAlertRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No alert without its notice: the outbox is one transaction (ADR-0016)."""
    org = await _make_org(db_session)

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("outbox write failed")

    monkeypatch.setattr(SqlAlchemyNotificationRepository, "insert_drafts", _boom)

    with pytest.raises(RuntimeError, match="outbox write failed"):
        await _open_plot_alert(db_session, alerts, org, "water_stress", _MORNING)

    assert (await db_session.execute(select(AlertRow))).scalars().all() == []
    assert (await db_session.execute(select(NotificationRow))).scalars().all() == []


async def test_upgrade_to_critical_notifies_again_as_critical(
    db_session: AsyncSession, alerts: SqlAlchemyAlertRepository
) -> None:
    org = await _make_org(db_session)
    later = _MORNING + timedelta(hours=48)
    alert = await _open_plot_alert(db_session, alerts, org, "water_stress", _MORNING)

    upgraded = await upgrade_to_critical(
        alert_id=alert.id, org_id=org.org_id, at=later, alerts=alerts
    )

    assert (upgraded.id, upgraded.severity) == (alert.id, Severity.CRITICAL)
    assert upgraded.opened_at == _MORNING
    rows = await _rows(db_session, alert.id)
    assert len(rows) == 4
    assert {row.next_attempt_at for row in rows if row.next_attempt_at == later} == {later}


async def test_resolve_automatically_resolves_and_refuses_a_second_resolve(
    db_session: AsyncSession, alerts: SqlAlchemyAlertRepository
) -> None:
    org = await _make_org(db_session)
    alert = await _open_plot_alert(db_session, alerts, org, "water_stress", _MORNING)

    resolved = await resolve_automatically(
        alert_id=alert.id,
        org_id=org.org_id,
        farm_id=org.farm_id,
        at=_MORNING + timedelta(hours=1),
        alerts=alerts,
    )

    assert resolved.state == AlertState.RESOLVED
    row = (await db_session.execute(select(AlertRow).where(AlertRow.id == alert.id))).scalar_one()
    assert (row.state, row.resolved_at) == ("resolved", _MORNING + timedelta(hours=1))

    with pytest.raises(InvalidAlertTransitionError):
        await resolve_automatically(
            alert_id=alert.id,
            org_id=org.org_id,
            farm_id=org.farm_id,
            at=_MORNING + timedelta(hours=2),
            alerts=alerts,
        )


async def test_acknowledge_then_resolve_manually_stores_the_note(
    db_session: AsyncSession,
    alerts: SqlAlchemyAlertRepository,
    memberships: SqlAlchemyMembershipRepository,
) -> None:
    org = await _make_org(db_session)
    alert = await _open_plot_alert(db_session, alerts, org, "water_stress", _MORNING)

    acknowledged = await acknowledge(
        user_id=org.producer,
        alert_id=alert.id,
        farm_id=org.farm_id,
        at=_MORNING + timedelta(minutes=1),
        alerts=alerts,
        memberships=memberships,
    )
    assert (acknowledged.state, acknowledged.acknowledged_at) == (
        AlertState.ACKNOWLEDGED,
        _MORNING + timedelta(minutes=1),
    )

    resolved = await resolve_manually(
        user_id=org.owner,
        alert_id=alert.id,
        farm_id=org.farm_id,
        note="Válvula abierta",
        at=_MORNING + timedelta(minutes=2),
        alerts=alerts,
        memberships=memberships,
    )
    assert (resolved.state, resolved.resolution_note) == (AlertState.RESOLVED, "Válvula abierta")
    # A lifecycle change notifies nobody new: the rows are per severity, not per state.
    assert len(await _rows(db_session, alert.id)) == 2


async def test_an_alert_of_another_org_is_not_found(
    db_session: AsyncSession,
    alerts: SqlAlchemyAlertRepository,
    memberships: SqlAlchemyMembershipRepository,
) -> None:
    """docs/04: another org's alert responds 404, never 403."""
    org = await _make_org(db_session)
    other = await _make_org(db_session)
    alert = await _open_plot_alert(db_session, alerts, org, "water_stress", _MORNING)

    with pytest.raises(AlertNotFoundError):
        await acknowledge(
            user_id=other.owner,
            alert_id=alert.id,
            farm_id=org.farm_id,
            at=_MORNING,
            alerts=alerts,
            memberships=memberships,
        )
    with pytest.raises(AlertNotFoundError):
        await resolve_manually(
            user_id=other.owner,
            alert_id=alert.id,
            farm_id=org.farm_id,
            note=None,
            at=_MORNING,
            alerts=alerts,
            memberships=memberships,
        )


async def test_a_viewer_may_not_acknowledge_or_resolve(
    db_session: AsyncSession,
    alerts: SqlAlchemyAlertRepository,
    memberships: SqlAlchemyMembershipRepository,
) -> None:
    org = await _make_org(db_session)
    alert = await _open_plot_alert(db_session, alerts, org, "water_stress", _MORNING)

    with pytest.raises(InsufficientRoleError):
        await acknowledge(
            user_id=org.viewer,
            alert_id=alert.id,
            farm_id=org.farm_id,
            at=_MORNING,
            alerts=alerts,
            memberships=memberships,
        )
    with pytest.raises(InsufficientRoleError):
        await resolve_manually(
            user_id=org.viewer,
            alert_id=alert.id,
            farm_id=org.farm_id,
            note=None,
            at=_MORNING,
            alerts=alerts,
            memberships=memberships,
        )


async def test_manual_resolve_from_open_is_an_invalid_transition(
    db_session: AsyncSession,
    alerts: SqlAlchemyAlertRepository,
    memberships: SqlAlchemyMembershipRepository,
) -> None:
    """Only from `acknowledged` (docs/06 §3 diagram, D12)."""
    org = await _make_org(db_session)
    alert = await _open_plot_alert(db_session, alerts, org, "water_stress", _MORNING)

    with pytest.raises(InvalidAlertTransitionError):
        await resolve_manually(
            user_id=org.owner,
            alert_id=alert.id,
            farm_id=org.farm_id,
            note="cerrada",
            at=_MORNING,
            alerts=alerts,
            memberships=memberships,
        )


async def test_opened_and_updated_reach_plot_events_and_the_sse_hub(
    db_session: AsyncSession,
    alerts: SqlAlchemyAlertRepository,
    memberships: SqlAlchemyMembershipRepository,
) -> None:
    org = await _make_org(db_session)
    payloads: list[str] = []

    def _on_notify(_conn: object, _pid: int, _channel: str, payload: str) -> None:
        payloads.append(payload)

    listener = await asyncpg.connect(dsn=_dsn())
    await listener.add_listener("plot_events", _on_notify)
    try:
        alert = await _open_plot_alert(db_session, alerts, org, "heat_stress", _MORNING)
        await acknowledge(
            user_id=org.owner,
            alert_id=alert.id,
            farm_id=org.farm_id,
            at=_MORNING,
            alerts=alerts,
            memberships=memberships,
        )
        await asyncio.sleep(0.2)  # let the listener connection process the NOTIFY
    finally:
        await listener.close()

    assert len(payloads) == 2
    opened, updated = (json.loads(payload) for payload in payloads)
    assert opened["type"] == "alert.opened"
    assert opened["farm_id"] == str(org.farm_id)
    assert (opened["id"], opened["rule_code"], opened["state"]) == (
        str(alert.id),
        "heat_stress",
        "open",
    )
    assert (opened["plot_id"], opened["severity"], opened["opened_at"]) == (
        str(org.plot_id),
        "warning",
        _MORNING.isoformat(),
    )
    assert updated["type"] == "alert.updated"
    assert (updated["id"], updated["state"]) == (str(alert.id), "acknowledged")

    # The hub routes by farm and forwards both kinds without importing alerts (D9).
    hub = PlotEventsHub()
    client_id, queue = hub.subscribe(org.farm_id)
    hub.claim(client_id)
    try:
        hub.dispatch(payloads[0])
        hub.dispatch(payloads[1])
        events = [queue.get_nowait(), queue.get_nowait()]
    finally:
        hub.unsubscribe(client_id)
    assert [event.event for event in events] == ["alert.opened", "alert.updated"]
    assert events[0].farm_id == org.farm_id
    assert events[0].data["rule_code"] == "heat_stress"
    assert events[1].data["state"] == "acknowledged"


async def test_list_alerts_filters_and_pages_by_cursor(
    db_session: AsyncSession,
    alerts: SqlAlchemyAlertRepository,
    memberships: SqlAlchemyMembershipRepository,
) -> None:
    org = await _make_org(db_session)
    other = await _make_org(db_session)
    water = await _open_plot_alert(db_session, alerts, org, "water_stress", _MORNING)
    heat = await _open_plot_alert(db_session, alerts, org, "heat_stress", _MORNING)
    node = await open_alert(
        rule=await _rule(db_session, "node_offline"),
        node_id=org.node_id,
        evidence={},
        at=_MORNING,
        alerts=alerts,
    )
    await _open_plot_alert(db_session, alerts, other, "water_stress", _MORNING)

    def _ids(alerts_found: list[Alert]) -> set[UUID]:
        return {alert.id for alert in alerts_found}

    mine = await list_alerts(user_id=org.owner, alerts=alerts, memberships=memberships)
    assert _ids(mine) == {water.id, heat.id, node.id}  # never the other org's alert

    by_plot = await list_alerts(
        user_id=org.owner, plot_id=org.plot_id, alerts=alerts, memberships=memberships
    )
    assert _ids(by_plot) == {water.id, heat.id}

    still_open = await list_alerts(
        user_id=org.owner, state=AlertState.ACKNOWLEDGED, alerts=alerts, memberships=memberships
    )
    assert still_open == []

    page = await list_alerts(user_id=org.owner, limit=2, alerts=alerts, memberships=memberships)
    assert [alert.id for alert in page] == sorted({water.id, heat.id, node.id}, reverse=True)[:2]
    rest = await list_alerts(
        user_id=org.owner, limit=2, cursor=page[-1].id, alerts=alerts, memberships=memberships
    )
    assert [alert.id for alert in rest] == [min({water.id, heat.id, node.id})]
