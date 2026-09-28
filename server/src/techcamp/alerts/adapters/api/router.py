"""Alert endpoints (docs/04-api.md §Alertas y notificaciones; D15).

The routers carry the HTTP shape only: pydantic in, domain errors mapped to
`problem+json` out, and the T3 use cases decide (docs/04 conventions: a
resource of another organization is 404, never 403).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from techcamp.alerts.adapters.api.deps import AlertRepoDep, AlertRuleRepoDep
from techcamp.alerts.application import (
    acknowledge,
    create_rule,
    list_alerts,
    list_rules,
    resolve_manually,
    update_rule,
)
from techcamp.alerts.domain import (
    Alert,
    AlertNotFoundError,
    AlertRule,
    AlertRuleChanges,
    AlertRuleNotFoundError,
    AlertState,
    InsufficientRoleError,
    InvalidAlertRuleError,
    InvalidAlertTransitionError,
    Severity,
)
from techcamp.identity.adapters.api.deps import CurrentUserId, MembershipRepoDep
from techcamp.identity.domain.errors import NotAMemberError
from techcamp.shared.errors import ProblemError

router = APIRouter(tags=["alerts"])


class AlertView(BaseModel):
    id: UUID
    org_id: UUID
    rule_id: UUID
    rule_code: str
    plot_id: UUID | None
    node_id: UUID | None
    state: AlertState
    severity: Severity
    evidence: dict[str, Any]
    opened_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    escalated_at: datetime | None
    resolution_note: str | None


class AlertPage(BaseModel):
    items: list[AlertView]
    next_cursor: str | None


class AlertResolveRequest(BaseModel):
    note: str | None = None


def _alert_view(alert: Alert) -> AlertView:
    return AlertView(
        id=alert.id,
        org_id=alert.org_id,
        rule_id=alert.rule_id,
        rule_code=alert.rule_code,
        plot_id=alert.plot_id,
        node_id=alert.node_id,
        state=alert.state,
        severity=alert.severity,
        evidence=alert.evidence,
        opened_at=alert.opened_at,
        acknowledged_at=alert.acknowledged_at,
        resolved_at=alert.resolved_at,
        escalated_at=alert.escalated_at,
        resolution_note=alert.resolution_note,
    )


@router.get("/alerts", response_model=AlertPage)
async def get_alerts(
    user_id: CurrentUserId,
    alerts: AlertRepoDep,
    memberships: MembershipRepoDep,
    org_id: Annotated[UUID, Query()],
    plot_id: Annotated[UUID | None, Query()] = None,
    state: Annotated[AlertState | None, Query()] = None,
    limit: Annotated[int, Query(gt=0, le=200)] = 50,
    cursor: Annotated[UUID | None, Query()] = None,
) -> AlertPage:
    """docs/04 `GET /alerts?org_id=`: one org's alerts, newest first (D15)."""
    try:
        page = await list_alerts(
            user_id=user_id,
            org_id=org_id,
            plot_id=plot_id,
            state=state,
            limit=limit,
            cursor=cursor,
            alerts=alerts,
            memberships=memberships,
        )
    except NotAMemberError as exc:
        raise ProblemError(status=404, title="Organization not found") from exc
    next_cursor = str(page[-1].id) if len(page) == limit else None
    return AlertPage(items=[_alert_view(alert) for alert in page], next_cursor=next_cursor)


@router.post("/alerts/{alert_id}:acknowledge", response_model=AlertView)
async def acknowledge_alert(
    alert_id: UUID,
    user_id: CurrentUserId,
    alerts: AlertRepoDep,
    memberships: MembershipRepoDep,
) -> AlertView:
    try:
        alert = await acknowledge(
            user_id=user_id,
            alert_id=alert_id,
            at=datetime.now(UTC),
            alerts=alerts,
            memberships=memberships,
        )
    except AlertNotFoundError as exc:
        raise ProblemError(status=404, title="Alert not found") from exc
    except InsufficientRoleError as exc:
        raise ProblemError(status=403, title="Role cannot manage alerts") from exc
    except InvalidAlertTransitionError as exc:
        raise ProblemError(status=409, title="Invalid alert transition") from exc
    return _alert_view(alert)


@router.post("/alerts/{alert_id}:resolve", response_model=AlertView)
async def resolve_alert(
    alert_id: UUID,
    user_id: CurrentUserId,
    alerts: AlertRepoDep,
    memberships: MembershipRepoDep,
    payload: AlertResolveRequest | None = None,
) -> AlertView:
    """docs/06 §3: manual resolution only from `acknowledged`, so 409 otherwise.

    The body is optional: `POST /alerts/{id}:resolve` closes the alert with no
    note (docs/04 `{ note? }`).
    """
    try:
        alert = await resolve_manually(
            user_id=user_id,
            alert_id=alert_id,
            note=payload.note if payload is not None else None,
            at=datetime.now(UTC),
            alerts=alerts,
            memberships=memberships,
        )
    except AlertNotFoundError as exc:
        raise ProblemError(status=404, title="Alert not found") from exc
    except InsufficientRoleError as exc:
        raise ProblemError(status=403, title="Role cannot manage alerts") from exc
    except InvalidAlertTransitionError as exc:
        raise ProblemError(status=409, title="Invalid alert transition") from exc
    return _alert_view(alert)


class AlertRuleView(BaseModel):
    id: UUID
    org_id: UUID | None
    code: str
    metric: str | None
    operator: str | None
    threshold: float | None
    hysteresis: float
    min_duration_min: int
    severity: Severity
    crop_id: int | None


class AlertRuleCreateRequest(BaseModel):
    org_id: UUID
    code: str = Field(min_length=1, max_length=100)
    metric: str
    operator: Literal["<", ">"]
    threshold: float
    hysteresis: float = Field(default=0.0, ge=0)
    min_duration_min: int = Field(default=0, ge=0)
    severity: Severity = Severity.WARNING
    crop_id: int | None = None


class AlertRulePatchRequest(BaseModel):
    threshold: float | None = None
    hysteresis: float | None = Field(default=None, ge=0)
    min_duration_min: int | None = Field(default=None, ge=0)
    severity: Severity | None = None


def _rule_view(rule: AlertRule) -> AlertRuleView:
    return AlertRuleView(
        id=rule.id,
        org_id=rule.org_id,
        code=rule.code,
        metric=rule.metric,
        operator=rule.operator,
        threshold=rule.threshold,
        hysteresis=rule.hysteresis,
        min_duration_min=int(rule.min_duration.total_seconds() // 60),
        severity=rule.severity,
        crop_id=rule.crop_id,
    )


def _reject_explicit_null(raw: dict[str, Any]) -> None:
    """A rule's thresholds are not nullable (only the factory `water_stress`
    rule has none), so an explicit JSON `null` is a client error and not a DB
    `IntegrityError` 500. The farms PATCH routes reject the same way."""
    nulled = sorted(key for key, value in raw.items() if value is None)
    if nulled:
        raise ProblemError(status=422, title=f"{', '.join(nulled)} cannot be null")


@router.get("/alert-rules", response_model=list[AlertRuleView])
async def get_alert_rules(
    user_id: CurrentUserId,
    rules: AlertRuleRepoDep,
    memberships: MembershipRepoDep,
    org_id: Annotated[UUID, Query()],
) -> list[AlertRuleView]:
    """D11, D15: the factory rules and one org's own, for any member of it."""
    try:
        listed = await list_rules(
            user_id=user_id, org_id=org_id, rules=rules, memberships=memberships
        )
    except NotAMemberError as exc:
        raise ProblemError(status=404, title="Organization not found") from exc
    return [_rule_view(rule) for rule in listed]


@router.post("/alert-rules", response_model=AlertRuleView, status_code=201)
async def post_alert_rule(
    payload: AlertRuleCreateRequest,
    user_id: CurrentUserId,
    rules: AlertRuleRepoDep,
    memberships: MembershipRepoDep,
) -> AlertRuleView:
    """D11: an org writes its own threshold rules, `owner` only."""
    try:
        rule = await create_rule(
            user_id=user_id,
            org_id=payload.org_id,
            code=payload.code,
            metric=payload.metric,
            operator=payload.operator,
            threshold=payload.threshold,
            hysteresis=payload.hysteresis,
            min_duration=timedelta(minutes=payload.min_duration_min),
            severity=payload.severity,
            crop_id=payload.crop_id,
            rules=rules,
            memberships=memberships,
        )
    except NotAMemberError as exc:
        raise ProblemError(status=404, title="Organization not found") from exc
    except InsufficientRoleError as exc:
        raise ProblemError(status=403, title="Role cannot manage alert rules") from exc
    except InvalidAlertRuleError as exc:
        # The detail names the offending rule, never the database message.
        raise ProblemError(status=422, title="Invalid alert rule") from exc
    return _rule_view(rule)


@router.patch("/alert-rules/{rule_id}", response_model=AlertRuleView)
async def patch_alert_rule(
    rule_id: UUID,
    payload: AlertRulePatchRequest,
    user_id: CurrentUserId,
    rules: AlertRuleRepoDep,
    memberships: MembershipRepoDep,
) -> AlertRuleView:
    """D11: a factory rule or another org's rule is 404, never 403."""
    raw = payload.model_dump(exclude_unset=True)
    _reject_explicit_null(raw)
    stated = raw.get("min_duration_min")
    changes = AlertRuleChanges(
        threshold=raw.get("threshold"),
        hysteresis=raw.get("hysteresis"),
        min_duration=None if stated is None else timedelta(minutes=stated),
        severity=raw.get("severity"),
    )
    try:
        rule = await update_rule(
            user_id=user_id, rule_id=rule_id, changes=changes, rules=rules, memberships=memberships
        )
    except AlertRuleNotFoundError as exc:
        raise ProblemError(status=404, title="Alert rule not found") from exc
    except InsufficientRoleError as exc:
        raise ProblemError(status=403, title="Role cannot manage alert rules") from exc
    return _rule_view(rule)
