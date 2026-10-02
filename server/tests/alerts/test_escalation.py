"""The escalation clock of a critical alert (docs/06 §3 "Reloj de escalamiento",
"Escalar una alerta crítica"; docs/01:31 RF-08, :57 RNF-05; D3, D4, D5, D12,
D21, D30, D37).

Real Postgres, no doubles: what matters is the row the lock hands the sweep, the
`escalated_at` it writes and the outbox row it writes with it (ADR-0016), so the
repository is the only implementation of its own port here — the same reason
`test_lifecycle.py` runs the lifecycle this way.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.orm import AlertRow, AlertRuleRow
from techcamp.alerts.adapters.repositories import SqlAlchemyAlertRepository
from techcamp.alerts.application import (
    acknowledge,
    escalate_due_alerts,
    open_alert,
    resolve_automatically,
    upgrade_to_critical,
)
from techcamp.alerts.application.ports import EscalationTarget
from techcamp.alerts.domain import ESCALATION_DELAY, Alert, AlertRule, Severity
from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import AppUserRow, MembershipRow, OrganizationRow
from techcamp.identity.adapters.repositories import SqlAlchemyMembershipRepository
from techcamp.identity.domain.models import Role
from techcamp.notifications.adapters.orm import NotificationRow
from techcamp.shared.config import database_url
from techcamp.shared.db import async_session_factory
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


async def _add_plot(session: AsyncSession, org: Org, name: str = "Lote 2") -> UUID:
    """A second plot of the same farm, so one test can hold two alert targets."""
    plot_id = uuid7()
    session.add(
        PlotRow(
            id=plot_id,
            org_id=org.org_id,
            farm_id=org.farm_id,
            name=name,
            boundary=_BOUNDARY,
            irrigation_system="drip",
        )
    )
    await session.commit()
    return plot_id


async def _open_on(
    session: AsyncSession,
    alerts: SqlAlchemyAlertRepository,
    org: Org,
    *,
    plot_id: UUID,
    code: str,
    at: datetime = _OPENED,
) -> Alert:
    """The same as `_open` but for a plot the test names itself."""
    return await open_alert(
        rule=await _rule(session, code),
        plot_id=plot_id,
        at=at,
        severity=Severity.CRITICAL,
        alerts=alerts,
    )


async def _open_node(
    session: AsyncSession,
    alerts: SqlAlchemyAlertRepository,
    org: Org,
    *,
    code: str = "node_offline",
    at: datetime = _OPENED,
) -> Alert:
    """A critical alert about the org's NODE, the other half of `alert`'s target.

    `node_offline` is seeded `warning` (docs/06 §3: node health warns the
    technician), so a critical node alert is the D20 severity override — the same
    one a model rule uses when the evidence decides the severity.
    """
    return await open_alert(
        rule=await _rule(session, code),
        node_id=org.node_id,
        at=at,
        severity=Severity.CRITICAL,
        alerts=alerts,
    )


class _OfferingOneRefusedRow(SqlAlchemyAlertRepository):
    """The lock keeps handing back a row the domain refuses (D42).

    The page filter and `is_eligible_for_escalation` state the same rule, so real
    rows cannot do this — which is exactly why the loop must be proved to make
    progress: a sweep that re-reads what it already refused never finishes, and
    this job runs every five minutes for the rest of the life of the product.
    """

    def __init__(self, session: AsyncSession, *, row: Alert, max_offers: int = 3) -> None:
        super().__init__(session)
        self._row = row
        self._max_offers = max_offers
        self.offers = 0

    async def lock_escalation_candidate(
        self, *, org_id: UUID, at: datetime, skip: frozenset[UUID] = frozenset()
    ) -> Alert | None:
        if self._row.id in skip:
            return None
        self.offers += 1
        if self.offers > self._max_offers:
            raise AssertionError("the sweep re-read a row it had already refused")
        return self._row


class _TargetUnresolvableForOnePlot(SqlAlchemyAlertRepository):
    """`get_escalation_target` fails for one plot, as it would for an alert whose
    target no longer resolves (D42).

    Unreachable with real rows — `alert.plot_id` is a `NO ACTION` foreign key, so
    the plot of an alert cannot be deleted — and reachable only as a defence. The
    cost of getting the defence wrong is one alert stopping its whole
    organization's escalations, which is what this seam makes testable.
    """

    def __init__(self, session: AsyncSession, *, plot_id: UUID) -> None:
        super().__init__(session)
        self._plot_id = plot_id

    async def get_escalation_target(
        self, *, org_id: UUID, plot_id: UUID | None, node_id: UUID | None
    ) -> EscalationTarget:
        if plot_id == self._plot_id:
            raise ValueError(f"Target is not a plot or node of org {org_id}")
        return await super().get_escalation_target(org_id=org_id, plot_id=plot_id, node_id=node_id)


async def test_the_sweep_steps_over_a_row_it_cannot_act_on_and_finishes(
    db_session: AsyncSession,
) -> None:
    """D42: the sweep must make progress. A row the domain refuses is never
    escalated, and it must not be handed back on the next read either — otherwise
    the 5-minute job spins on it forever and no later alert of the org is ever
    escalated."""
    org = await _make_org(db_session)
    real = SqlAlchemyAlertRepository(db_session)
    alert = await _open(db_session, real, org, code="water_stress")
    # The same row as the query found it, minus two hours: the query would have
    # offered it, the domain refuses it.
    refused = replace(alert, opened_at=alert.opened_at + timedelta(hours=2))
    alerts = _OfferingOneRefusedRow(db_session, row=refused)

    escalated = await escalate_due_alerts(org_id=org.org_id, at=_DUE, alerts=alerts)

    assert escalated == 0
    # Offered once and never again: the sweep's memory of the row is what ends the
    # round, not a second refusal of the same row.
    assert alerts.offers == 1
    row = (await db_session.execute(select(AlertRow).where(AlertRow.id == alert.id))).scalar_one()
    assert row.escalated_at is None
    assert await _rows(db_session, alert.id, "sms") == []


async def test_one_unresolvable_alert_does_not_stop_the_rest_of_its_org(
    db_session: AsyncSession,
) -> None:
    """D42: the alert whose target cannot be resolved is the OLDEST of the two, so
    it comes off the lock first — and the alert behind it must still escalate. One
    unreadable row is not an organization's escalation outage."""
    org = await _make_org(db_session)
    second_plot = await _add_plot(db_session, org)
    real = SqlAlchemyAlertRepository(db_session)
    # STRICTLY older, because the lock hands the OLDEST candidate first: opened at
    # the same instant, the order the lock returned them would be the primary key's
    # and the test would not be exercising the order it claims to prove.
    unreadable = await _open(
        db_session, real, org, code="water_stress", at=_OPENED - timedelta(minutes=1)
    )
    readable = await _open_on(
        db_session, real, org, plot_id=second_plot, code="heat_stress", at=_OPENED
    )
    alerts = _TargetUnresolvableForOnePlot(db_session, plot_id=org.plot_id)

    escalated = await escalate_due_alerts(org_id=org.org_id, at=_DUE, alerts=alerts)

    assert escalated == 1
    rows = {
        row.id: row.escalated_at for row in (await db_session.execute(select(AlertRow))).scalars()
    }
    assert rows[readable.id] == _DUE
    # The negative half: the unreadable one is left exactly as it was — NOT
    # escalated, because nobody could be told about it and `escalated_at` would
    # close the question for good. It is retried on the next round instead.
    assert rows[unreadable.id] is None
    assert [row.user_id for row in await _rows(db_session, readable.id, "sms")] == [org.technician]
    assert await _rows(db_session, unreadable.id, "sms") == []


async def test_a_critical_node_alert_escalates_to_the_farms_technician(
    db_session: AsyncSession,
) -> None:
    """R3-node-branch-untested: `get_escalation_target` has TWO statements, one
    per target kind, and only the plot one was covered. A `node_offline` critical
    reaches the technician through the plot its node hangs on (docs/06 §3
    "Salud del nodo": node alerts go to the technician, not the producer)."""
    org = await _make_org(db_session)
    alerts = SqlAlchemyAlertRepository(db_session)
    alert = await _open_node(db_session, alerts, org)

    escalated = await escalate_due_alerts(org_id=org.org_id, at=_DUE, alerts=alerts)

    assert escalated == 1
    row = (await db_session.execute(select(AlertRow).where(AlertRow.id == alert.id))).scalar_one()
    assert (row.plot_id, row.node_id, row.escalated_at) == (None, org.node_id, _DUE)
    sms = await _rows(db_session, alert.id, "sms")
    assert [row.user_id for row in sms] == [org.technician]
    # The negative half: a node alert's push already went to the technician
    # (D4), so the escalation must not reach the producer or the viewer, and it
    # must not add a second sms of its own.
    assert {row.user_id for row in await _rows(db_session, alert.id, "push")} == {org.technician}
    assert len(sms) == 1


async def test_an_escalation_target_naming_both_a_plot_and_a_node_is_refused(
    db_session: AsyncSession,
) -> None:
    """R3-both-targets-accepted: the contract is "a plot or a node, never neither or
    both". With both given the plot used to win silently and the node was ignored."""
    org = await _make_org(db_session)
    alerts = SqlAlchemyAlertRepository(db_session)

    with pytest.raises(ValueError, match="never neither or both"):
        await alerts.get_escalation_target(
            org_id=org.org_id, plot_id=org.plot_id, node_id=org.node_id
        )


async def test_a_node_alert_of_one_org_never_texts_another_orgs_technician(
    db_session: AsyncSession,
) -> None:
    """R3-node-branch-untested, the isolation half: the node statement joins node to
    plot to farm, and it is the org filter on that join that keeps one
    organization's node alert away from another organization's technician
    (docs/09)."""
    org = await _make_org(db_session)
    other = await _make_org(db_session)
    alerts = SqlAlchemyAlertRepository(db_session)
    mine = await _open_node(db_session, alerts, org)
    theirs = await _open_node(db_session, alerts, other)

    await escalate_due_alerts(org_id=org.org_id, at=_DUE, alerts=alerts)
    await escalate_due_alerts(org_id=other.org_id, at=_DUE, alerts=alerts)

    assert [row.user_id for row in await _rows(db_session, mine.id, "sms")] == [org.technician]
    assert [row.user_id for row in await _rows(db_session, theirs.id, "sms")] == [other.technician]


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


# -- what the escalation writes (docs/06 §3, §4; D4, D5) --


async def test_a_due_critical_alert_escalates_and_texts_the_farms_technician(
    db_session: AsyncSession,
) -> None:
    """docs/06 §3: "Escalar una alerta crítica notifica por SMS o WhatsApp al
    técnico asignado a la finca (`farm.technician_id`)"; docs/06 §4's row goes
    through the outbox, never inline (ADR-0016)."""
    org = await _make_org(db_session)
    alerts = SqlAlchemyAlertRepository(db_session)
    alert = await _open(db_session, alerts, org, code="water_stress")

    escalated = await escalate_due_alerts(org_id=org.org_id, at=_DUE, alerts=alerts)

    assert escalated == 1
    row = (await db_session.execute(select(AlertRow).where(AlertRow.id == alert.id))).scalar_one()
    assert (row.state, row.severity, row.escalated_at) == ("open", "critical", _DUE)
    # The alert itself does not become a new state (D3): `escalated_at` is the
    # whole of the escalation.
    sms = await _rows(db_session, alert.id, "sms")
    assert len(sms) == 1
    assert (sms[0].user_id, sms[0].status) == (org.technician, "pending")
    # A critical is due now even at night (docs/06 §4 "Horas de silencio": only
    # criticals break the silence), and the row is the technician's alone: the
    # producer, the owner and the viewer are not texted.
    assert sms[0].next_attempt_at == _DUE
    assert {row.user_id for row in sms} == {org.technician}
    assert {row.user_id for row in await _rows(db_session, alert.id, "push")} == {
        org.owner,
        org.producer,
    }


async def test_the_escalation_falls_back_to_the_owners_when_the_farm_has_no_technician(
    db_session: AsyncSession,
) -> None:
    """D4: "with no technician assigned they fall back to the org owners". A member
    who merely holds the `technician` ROLE is not the farm's technician, and a
    `viewer` never receives a notification."""
    org = await _make_org(db_session, with_technician=False)
    alerts = SqlAlchemyAlertRepository(db_session)
    alert = await _open(db_session, alerts, org, code="water_stress")

    await escalate_due_alerts(org_id=org.org_id, at=_DUE, alerts=alerts)

    assert {row.user_id for row in await _rows(db_session, alert.id, "sms")} == {org.owner}


async def test_a_critical_with_nobody_to_text_still_escalates(
    db_session: AsyncSession,
) -> None:
    """The third state the brief's checklist names: no technician AND no owner to
    fall back to is not "nobody is notified because nothing happened". The alert
    escalates — the clock is the alert's own — and the gap is left visible in the
    log instead of a silent row nobody can see."""
    org = await _make_org(db_session, with_technician=False, with_owner=False)
    alerts = SqlAlchemyAlertRepository(db_session)
    alert = await _open(db_session, alerts, org, code="water_stress")

    escalated = await escalate_due_alerts(org_id=org.org_id, at=_DUE, alerts=alerts)

    assert escalated == 1
    row = (await db_session.execute(select(AlertRow).where(AlertRow.id == alert.id))).scalar_one()
    assert row.escalated_at == _DUE
    assert await _rows(db_session, alert.id, "sms") == []


async def test_a_late_critical_upgrade_escalates_at_the_next_check(
    db_session: AsyncSession,
) -> None:
    """docs/06 §3 "Reloj de escalamiento": "una alerta ascendida a crítica tras 48 h
    escala en su siguiente revisión" (D12). The clock runs from `opened_at`, not
    from the upgrade, so a critical that was a warning for two days is due at once
    — and it escalates on the next sweep, without a second evaluation of the rule.
    """
    org = await _make_org(db_session)
    alerts = SqlAlchemyAlertRepository(db_session)
    alert = await _open(db_session, alerts, org, code="water_stress", severity=Severity.WARNING)
    upgraded_at = _OPENED + timedelta(hours=48)
    # The negative half first: a warning is not an escalation candidate at any age,
    # so two days of sweeps leave it alone.
    assert await escalate_due_alerts(org_id=org.org_id, at=upgraded_at, alerts=alerts) == 0
    assert await _rows(db_session, alert.id, "sms") == []

    await upgrade_to_critical(alert_id=alert.id, org_id=org.org_id, at=upgraded_at, alerts=alerts)
    # The clock runs from `opened_at`, not from the upgrade: the very next sweep
    # finds it already 2 h old, with no second evaluation of the rule in between.
    escalated = await escalate_due_alerts(
        org_id=org.org_id, at=upgraded_at + timedelta(minutes=1), alerts=alerts
    )

    assert escalated == 1
    row = (await db_session.execute(select(AlertRow).where(AlertRow.id == alert.id))).scalar_one()
    assert row.escalated_at == upgraded_at + timedelta(minutes=1)
    assert [row.user_id for row in await _rows(db_session, alert.id, "sms")] == [org.technician]


async def test_a_sweep_escalates_each_alert_once_and_nothing_else(
    db_session: AsyncSession,
) -> None:
    """The negative half of the whole job, and the idempotency of one worker: a
    warning, an acknowledged, a resolved and a too-young critical are left exactly
    as they were, and a second sweep writes no second `sms` row."""
    org = await _make_org(db_session)
    alerts = SqlAlchemyAlertRepository(db_session)
    memberships = SqlAlchemyMembershipRepository(db_session)
    due = await _open(db_session, alerts, org, code="water_stress")
    warning = await _open(db_session, alerts, org, code="heat_stress", severity=Severity.WARNING)
    young = await _open(
        db_session, alerts, org, code="waterlogging", at=_DUE - timedelta(minutes=1)
    )
    acknowledged = await _open(db_session, alerts, org, code="fungal_risk")
    await acknowledge(
        user_id=org.owner,
        alert_id=acknowledged.id,
        at=_OPENED + timedelta(minutes=1),
        alerts=alerts,
        memberships=memberships,
    )
    resolved = await _open(db_session, alerts, org, code="node_offline")
    await resolve_automatically(
        alert_id=resolved.id,
        org_id=org.org_id,
        farm_id=org.farm_id,
        at=_OPENED + timedelta(minutes=2),
        alerts=alerts,
    )
    untouched = {warning.id, young.id, acknowledged.id, resolved.id}

    assert await escalate_due_alerts(org_id=org.org_id, at=_DUE, alerts=alerts) == 1
    # A second run of the same sweep: the escalated alert is no longer a candidate
    # (`escalated_at` is set), so nothing happens and nobody is texted twice.
    assert await escalate_due_alerts(org_id=org.org_id, at=_DUE, alerts=alerts) == 0

    rows = {
        row.id: row.escalated_at for row in (await db_session.execute(select(AlertRow))).scalars()
    }
    assert rows[due.id] == _DUE
    assert {alert_id for alert_id, at in rows.items() if alert_id in untouched and at is None} == (
        untouched
    )
    assert len(await _rows(db_session, due.id, "sms")) == 1
    for alert_id in untouched:
        assert await _rows(db_session, alert_id, "sms") == []


async def test_the_escalation_of_one_org_never_texts_another_orgs_technician(
    db_session: AsyncSession,
) -> None:
    """docs/09, the other half of org isolation: the alert is org A's, and the SMS
    must not reach org B's technician even while org B's own sweep runs."""
    org = await _make_org(db_session)
    other = await _make_org(db_session)
    alerts = SqlAlchemyAlertRepository(db_session)
    mine = await _open(db_session, alerts, org, code="water_stress")
    theirs = await _open(db_session, alerts, other, code="water_stress")

    await escalate_due_alerts(org_id=org.org_id, at=_DUE, alerts=alerts)
    await escalate_due_alerts(org_id=other.org_id, at=_DUE, alerts=alerts)

    assert [row.user_id for row in await _rows(db_session, mine.id, "sms")] == [org.technician]
    assert [row.user_id for row in await _rows(db_session, theirs.id, "sms")] == [other.technician]


async def test_the_escalation_reaches_plot_events_as_an_alert_updated(
    db_session: AsyncSession,
) -> None:
    """ADR-0015: the escalation is a change of the alert, so the SSE stream of the
    farm sees `alert.updated` — the tray of docs/06 §3 is not stale for two hours."""
    org = await _make_org(db_session)
    alerts = SqlAlchemyAlertRepository(db_session)
    payloads: list[str] = []

    def _on_notify(_conn: object, _pid: int, _channel: str, payload: str) -> None:
        payloads.append(payload)

    listener = await asyncpg.connect(dsn=_dsn())
    await listener.add_listener("plot_events", _on_notify)
    try:
        await _open(db_session, alerts, org, code="water_stress")
        await asyncio.sleep(0.2)
        payloads.clear()
        await escalate_due_alerts(org_id=org.org_id, at=_DUE, alerts=alerts)
        await asyncio.sleep(0.2)  # let the listener connection process the NOTIFY
    finally:
        await listener.close()

    assert len(payloads) == 1
    event = json.loads(payloads[0])
    assert (event["type"], event["farm_id"], event["state"], event["severity"]) == (
        "alert.updated",
        str(org.farm_id),
        "open",
        "critical",
    )


# -- two workers racing on one alert (D42) --


async def test_two_concurrent_sweeps_escalate_the_alert_once_and_text_the_technician_once(
    db_session: AsyncSession,
) -> None:
    """The invariant the lock exists for: two workers, two sessions, one alert past
    its 2 h — it escalates ONCE and its technician gets exactly ONE `sms` row.

    Without the `FOR UPDATE SKIP LOCKED` re-read both workers would see the same
    un-escalated row, and `save`'s CAS would not stop either: it guards `state`
    and `severity`, which an escalation does not change, so both would land and
    write a second SMS. This is the one test that would have caught that, and it
    is why the sweep takes the row's own lock instead of trusting the read.

    Each sweep runs on its own session, so the two really contend in the database
    instead of sharing a unit of work that would serialize them by accident.

    `db_session` is requested for its TRUNCATE teardown ONLY, and nothing here is
    written on it: without it this test's org, plot and alert would survive until
    some LATER test's teardown, and `test_the_escalation_sweep_defers_one_job_per_org_
    with_its_own_lock` asserts the exact set of organizations a sweep defers — so
    the leak would make that test fail or pass on the order the suite happens to
    run in (R3-concurrency-test-commits-outside-fixture).
    """
    async with async_session_factory() as setup:
        org = await _make_org(setup)
        alert = await _open(setup, SqlAlchemyAlertRepository(setup), org, code="water_stress")

    async def _sweep() -> int:
        async with async_session_factory() as session:
            return await escalate_due_alerts(
                org_id=org.org_id, at=_DUE, alerts=SqlAlchemyAlertRepository(session)
            )

    async with asyncio.timeout(30), async_session_factory() as check:
        first, second = await asyncio.gather(_sweep(), _sweep())
        escalated_at = (
            await check.execute(select(AlertRow.escalated_at).where(AlertRow.id == alert.id))
        ).scalar_one()
        stored = await _rows(check, alert.id, "sms")

    # Exactly one of the two workers did the work. Which one is the database's
    # answer, not this test's: the other found the row locked and stepped over it.
    assert sorted([first, second]) == [0, 1]
    assert escalated_at == _DUE
    assert len(stored) == 1
    assert (stored[0].user_id, stored[0].status) == (org.technician, "pending")
