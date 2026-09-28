"""Postgres writer for the notification outbox (docs/06-diseno-detallado.md §4; ADR-0016).

`alerts` builds this with the same `AsyncSession` it writes the alert on, which
is what makes the alert, its rows and the `NOTIFY` one transaction (D14).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.notifications.adapters.orm import NotificationRow
from techcamp.notifications.domain.models import NotificationDraft
from techcamp.shared.ids import uuid7


class SqlAlchemyNotificationRepository:
    """Writes the pending rows of an alert. Dispatch is the worker's (T7)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_drafts(
        self, alert_id: UUID, drafts: Sequence[NotificationDraft]
    ) -> list[NotificationRow]:
        """Add the rows to the caller's transaction without committing it."""
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
        return rows
