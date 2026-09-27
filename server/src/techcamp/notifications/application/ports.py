"""Repository port the push subscription use cases depend on (ADR-0002; docs/05).

A subscription is external I/O (the browser's endpoint), so it is behind a
port; the SQLAlchemy adapter is its only implementation.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class PushSubscriptionRepository(Protocol):
    async def upsert(self, *, user_id: UUID, endpoint: str, keys: dict[str, str]) -> UUID:
        """Bind `endpoint` to `user_id` and return the row's id.

        `push_subscription.endpoint` is UNIQUE (docs/03:307-312), so a browser
        that registers again after a new key pair rebinds and replaces the keys
        of the row it already owns instead of failing on the constraint.
        """
        ...

    async def delete_owned(self, subscription_id: UUID, user_id: UUID) -> bool:
        """Delete the row only when `user_id` owns it; `False` when it does not
        exist or belongs to someone else (both are 404 for the caller)."""
        ...
