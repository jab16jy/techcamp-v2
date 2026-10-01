"""Alert lifecycle use cases (docs/06-diseno-detallado.md §3, §4; ADR-0016; D3–D6).

Each one is the T2 transition plus the outbox planning; the repository owns the
single transaction, the notification rows and the `NOTIFY`. The evaluator opens,
upgrades and resolves with what it evaluated (`rule` plus the plot or node it
read); a user acts on an alert of one of their orgs. `farm_id` is the SSE
stream the change is published to (ADR-0015), so every evaluator states it; a
user action takes the alert's own farm instead (D15).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from techcamp.alerts.application.ports import AlertRepository, AlertTarget
from techcamp.alerts.domain.errors import AlertNotFoundError
from techcamp.alerts.domain.models import (
    Alert,
    AlertRule,
    AlertState,
    InvalidAlertTransitionError,
    Severity,
    ensure_can_manage_alert,
)
from techcamp.identity.application.ports import MembershipRepository
from techcamp.identity.application.resolve_org_access import resolve_org_membership
from techcamp.notifications.application import NotificationDraft, plan_notifications
from techcamp.shared.ids import uuid7


def _drafts(alert: Alert, target: AlertTarget, at: datetime) -> list[NotificationDraft]:
    """D5: `info` is in-app only, so it plans no row; the rest get one push."""
    if alert.severity is Severity.INFO:
        return []
    return plan_notifications(
        target.recipients,
        critical=alert.severity is Severity.CRITICAL,
        now=at,
        group_times=target.group_times,
    )


async def open_alert(
    *,
    rule: AlertRule,
    at: datetime,
    alerts: AlertRepository,
    plot_id: UUID | None = None,
    node_id: UUID | None = None,
    evidence: dict[str, Any] | None = None,
    severity: Severity | None = None,
) -> Alert:
    """Open an alert for one evaluated (rule, target), idempotently.

    docs/06 §3: one non-resolved alert per (`rule_id`, `plot_id`/`node_id`). An
    alert already open for that pair is returned untouched, so a second batch
    that still violates the rule sends no second notice; the partial unique
    index is the backstop for the race between two writers.

    `severity` overrides the rule's own for an evaluator that decides it from
    the evidence instead of from the row (D20: `heavy_rain_forecast` is critical
    when the plot's soil is saturated, and it is decided HERE so the alert opens
    once at the right severity with one notification, instead of opening
    `warning` and taking the `upgrade_to_critical` path afterwards).
    """
    if (plot_id is None) == (node_id is None):
        raise ValueError("An alert targets exactly one of plot_id or node_id")
    target = await alerts.get_target_context(plot_id=plot_id, node_id=node_id)
    existing = await alerts.get_non_resolved_for_target(
        rule_id=rule.id, org_id=target.org_id, plot_id=plot_id, node_id=node_id
    )
    if existing is not None:
        return existing
    alert = Alert(
        id=uuid7(),
        state=AlertState.OPEN,
        severity=severity or rule.severity,
        opened_at=at,
        org_id=target.org_id,
        rule_id=rule.id,
        rule_code=rule.code,
        plot_id=plot_id,
        node_id=node_id,
        evidence=evidence or {},
    )
    return await alerts.insert(alert, _drafts(alert, target, at), target)


async def upgrade_to_critical(
    *, alert_id: UUID, org_id: UUID, at: datetime, alerts: AlertRepository
) -> Alert:
    """D5: `water_stress` at 48 h and a saturated-soil forecast become critical
    and notify again as critical.

    An alert that is already critical is returned untouched: the upgrade happens
    once, so a re-evaluated or repeated call must not send a second critical
    notice. A resolved alert is refused (`InvalidAlertTransitionError`). Two
    evaluators upgrading at once contend on the same row, and the one that
    loses gets the winner's critical alert back rather than a second notice.
    """
    alert = await _load(alert_id, org_id, alerts)
    if alert.severity is Severity.CRITICAL:
        return alert
    upgraded = alert.upgrade_to_critical()
    target = await alerts.get_target_context(plot_id=alert.plot_id, node_id=alert.node_id)
    try:
        return await alerts.save(
            upgraded,
            _drafts(upgraded, target, at),
            target.farm_id,
            expected_state=alert.state,
            expected_severity=alert.severity,
        )
    except InvalidAlertTransitionError:
        # Another writer moved the alert while this one decided. A concurrent
        # upgrade already did the work, so there is nothing left to send; any
        # other move is that writer's transition, not ours, and still invalid.
        current = await _load(alert_id, org_id, alerts)
        if current.severity is Severity.CRITICAL:
            return current
        raise


async def resolve_automatically(
    *, alert_id: UUID, org_id: UUID, farm_id: UUID, at: datetime, alerts: AlertRepository
) -> Alert:
    """The condition cleared beyond the hysteresis band, sustained (docs/06 §3)."""
    alert = await _load(alert_id, org_id, alerts)
    return await alerts.save(
        alert.resolve_automatically(at),
        [],
        farm_id,
        expected_state=alert.state,
        expected_severity=alert.severity,
    )


async def acknowledge(
    *,
    user_id: UUID,
    alert_id: UUID,
    at: datetime,
    alerts: AlertRepository,
    memberships: MembershipRepository,
) -> Alert:
    """A user claims the work on an alert of one of their orgs.

    The farm the change is published to is the alert's own (D15), resolved on
    the same session: a caller naming a foreign farm could never publish the
    transition to the farm whose SSE stream follows it.
    """
    alert = await _managed_alert(user_id, alert_id, alerts, memberships)
    target = await alerts.get_target_context(plot_id=alert.plot_id, node_id=alert.node_id)
    return await alerts.save(
        alert.acknowledge(at),
        [],
        target.farm_id,
        expected_state=alert.state,
        expected_severity=alert.severity,
    )


async def resolve_manually(
    *,
    user_id: UUID,
    alert_id: UUID,
    note: str | None,
    at: datetime,
    alerts: AlertRepository,
    memberships: MembershipRepository,
) -> Alert:
    """Close an acknowledged alert, storing the note (D3, D12)."""
    alert = await _managed_alert(user_id, alert_id, alerts, memberships)
    target = await alerts.get_target_context(plot_id=alert.plot_id, node_id=alert.node_id)
    return await alerts.save(
        alert.resolve_manually(at, note),
        [],
        target.farm_id,
        expected_state=alert.state,
        expected_severity=alert.severity,
    )


async def list_alerts(
    *,
    user_id: UUID,
    org_id: UUID,
    alerts: AlertRepository,
    memberships: MembershipRepository,
    plot_id: UUID | None = None,
    state: AlertState | None = None,
    limit: int = 50,
    cursor: UUID | None = None,
) -> list[Alert]:
    """A cursor page of one org's alerts (docs/04 `GET /alerts?org_id=`; D15).

    The membership is resolved first, so a caller of another org gets
    `NotAMemberError` (404 in the adapter) instead of an empty page that would
    read as "this org has no alerts".
    """
    await resolve_org_membership(user_id=user_id, org_id=org_id, memberships=memberships)
    return await alerts.list_for_orgs(
        [org_id], plot_id=plot_id, state=state, limit=limit, cursor=cursor
    )


async def _load(alert_id: UUID, org_id: UUID, alerts: AlertRepository) -> Alert:
    alert = await alerts.get_for_orgs(alert_id, [org_id])
    if alert is None:
        raise AlertNotFoundError(alert_id)
    return alert


async def _managed_alert(
    user_id: UUID, alert_id: UUID, alerts: AlertRepository, memberships: MembershipRepository
) -> Alert:
    """The alert the caller may manage, or `AlertNotFoundError`.

    Same reasoning as `farms.resolve_plot_access`: an alert-id-only route has no
    `org_id` in the path, so the alert is checked against every org the caller
    belongs to, and the role in that org decides whether it may be managed.
    """
    roles_by_org = {m.org_id: m.role for m in await memberships.list_for_user(user_id)}
    alert = await alerts.get_for_orgs(alert_id, list(roles_by_org))
    if alert is None:
        raise AlertNotFoundError(alert_id)
    assert alert.org_id is not None, "a stored alert carries its org_id"
    ensure_can_manage_alert(roles_by_org[alert.org_id])
    return alert


async def list_open_alerts_for_plots(
    *,
    plot_ids: Sequence[UUID],
    org_ids: Sequence[UUID],
    alerts: AlertRepository,
) -> list[Alert]:
    """Open and acknowledged alerts for plot_ids within org_ids (D-T0.3).

    Ordered critical first, then newest first.
    """
    if not plot_ids or not org_ids:
        return []
    return await alerts.list_open_for_plots(plot_ids=plot_ids, org_ids=org_ids)
