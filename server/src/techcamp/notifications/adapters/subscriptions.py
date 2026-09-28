"""The `push_subscription` writer (docs/03-modelo-datos.md:312-318; docs/06 §4).

Its own module, not `repositories.py`, for the same reason `outbox.py` has one:
the dispatch job needs this repository, and the job must not import the module
that enqueues it. `repositories.py` imports `jobs.py`, so a `jobs.py` that also
imported the subscription writer from there would be a cycle. The dependency runs
one way — `repositories.py` → `jobs.py` → `subscriptions.py` — which is what lets
both the API and the Web Push sender share one implementation of the same table.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.notifications.adapters.orm import PushSubscriptionRow
from techcamp.notifications.domain.models import PushSubscription
from techcamp.shared.ids import uuid7


class SqlAlchemyPushSubscriptionRepository:
    """CRUD of `push_subscription` (docs/03:312-318; D15).

    The upsert is the write path the browser needs: `endpoint` is UNIQUE, so a
    re-registration (a new key pair, or the same browser under another account)
    rebinds the existing row instead of hitting the constraint.

    Every method commits. That is what the API's own use case wants — a browser
    that registered a subscription has it — and it is why the Web Push sender is
    handed a session of its own rather than the dispatcher's: a commit here would
    otherwise end the transaction holding the dispatch claim's row locks.
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

    async def list_for_user(self, user_id: UUID) -> list[PushSubscription]:
        """The user's own browsers, oldest first, so a repeat send is deterministic."""
        result = await self._session.execute(
            select(PushSubscriptionRow)
            .where(PushSubscriptionRow.user_id == user_id)
            .order_by(PushSubscriptionRow.created_at, PushSubscriptionRow.id)
        )
        return [
            PushSubscription(id=row.id, endpoint=row.endpoint, keys=row.keys)
            for row in result.scalars()
        ]
