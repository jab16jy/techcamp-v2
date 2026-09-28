"""Postgres writer for the notification outbox and push subscriptions
(docs/06-diseno-detallado.md §4; ADR-0016).

`alerts` builds the outbox writer with the same `AsyncSession` it writes the
alert on, which is what makes the alert, its rows, their dispatch job and the
`NOTIFY` one transaction (D14, D7). The subscription repository is a plain CRUD:
it never shares a transaction with an alert.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.notifications.adapters.jobs import enqueue_dispatch
from techcamp.notifications.adapters.orm import NotificationRow, PushSubscriptionRow
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


class SqlAlchemyPushSubscriptionRepository:
    """CRUD of `push_subscription` (docs/03:307-312; D15).

    The upsert is the write path the browser needs: `endpoint` is UNIQUE, so a
    re-registration (a new key pair, or the same browser under another account)
    rebinds the existing row instead of hitting the constraint.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, *, user_id: UUID, endpoint: str, keys: dict[str, str]) -> UUID:
        result = await self._session.execute(
            select(PushSubscriptionRow).where(PushSubscriptionRow.endpoint == endpoint)
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = PushSubscriptionRow(
                id=uuid7(),
                user_id=user_id,
                endpoint=endpoint,
                keys=keys,
                created_at=datetime.now(UTC),
            )
            self._session.add(row)
        else:
            row.user_id = user_id
            row.keys = keys
        await self._session.commit()
        return row.id

    async def delete_owned(self, subscription_id: UUID, user_id: UUID) -> bool:
        result = await self._session.execute(
            delete(PushSubscriptionRow).where(
                PushSubscriptionRow.id == subscription_id, PushSubscriptionRow.user_id == user_id
            )
        )
        await self._session.commit()
        return cast(CursorResult[Any], result).rowcount > 0
