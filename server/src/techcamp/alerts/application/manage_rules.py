"""Alert rule read and write use cases (docs/04-api.md §Alertas; docs/03:272-283; D11, D15).

The factory rules (`org_id is null`) are readable by every member of every org
and never editable: a rule is written only by an `owner` of the org it belongs
to, and any other rule or org is `AlertRuleNotFoundError` (404 in the
adapter). Evaluation of these rules is T5; here they are only stored and read.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from techcamp.alerts.application.ports import AlertRuleRepository
from techcamp.alerts.domain.errors import AlertRuleNotFoundError
from techcamp.alerts.domain.models import (
    AlertRule,
    AlertRuleChanges,
    Severity,
    ensure_can_manage_rules,
)
from techcamp.identity.application.ports import MembershipRepository
from techcamp.identity.application.resolve_org_access import resolve_org_membership
from techcamp.shared.ids import uuid7


async def list_rules(
    *, org_id: UUID, rules: AlertRuleRepository, memberships: MembershipRepository, user_id: UUID
) -> list[AlertRule]:
    """The factory rules plus one org's own, for any member of that org (D15).

    The membership is resolved first: a caller of another org gets
    `NotAMemberError` (404) instead of the factory rules alone, which would
    read as "this org has no rules of its own".
    """
    await resolve_org_membership(user_id=user_id, org_id=org_id, memberships=memberships)
    return await rules.list_for_org(org_id)


async def create_rule(
    *,
    user_id: UUID,
    org_id: UUID,
    code: str,
    metric: str,
    operator: str,
    threshold: float,
    hysteresis: float,
    min_duration: timedelta,
    severity: Severity,
    crop_id: int | None,
    rules: AlertRuleRepository,
    memberships: MembershipRepository,
) -> AlertRule:
    """D11: an org adds its own threshold rule; only an `owner` may."""
    membership = await resolve_org_membership(
        user_id=user_id, org_id=org_id, memberships=memberships
    )
    ensure_can_manage_rules(membership.role)
    return await rules.create(
        AlertRule(
            id=uuid7(),
            org_id=org_id,
            code=code,
            metric=metric,
            operator=operator,
            threshold=threshold,
            hysteresis=hysteresis,
            min_duration=min_duration,
            severity=severity,
            crop_id=crop_id,
        )
    )


async def update_rule(
    *,
    user_id: UUID,
    rule_id: UUID,
    changes: AlertRuleChanges,
    rules: AlertRuleRepository,
    memberships: MembershipRepository,
) -> AlertRule:
    """Change only the thresholds the caller stated (R3-001).

    Same reasoning as `farms.resolve_plot_access`: the route has no `org_id`, so
    the rule is looked up in every org the caller belongs to (never the factory
    ones) and the role in the rule's own org decides.
    """
    roles_by_org = {m.org_id: m.role for m in await memberships.list_for_user(user_id)}
    rule = await rules.get_for_orgs(rule_id, list(roles_by_org))
    if rule is None or rule.org_id is None:
        raise AlertRuleNotFoundError(rule_id)
    ensure_can_manage_rules(roles_by_org[rule.org_id])
    return await rules.update(rule.id, rule.org_id, changes)
