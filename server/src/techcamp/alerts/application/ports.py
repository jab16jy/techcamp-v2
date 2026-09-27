"""Repository port the alert use cases depend on (ADR-0002; docs/05).

Six methods, and the adapter is the only implementation: every read filters by
`org_id` (docs/09-cuellos-de-botella.md#seguridad) and the two writes own their
transaction, the outbox rows and the `NOTIFY` (docs/06 §4; ADR-0016).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from techcamp.alerts.domain.models import Alert, AlertState, Severity
from techcamp.notifications.application import NotificationDraft


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
