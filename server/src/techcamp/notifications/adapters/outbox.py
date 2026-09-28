"""The `notification` outbox as the dispatcher reads and writes it
(docs/06-diseno-detallado.md §4; ADR-0016).

Its own module, not `repositories.py`, for one reason: the dispatch job needs
this repository, and the job must not import the module that enqueues it. The
dependency then runs one way — `repositories.py` (the alert's outbox write) →
`jobs.py` → this module — the same direction `telemetry` and `farms` use to
enqueue a neighbour's job from inside their own transaction.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.orm import AlertRow, AlertRuleRow
from techcamp.notifications.adapters.orm import NotificationRow
from techcamp.notifications.domain.models import CLAIM_LIMIT, Channel, PendingNotification

_RULE_CODE = AlertRuleRow.code.label("rule_code")


class SqlAlchemyOutboxRepository:
    """Claim due rows and record each one's outcome (docs/06 §4).

    Every write commits. The claim's `FOR UPDATE SKIP LOCKED` locks live until
    the transaction ends, so the commit is what releases a row once its outcome
    is durable — and it is per row, not per batch: a message that reached the
    provider must not be sent again because a later row in the same batch died.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim_due(
        self, *, now: datetime, limit: int = CLAIM_LIMIT
    ) -> Sequence[PendingNotification]:
        """docs/06 §4: `SELECT … WHERE status='pending' AND next_attempt_at <=
        now() FOR UPDATE SKIP LOCKED LIMIT 50`.

        The alert's `rule_code` and `severity` join in because they are the
        message: a provider handed an id alone cannot render anything. `of=`
        names the outbox table because the join brings the rule in too, and
        locking an `alert` or an `alert_rule` row would block the very writes
        that produce the notices. The join is to the row's own alert, so no row
        of one organization is reachable through another's.
        """
        result = await self._session.execute(
            select(NotificationRow, AlertRow.org_id, AlertRow.severity, _RULE_CODE)
            .join(AlertRow, AlertRow.id == NotificationRow.alert_id)
            .join(AlertRuleRow, AlertRuleRow.id == AlertRow.rule_id)
            .where(NotificationRow.status == "pending", NotificationRow.next_attempt_at <= now)
            .order_by(NotificationRow.next_attempt_at, NotificationRow.id)
            .limit(limit)
            .with_for_update(of=NotificationRow, skip_locked=True)
        )
        return [
            PendingNotification(
                id=row.id,
                alert_id=row.alert_id,
                org_id=org_id,
                user_id=row.user_id,
                channel=Channel(row.channel),
                rule_code=rule_code,
                severity=severity,
                attempts=row.attempts,
            )
            for row, org_id, severity, rule_code in result
        ]

    async def mark_sent(self, notification_id: UUID, *, at: datetime) -> None:
        row = await self._locked(notification_id)
        row.status = "sent"
        row.sent_at = at
        row.last_error = None
        await self._session.commit()

    async def mark_retry(
        self, notification_id: UUID, *, attempts: int, next_attempt_at: datetime, error: str
    ) -> None:
        row = await self._locked(notification_id)
        row.status = "pending"
        row.attempts = attempts
        row.next_attempt_at = next_attempt_at
        row.last_error = error
        await self._session.commit()

    async def mark_failed(self, notification_id: UUID, *, attempts: int, error: str) -> None:
        row = await self._locked(notification_id)
        row.status = "failed"
        row.attempts = attempts
        row.last_error = error
        await self._session.commit()

    async def _locked(self, notification_id: UUID) -> NotificationRow:
        """The claimed row, re-read under its own lock.

        The claim already holds it, so this is a re-read rather than a new
        lookup: the ORM session has no row in its identity map for the claim's
        tuple projection, and the write needs the mapped object.
        """
        return (
            await self._session.execute(
                select(NotificationRow)
                .where(NotificationRow.id == notification_id)
                .with_for_update()
            )
        ).scalar_one()
