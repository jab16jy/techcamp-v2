"""Postgres repository for alerts and their outbox rows (docs/06-diseno-detallado.md §3, §4).

The only place with SQL for alerts. Every read filters by `org_id`
(docs/09-cuellos-de-botella.md#seguridad), and `insert`/`save` write the alert,
the `notification` rows (through the notifications adapter on the same session)
and the `NOTIFY plot_events` in one transaction (ADR-0016). Plot, farm, node and
membership rows are read here on that same session because one alert's write
spans them (D14, the `farms` → `weather` precedent of E5 T2).
"""

from __future__ import annotations

import decimal
import json
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, Row, case, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from techcamp.alerts.adapters.orm import AlertRow, AlertRuleRow
from techcamp.alerts.application.ports import AlertTarget, EscalationTarget
from techcamp.alerts.domain.errors import InvalidAlertRuleError
from techcamp.alerts.domain.models import (
    ESCALATION_DELAY,
    Alert,
    AlertRule,
    AlertRuleChanges,
    AlertState,
    InvalidAlertTransitionError,
    Severity,
)
from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import MembershipRow
from techcamp.identity.domain.models import Role
from techcamp.notifications.adapters.orm import NotificationRow
from techcamp.notifications.adapters.repositories import SqlAlchemyNotificationRepository
from techcamp.notifications.application import NotificationDraft
from techcamp.telemetry.adapters.orm import NodeRow

_ALERT_COLUMNS = (
    AlertRow.id,
    AlertRow.org_id,
    AlertRow.rule_id,
    AlertRow.plot_id,
    AlertRow.node_id,
    AlertRow.state,
    AlertRow.severity,
    AlertRow.evidence,
    AlertRow.opened_at,
    AlertRow.acknowledged_at,
    AlertRow.resolved_at,
    AlertRow.escalated_at,
    AlertRow.resolution_note,
    AlertRow.outcome,
)

_RULE_CODE = AlertRuleRow.code.label("rule_code")
"""The rule's code travels with the alert: the `NOTIFY` payload carries it and
nothing else joins on it (ADR-0015: ids and minimal data)."""

_PUSH_RECIPIENT_ROLES = (Role.OWNER.value, Role.PRODUCER.value)
"""D4: plot alerts notify the org's owners and producers; `viewer` never."""

_GROUP_WINDOW = timedelta(minutes=15)
"""D6: a non-critical row groups with a pending one of the last 15 min."""


def _alert_from_row(row: Row[Any]) -> Alert:
    return Alert(
        id=row.id,
        org_id=row.org_id,
        rule_id=row.rule_id,
        rule_code=row.rule_code,
        plot_id=row.plot_id,
        node_id=row.node_id,
        state=AlertState(row.state),
        severity=Severity(row.severity),
        evidence=dict(row.evidence or {}),
        opened_at=row.opened_at,
        acknowledged_at=row.acknowledged_at,
        resolved_at=row.resolved_at,
        escalated_at=row.escalated_at,
        resolution_note=row.resolution_note,
        outcome=row.outcome,
    )


class SqlAlchemyAlertRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._notifications = SqlAlchemyNotificationRepository(session)

    async def get_non_resolved_for_target(
        self, *, rule_id: UUID, org_id: UUID, plot_id: UUID | None, node_id: UUID | None
    ) -> Alert | None:
        stmt = (
            select(*_ALERT_COLUMNS, _RULE_CODE)
            .join(AlertRuleRow, AlertRuleRow.id == AlertRow.rule_id)
            .where(
                AlertRow.rule_id == rule_id,
                AlertRow.org_id == org_id,
                AlertRow.state != AlertState.RESOLVED.value,
            )
        )
        if plot_id is not None:
            stmt = stmt.where(AlertRow.plot_id == plot_id, AlertRow.node_id.is_(None))
        else:
            stmt = stmt.where(AlertRow.node_id == node_id, AlertRow.plot_id.is_(None))
        result = await self._session.execute(stmt)
        row = result.one_or_none()
        return _alert_from_row(row) if row is not None else None

    async def get_for_orgs(self, alert_id: UUID, org_ids: Sequence[UUID]) -> Alert | None:
        if not org_ids:
            return None
        stmt = (
            select(*_ALERT_COLUMNS, _RULE_CODE)
            .join(AlertRuleRow, AlertRuleRow.id == AlertRow.rule_id)
            .where(AlertRow.id == alert_id, AlertRow.org_id.in_(org_ids))
            .limit(1)
        )
        result = await self._session.execute(stmt)
        row = result.one_or_none()
        return _alert_from_row(row) if row is not None else None

    async def list_for_orgs(
        self,
        org_ids: Sequence[UUID],
        *,
        plot_id: UUID | None = None,
        state: AlertState | None = None,
        limit: int = 50,
        cursor: UUID | None = None,
    ) -> list[Alert]:
        if not org_ids:
            return []
        stmt = (
            select(*_ALERT_COLUMNS, _RULE_CODE)
            .join(AlertRuleRow, AlertRuleRow.id == AlertRow.rule_id)
            .where(AlertRow.org_id.in_(org_ids))
        )
        if plot_id is not None:
            stmt = stmt.where(AlertRow.plot_id == plot_id)
        if state is not None:
            stmt = stmt.where(AlertRow.state == state.value)
        if cursor is not None:
            stmt = stmt.where(AlertRow.id < cursor)
        result = await self._session.execute(stmt.order_by(AlertRow.id.desc()).limit(limit))
        return [_alert_from_row(row) for row in result]

    async def list_open_for_plots(
        self,
        plot_ids: Sequence[UUID],
        org_ids: Sequence[UUID],
    ) -> list[Alert]:
        if not plot_ids or not org_ids:
            return []
        stmt = (
            select(*_ALERT_COLUMNS, _RULE_CODE)
            .join(AlertRuleRow, AlertRuleRow.id == AlertRow.rule_id)
            .where(
                AlertRow.plot_id.in_(plot_ids),
                AlertRow.org_id.in_(org_ids),
                AlertRow.state != AlertState.RESOLVED.value,
            )
            .order_by(
                case((AlertRow.severity == Severity.CRITICAL.value, 0), else_=1),
                AlertRow.opened_at.desc(),
                AlertRow.id.desc(),
            )
        )
        result = await self._session.execute(stmt)
        return [_alert_from_row(row) for row in result]

    async def get_target_context(
        self, *, plot_id: UUID | None, node_id: UUID | None
    ) -> AlertTarget:
        """The org and farm of the target, and who to notify about it (D4, D6)."""
        if plot_id is not None:
            result = await self._session.execute(
                select(PlotRow.org_id, PlotRow.farm_id).where(PlotRow.id == plot_id)
            )
            row = result.one_or_none()
            if row is None:
                raise ValueError(f"Plot {plot_id} not found")
            org_id, farm_id = row
            recipients = await self._members(org_id, _PUSH_RECIPIENT_ROLES)
        else:
            if node_id is None:
                raise ValueError("An alert target is a plot or a node, never neither")
            result = await self._session.execute(
                select(NodeRow.org_id, PlotRow.farm_id, FarmRow.technician_id)
                .join(PlotRow, NodeRow.plot_id == PlotRow.id)
                .join(FarmRow, PlotRow.farm_id == FarmRow.id)
                .where(NodeRow.id == node_id)
            )
            row = result.one_or_none()
            if row is None:
                raise ValueError(f"Node {node_id} not found or not claimed to a plot")
            org_id, farm_id, technician_id = row
            if org_id is None:
                raise ValueError(f"Node {node_id} is unclaimed, so it belongs to no organization")
            # docs/06 §3: node alerts go to the technician; with none assigned
            # the org's owners are who can act on it.
            recipients = (
                (technician_id,)
                if technician_id is not None
                else await self._members(org_id, (Role.OWNER.value,))
            )
        return AlertTarget(
            org_id=org_id,
            farm_id=farm_id,
            recipients=recipients,
            group_times=await self._group_times(farm_id),
        )

    async def get_escalation_target(
        self, *, org_id: UUID, plot_id: UUID | None, node_id: UUID | None
    ) -> EscalationTarget:
        """The farm of the target and the technician to text (docs/06 §3; D4).

        The recipient rule does not care which of the two target kinds the alert
        is about — an escalation is about a FARM — but the two statements are the
        ones `get_target_context` and `_assert_alert_farm` already write, and the
        `org_id` in each `where` is the isolation (docs/09): a target of another
        organization resolves to nothing, so its technician can never be reached.
        """
        if plot_id is not None and node_id is not None:
            raise ValueError("An alert target is a plot or a node, never neither or both")
        if plot_id is not None:
            stmt = (
                select(PlotRow.farm_id, FarmRow.technician_id)
                .join(FarmRow, PlotRow.farm_id == FarmRow.id)
                .where(PlotRow.id == plot_id, PlotRow.org_id == org_id)
            )
        elif node_id is not None:
            stmt = (
                select(PlotRow.farm_id, FarmRow.technician_id)
                .join(NodeRow, NodeRow.plot_id == PlotRow.id)
                .join(FarmRow, PlotRow.farm_id == FarmRow.id)
                .where(NodeRow.id == node_id, PlotRow.org_id == org_id)
            )
        else:
            raise ValueError("An alert target is a plot or a node, never neither or both")
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            raise ValueError(f"Target is not a plot or node of org {org_id}")
        farm_id, technician_id = row
        # docs/06 §3: an escalation goes to the technician; with none assigned,
        # the org's owners are who can act on it. `viewer` is not among them.
        recipients = (
            (technician_id,)
            if technician_id is not None
            else await self._members(org_id, (Role.OWNER.value,))
        )
        return EscalationTarget(farm_id=farm_id, recipients=recipients)

    async def lock_escalation_candidate(
        self, *, org_id: UUID, at: datetime, skip: frozenset[UUID] = frozenset()
    ) -> Alert | None:
        """One due critical of this org, oldest first, held for the decision.

        ONE row and not a page: `save` commits per alert, and that commit is what
        releases the lock, so a claimed page would leave its later rows unlocked
        again for a second worker to escalate a second time. One alert per
        transaction is the unit `save` already is, and the sweep simply calls
        this until it returns nothing.

        The narrowing here (critical, open, unescalated, 2 h from `opened_at`) is
        the sweep's page filter, not the decision: `is_eligible_for_escalation`
        decides on the value read under this lock, and an alert that fails it
        escalates nothing. `skip` is how a row that failed it stops coming back
        (D42); the set holds the round's own un-actionable rows, which is what
        makes the sweep terminate.
        """
        stmt = (
            select(*_ALERT_COLUMNS, _RULE_CODE)
            .join(AlertRuleRow, AlertRuleRow.id == AlertRow.rule_id)
            .where(
                AlertRow.org_id == org_id,
                AlertRow.state == AlertState.OPEN.value,
                AlertRow.severity == Severity.CRITICAL.value,
                AlertRow.escalated_at.is_(None),
                AlertRow.opened_at <= at - ESCALATION_DELAY,
            )
            .order_by(AlertRow.opened_at, AlertRow.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if skip:
            stmt = stmt.where(AlertRow.id.not_in(skip))
        row = (await self._session.execute(stmt)).one_or_none()
        return _alert_from_row(row) if row is not None else None

    async def insert(
        self, alert: Alert, drafts: Sequence[NotificationDraft], target: AlertTarget
    ) -> Alert:
        """The alert, its outbox rows and `alert.opened` in one transaction."""
        self._session.add(_to_row(alert))
        try:
            await self._session.flush()
        except IntegrityError:
            # The partial unique index says a non-resolved alert for (rule,
            # target) already exists: another writer opened it first, so its
            # notices are the ones that go out (docs/06 §3).
            await self._session.rollback()
            existing = await self.get_non_resolved_for_target(
                rule_id=alert.rule_id,
                org_id=alert.org_id,
                plot_id=alert.plot_id,
                node_id=alert.node_id,
            )
            if existing is None:
                raise
            return existing
        await self._write(alert, drafts, "alert.opened", target.farm_id)
        return alert

    async def save(
        self,
        alert: Alert,
        drafts: Sequence[NotificationDraft],
        farm_id: UUID,
        *,
        expected_state: AlertState,
        expected_severity: Severity,
    ) -> Alert:
        """The transitioned alert, any new outbox rows and `alert.updated` in one
        transaction.

        `expected_state` and `expected_severity` are what the caller validated
        its transition against, and the update only lands on both: a transition
        computed on a snapshot another writer has already moved on is refused
        instead of overwriting the newer row. Severity is part of the guard
        because a transition may leave the state alone (the 48 h upgrade) and
        would otherwise be applied twice.
        """
        await self._assert_alert_farm(alert, farm_id)
        result = await self._session.execute(
            update(AlertRow)
            .where(
                AlertRow.id == alert.id,
                AlertRow.org_id == alert.org_id,
                AlertRow.state == expected_state.value,
                AlertRow.severity == expected_severity.value,
            )
            .values(
                state=alert.state.value,
                severity=alert.severity.value,
                acknowledged_at=alert.acknowledged_at,
                resolved_at=alert.resolved_at,
                escalated_at=alert.escalated_at,
                resolution_note=alert.resolution_note,
                outcome=alert.outcome,
            )
        )
        if cast(CursorResult[Any], result).rowcount == 0:
            raise InvalidAlertTransitionError(
                f"Alert {alert.id} is no longer {expected_state}/{expected_severity}, "
                "so the transition is stale"
            )
        await self._write(alert, drafts, "alert.updated", farm_id)
        return alert

    async def _assert_alert_farm(self, alert: Alert, farm_id: UUID) -> None:
        """The `farm_id` a caller states must be the alert's own farm.

        `plot_events` is one stream per farm and the alert's ids ride it
        (ADR-0015), so publishing to another farm's stream would hand one
        organization's alert to another's subscribers.
        """
        if alert.plot_id is not None:
            stmt = select(PlotRow.farm_id).where(PlotRow.id == alert.plot_id)
        else:
            stmt = (
                select(PlotRow.farm_id)
                .join(NodeRow, NodeRow.plot_id == PlotRow.id)
                .where(NodeRow.id == alert.node_id)
            )
        if (await self._session.execute(stmt)).scalar_one() != farm_id:
            raise ValueError(f"Farm {farm_id} does not carry alert {alert.id}")

    async def _write(
        self, alert: Alert, drafts: Sequence[NotificationDraft], kind: str, farm_id: UUID
    ) -> None:
        """The outbox rows, the `NOTIFY` and the commit of one write, or none of
        them: a failure never leaves an alert without its notice (ADR-0016)."""
        try:
            await self._notifications.insert_drafts(alert.id, drafts)
            await self._publish(kind, alert, farm_id)
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise

    async def _members(self, org_id: UUID, roles: tuple[str, ...]) -> tuple[UUID, ...]:
        result = await self._session.execute(
            select(MembershipRow.user_id).where(
                MembershipRow.org_id == org_id, MembershipRow.role.in_(roles)
            )
        )
        return tuple(result.scalars())

    async def _group_times(self, farm_id: UUID) -> dict[UUID, datetime]:
        """The due time of each recipient's newest pending push of this farm.

        A `push` row is a warning's or a critical's row, so a pending push of the
        farm is the non-critical group a new row may join (D6); the escalated
        `sms` row (T8) is not one. The newest pending row per user is the group
        the new row extends.
        """
        node_plot = aliased(PlotRow)
        result = await self._session.execute(
            select(NotificationRow.user_id, NotificationRow.next_attempt_at)
            .join(AlertRow, AlertRow.id == NotificationRow.alert_id)
            .outerjoin(PlotRow, PlotRow.id == AlertRow.plot_id)
            .outerjoin(NodeRow, NodeRow.id == AlertRow.node_id)
            .outerjoin(node_plot, node_plot.id == NodeRow.plot_id)
            .where(
                func.coalesce(PlotRow.farm_id, node_plot.farm_id) == farm_id,
                NotificationRow.channel == "push",
                NotificationRow.status == "pending",
                NotificationRow.created_at >= func.now() - _GROUP_WINDOW,
            )
            .distinct(NotificationRow.user_id)
            .order_by(NotificationRow.user_id, NotificationRow.created_at.desc())
        )
        return {user_id: next_attempt_at for user_id, next_attempt_at in result}

    async def _publish(self, kind: str, alert: Alert, farm_id: UUID) -> None:
        """`NOTIFY plot_events` with ids and minimal data (ADR-0015: ≤ 8 KB).

        `farm_id` is what the SSE hub routes on and it is not on the alert, so
        the caller states it (it resolved the target, or owns the stream).
        """
        payload = json.dumps(
            {
                "type": kind,
                "org_id": str(alert.org_id),
                "farm_id": str(farm_id),
                "id": str(alert.id),
                "rule_code": alert.rule_code,
                "plot_id": str(alert.plot_id) if alert.plot_id is not None else None,
                "node_id": str(alert.node_id) if alert.node_id is not None else None,
                "state": alert.state.value,
                "severity": alert.severity.value,
                "opened_at": alert.opened_at.isoformat(),
            }
        )
        await self._session.execute(
            text("SELECT pg_notify('plot_events', :payload)"), {"payload": payload}
        )


def _to_row(alert: Alert) -> AlertRow:
    return AlertRow(
        id=alert.id,
        org_id=alert.org_id,
        rule_id=alert.rule_id,
        plot_id=alert.plot_id,
        node_id=alert.node_id,
        state=alert.state.value,
        severity=alert.severity.value,
        evidence=alert.evidence,
        opened_at=alert.opened_at,
        acknowledged_at=alert.acknowledged_at,
        resolved_at=alert.resolved_at,
        escalated_at=alert.escalated_at,
        resolution_note=alert.resolution_note,
        outcome=alert.outcome,
    )


def _rule_from_row(row: AlertRuleRow) -> AlertRule:
    """The `Numeric` thresholds of `alert_rule` become the floats the domain compares."""
    return AlertRule(
        id=row.id,
        org_id=row.org_id,
        code=row.code,
        metric=row.metric,
        operator=row.operator,
        threshold=float(row.threshold) if row.threshold is not None else None,
        hysteresis=float(row.hysteresis),
        min_duration=timedelta(minutes=row.min_duration_min),
        severity=Severity(row.severity),
        crop_id=row.crop_id,
    )


def _numeric(value: float | None) -> decimal.Decimal | None:
    """`Decimal(str(value))`, never `Decimal(value)`: the float's binary
    expansion would write 34.500000000000000444089209850062616169452667236328125
    into a `Numeric`."""
    return None if value is None else decimal.Decimal(str(value))


class SqlAlchemyAlertRuleRepository:
    """CRUD of `alert_rule` (docs/03:272-283; D11).

    Separate from `SqlAlchemyAlertRepository` because a rule write is a single
    row in its own transaction: it never opens an alert, an outbox row or a
    `NOTIFY` (the lifecycle repository owns those).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_org(self, org_id: UUID) -> list[AlertRule]:
        """The factory rules (`org_id is null`, every org reads them) plus this
        org's own, by code."""
        result = await self._session.execute(
            select(AlertRuleRow)
            .where(or_(AlertRuleRow.org_id.is_(None), AlertRuleRow.org_id == org_id))
            .order_by(AlertRuleRow.code)
        )
        return [_rule_from_row(row) for row in result.scalars()]

    async def get_for_orgs(self, rule_id: UUID, org_ids: Sequence[UUID]) -> AlertRule | None:
        """A rule of one of `org_ids`; a factory rule is never one (D11)."""
        if not org_ids:
            return None
        result = await self._session.execute(
            select(AlertRuleRow).where(AlertRuleRow.id == rule_id, AlertRuleRow.org_id.in_(org_ids))
        )
        row = result.scalar_one_or_none()
        return _rule_from_row(row) if row is not None else None

    async def create(self, rule: AlertRule) -> AlertRule:
        self._session.add(
            AlertRuleRow(
                id=rule.id,
                org_id=rule.org_id,
                code=rule.code,
                metric=rule.metric,
                operator=rule.operator,
                threshold=_numeric(rule.threshold),
                hysteresis=_numeric(rule.hysteresis) or decimal.Decimal(0),
                min_duration_min=int(rule.min_duration.total_seconds() // 60),
                severity=rule.severity.value,
                crop_id=rule.crop_id,
            )
        )
        try:
            await self._session.commit()
        except IntegrityError as exc:
            # A `crop_id` (or `org_id`) that doesn't exist is a client error, not
            # a 500: the membership already proved the org, so the reference
            # data is what failed. The database message is not echoed back.
            await self._session.rollback()
            raise InvalidAlertRuleError(
                f"Rule {rule.id} references data that does not exist"
            ) from exc
        return rule

    async def update(self, rule_id: UUID, org_id: UUID, changes: AlertRuleChanges) -> AlertRule:
        """Write only the columns `changes` states (R3-001), then re-read."""
        values: dict[str, Any] = {}
        where = (AlertRuleRow.id == rule_id, AlertRuleRow.org_id == org_id)
        if changes.threshold is not None:
            values["threshold"] = _numeric(changes.threshold)
        if changes.hysteresis is not None:
            values["hysteresis"] = _numeric(changes.hysteresis)
        if changes.min_duration is not None:
            values["min_duration_min"] = int(changes.min_duration.total_seconds() // 60)
        if changes.severity is not None:
            values["severity"] = changes.severity.value
        if values:
            await self._session.execute(update(AlertRuleRow).where(*where).values(**values))
            await self._session.commit()
        result = await self._session.execute(select(AlertRuleRow).where(*where))
        return _rule_from_row(result.scalar_one())
