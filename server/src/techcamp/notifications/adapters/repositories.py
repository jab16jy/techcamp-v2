"""The `notification` outbox writer (docs/06-diseno-detallado.md §4; ADR-0016).

`alerts` builds this writer with the same `AsyncSession` it writes the alert on,
which is what makes the alert, its rows, their dispatch job and the `NOTIFY` one
transaction (D14, D7). The `push_subscription` writer is a plain CRUD that never
shares a transaction with an alert, and it lives in `subscriptions.py` because
the dispatch job needs it too.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.notifications.adapters.jobs import enqueue_dispatch
from techcamp.notifications.adapters.orm import NotificationRow
from techcamp.notifications.domain.models import NotificationDraft
from techcamp.shared.ids import uuid7


class SqlAlchemyNotificationRepository:
    """Writes the pending rows of an alert and defers their dispatch (docs/06 §4).

    Every method adds to the caller's transaction without committing it, so the
    alert, its notices and the job that sends them land together or not at all
    (ADR-0016).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_drafts(
        self, alert_id: UUID, drafts: Sequence[NotificationDraft]
    ) -> list[NotificationRow]:
        """Add the rows to the caller's transaction without committing it.

        D7: the dispatch is deferred here, in the transaction that wrote the
        rows, so a committed alert always has a job behind it and a rolled back
        one leaves no job to pick up a row that does not exist. An alert with no
        rows (`info`, in-app only — D5) queues nothing, because there is
        nothing to send.
        """
        created_at = datetime.now(UTC)
        rows = [
            NotificationRow(
                id=uuid7(),
                alert_id=alert_id,
                user_id=draft.user_id,
                channel=draft.channel.value,
                status="pending",
                attempts=0,
                next_attempt_at=draft.next_attempt_at,
                created_at=created_at,
            )
            for draft in drafts
        ]
        self._session.add_all(rows)
        if rows:
            await enqueue_dispatch(self._session)
        return rows
