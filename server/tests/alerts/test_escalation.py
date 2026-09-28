"""The escalation clock of a critical alert (docs/06 §3 "Reloj de escalamiento",
"Escalar una alerta crítica"; docs/01:31 RF-08, :57 RNF-05; D3, D4, D5, D12,
D21, D30, D37).

Real Postgres, no doubles: what matters is the row the lock hands the sweep, the
`escalated_at` it writes and the outbox row it writes with it (ADR-0016), so the
repository is the only implementation of its own port here — the same reason
`test_lifecycle.py` runs the lifecycle this way.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.orm import AlertRuleRow
from techcamp.alerts.adapters.repositories import SqlAlchemyAlertRepository
from techcamp.alerts.application import acknowledge, open_alert, resolve_automatically
from techcamp.alerts.domain import ESCALATION_DELAY, Alert, AlertRule, Severity
from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.adapters.repositories import SqlAlchemyMembershipRepository
from techcamp.identity.domain.models import Role
from techcamp.notifications.adapters.orm import NotificationRow
from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.orm import NodeRow

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)
_OPENED = datetime(2026, 9, 26, 15, 0, tzinfo=UTC)  # 10:00 Bogotá, outside quiet hours
_DUE = _OPENED + ESCALATION_DELAY
"""Exactly the 2 h of docs/06 §3's clock, which `is_eligible_for_escalation`
includes: the deadline is reached, not passed."""


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


async def _make_org(
    session: AsyncSession, *, with_technician: bool = True, with_owner: bool = True
) -> Org:
    """One organization with people, a farm, a plot and a node.

    `with_technician=False` is the D4 fallback case (a farm nobody was assigned)
    and `with_owner=False` the third state that fallback itself has no answer for:
    an organization with no technician and no owner to fall back to.
    """
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
    roles = [
        (producer, Role.PRODUCER),
        (technician, Role.TECHNICIAN),
        (viewer, Role.VIEWER),
    ]
    if with_owner:
        roles.append((owner, Role.OWNER))
    for user_id, role in roles:
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
        threshold=float(row.threshold) if row.threshold is not None else None,
        hysteresis=float(row.hysteresis),
        min_duration=timedelta(minutes=row.min_duration_min),
        severity=Severity(row.severity),
    )


async def _open(
    session: AsyncSession,
    alerts: SqlAlchemyAlertRepository,
    org: Org,
    *,
    code: str,
    at: datetime = _OPENED,
    severity: Severity = Severity.CRITICAL,
) -> Alert:
    """An alert opened for a plot at `at`, critical unless a test says otherwise.

    `severity` is the D20 override an evaluator uses when the severity comes from
    the evidence instead of from the rule row; here it saves every test from
    inventing an org rule whose own severity is `critical`. The `code` is the
    test's own lever, because the partial unique index allows one non-resolved
    alert per (rule, plot).
    """
    return await open_alert(
        rule=await _rule(session, code),
        plot_id=org.plot_id,
        at=at,
        severity=severity,
        alerts=alerts,
    )


async def _rows(session: AsyncSession, alert_id: UUID, channel: str) -> list[NotificationRow]:
    result = await session.execute(
        select(NotificationRow).where(
            NotificationRow.alert_id == alert_id, NotificationRow.channel == channel
        )
    )
    return list(result.scalars())


# -- the candidate the lock hands the sweep (docs/06 §3, D12) --


async def test_the_lock_hands_the_oldest_due_critical_of_the_org_first(
    db_session: AsyncSession,
) -> None:
    """The overdue one escalates first: the sweep pages the org's due criticals in
    age order, so an alert unacknowledged longest is never starved by a newer one."""
    org = await _make_org(db_session)
    alerts = SqlAlchemyAlertRepository(db_session)
    older = await _open(db_session, alerts, org, code="water_stress", at=_OPENED)
    newer = await _open(
        db_session, alerts, org, code="heat_stress", at=_OPENED + timedelta(hours=1)
    )
    at = _DUE + timedelta(hours=1)

    candidate = await alerts.lock_escalation_candidate(org_id=org.org_id, at=at)

    assert candidate is not None
    assert (candidate.id, candidate.opened_at) == (older.id, _OPENED)
    # The negative half of the same fact: the lock does not skip the newer one, it
    # is simply second. What advances the page is escalating the row, which is
    # the caller's decision under this very lock.
    assert candidate.id != newer.id
    again = await alerts.lock_escalation_candidate(org_id=org.org_id, at=at)
    assert again is not None and again.id == older.id


async def test_the_lock_ignores_every_alert_the_escalation_clock_has_not_reached(
    db_session: AsyncSession,
) -> None:
    """docs/06 §3: only a CRITICAL that is still OPEN (not acknowledged, not
    resolved) escalates. Each of the four is old enough on every other axis."""
    org = await _make_org(db_session)
    alerts = SqlAlchemyAlertRepository(db_session)
    memberships = SqlAlchemyMembershipRepository(db_session)
    # A warning, open and old: the severity alone keeps it out.
    warning = await _open(db_session, alerts, org, code="water_stress", severity=Severity.WARNING)
    # A critical whose 2 h have not passed yet.
    young = await _open(db_session, alerts, org, code="heat_stress", at=_DUE - timedelta(minutes=1))
    # A critical the user already acknowledged: it is not "sin reconocer" anymore.
    acknowledged = await _open(db_session, alerts, org, code="waterlogging")
    await acknowledge(
        user_id=org.owner,
        alert_id=acknowledged.id,
        at=_OPENED + timedelta(minutes=1),
        alerts=alerts,
        memberships=memberships,
    )
    # A critical that already resolved.
    resolved = await _open(db_session, alerts, org, code="heavy_rain_forecast")
    await resolve_automatically(
        alert_id=resolved.id,
        org_id=org.org_id,
        farm_id=org.farm_id,
        at=_OPENED + timedelta(minutes=2),
        alerts=alerts,
    )
    assert len({warning.id, young.id, acknowledged.id, resolved.id}) == 4

    assert await alerts.lock_escalation_candidate(org_id=org.org_id, at=_DUE) is None


async def test_the_lock_of_one_org_never_reaches_another_orgs_alert(
    db_session: AsyncSession,
) -> None:
    """docs/09: the per-org fan-out exists so every read keeps its `org_id`, and an
    escalation must never reach another organization's technician."""
    org = await _make_org(db_session)
    other = await _make_org(db_session)
    alerts = SqlAlchemyAlertRepository(db_session)
    await _open(db_session, alerts, org, code="water_stress")

    assert await alerts.lock_escalation_candidate(org_id=other.org_id, at=_DUE) is None
