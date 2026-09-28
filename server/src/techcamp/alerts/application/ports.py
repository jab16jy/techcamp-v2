"""Repository ports the alert use cases depend on (ADR-0002; docs/05).

Every read filters by `org_id` (docs/09-cuellos-de-botella.md#seguridad) and the
two writes own their transaction, the outbox rows and the `NOTIFY`
(docs/06 §4; ADR-0016). The rule repository is the plain CRUD of `alert_rule`:
the lifecycle repository is the only one that opens a transaction with
notifications, so this one is separate.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from techcamp.alerts.domain.models import Alert, AlertRule, AlertRuleChanges, AlertState, Severity
from techcamp.notifications.application import NotificationDraft

type UnitOfWorkRecovery = Callable[[], Awaitable[None]]
"""D24: put the shared unit of work back in a usable state after one plot's
evaluation raised.

The plot loop keeps going on the same `AsyncSession`, and a failed statement
leaves that session in a failed-transaction state, so every read after it would
raise `PendingRollbackError` — the per-plot isolation would be cosmetic and the
whole batch would go undecided in silence. The adapter supplies the session's own
rollback: it discards only the failed plot's uncommitted work (the alerts of the
plots already decided are committed, ADR-0016), and it raises if the session
cannot be recovered, which is the one case where re-queueing the batch is right.
"""


@dataclass(frozen=True, slots=True)
class AlertTarget:
    """Everything an alert about one target needs from other modules (D4, D14).

    Resolved on the same session as the alert write: the plot's org and farm
    (farms), the farm's technician and the org's members (identity), and the
    pending rows of that farm that a new non-critical row may join.
    """

    org_id: UUID
    farm_id: UUID
    recipients: tuple[UUID, ...]
    group_times: dict[UUID, datetime]


@dataclass(frozen=True, slots=True)
class EscalationTarget:
    """Who an escalation is for, and the farm whose stream carries it (D4).

    Separate from `AlertTarget` because the escalation has the OPPOSITE rule of a
    plot alert: docs/06 §3 sends an escalation to the farm's technician
    (`farm.technician_id`), falling back to the org's owners, while a plot alert
    goes to the org's owners and producers. It also has no grouping (D38: a
    critical is never in a group), so there are no group times to resolve.
    """

    farm_id: UUID
    recipients: tuple[UUID, ...]


class AlertRepository(Protocol):
    async def get_non_resolved_for_target(
        self, *, rule_id: UUID, org_id: UUID, plot_id: UUID | None, node_id: UUID | None
    ) -> Alert | None:
        """The alert the partial unique index already holds for (rule, target).

        `plot_id` and `node_id` are what the caller evaluated, never guessed
        from the rule code, and exactly one of them is given; the index makes
        the row unique, so the read returns it or nothing.
        """
        ...

    async def get_for_orgs(self, alert_id: UUID, org_ids: Sequence[UUID]) -> Alert | None:
        """An alert across every org the caller belongs to (docs/04: 404 across
        orgs, so the read itself is the isolation)."""
        ...

    async def list_for_orgs(
        self,
        org_ids: Sequence[UUID],
        *,
        plot_id: UUID | None = None,
        state: AlertState | None = None,
        limit: int = 50,
        cursor: UUID | None = None,
    ) -> list[Alert]:
        """A cursor page, newest first (uuid7 is time-ordered, so `id` is the
        cursor, the same convention the other routers use)."""
        ...

    async def get_target_context(
        self, *, plot_id: UUID | None, node_id: UUID | None
    ) -> AlertTarget:
        """The org, farm, recipients (D4) and pending group times (D6) of a
        target, resolved on the session the alert is written on."""
        ...

    async def get_escalation_target(
        self, *, org_id: UUID, plot_id: UUID | None, node_id: UUID | None
    ) -> EscalationTarget:
        """The farm the alert's `alert.updated` belongs to and who to text about
        it (docs/06 §3; D4), resolved on the session the escalation is written on.

        The target's own org is the one that must come back: an escalation reads
        the farm through the alert's target, so a farm of another organization
        would hand one org's alert to another's technician (docs/09).
        """
        ...

    async def lock_escalation_candidate(self, *, org_id: UUID, at: datetime) -> Alert | None:
        """The next critical alert of one org whose 2 h are up, locked, or nothing.

        The row is taken `FOR UPDATE SKIP LOCKED` and the caller decides under
        that lock, because the write that follows is the same transaction
        (docs/06 §3 "Reloj de escalamiento"; D12, D42). A second worker skips a
        row another one holds instead of waiting on it, and one alert per
        transaction is the unit `save` already is. `org_id` is required, never
        optional (docs/09).
        """
        ...

    async def insert(
        self, alert: Alert, drafts: Sequence[NotificationDraft], target: AlertTarget
    ) -> Alert:
        """Insert the alert with its outbox rows in one transaction, publishing
        `alert.opened` to `target.farm_id`, and return the already-open alert of
        (rule, target) instead of failing when the partial unique index says one
        is there."""
        ...

    async def save(
        self,
        alert: Alert,
        drafts: Sequence[NotificationDraft],
        farm_id: UUID,
        *,
        expected_state: AlertState,
        expected_severity: Severity,
    ) -> Alert:
        """Persist the transitioned alert with any new outbox rows in one
        transaction, publishing `alert.updated` to the farm the SSE stream of
        the caller is subscribed to.

        `expected_state` and `expected_severity` are what the caller validated
        against, and `farm_id` must be the alert's own farm; a transition that no
        longer holds, or a farm that does not carry the alert, is refused.
        """
        ...


class AlertRuleRepository(Protocol):
    """CRUD of `alert_rule` (docs/03:272-283; D11).

    `list_for_org` serves the factory rules (`org_id is null`, readable by every
    member) together with one org's own rules, and `get_for_orgs` only ever
    returns a rule that belongs to one of the given orgs, so a factory rule is
    404 for every org (D11).
    """

    async def list_for_org(self, org_id: UUID) -> list[AlertRule]: ...

    async def get_for_orgs(self, rule_id: UUID, org_ids: Sequence[UUID]) -> AlertRule | None: ...

    async def create(self, rule: AlertRule) -> AlertRule:
        """Insert one org's threshold rule."""
        ...

    async def update(self, rule_id: UUID, org_id: UUID, changes: AlertRuleChanges) -> AlertRule:
        """Persist only the fields `changes` states, so two concurrent PATCHes
        of different fields cannot revert each other (R3-001)."""
        ...
