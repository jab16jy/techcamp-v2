"""FastAPI dependencies wiring notifications adapters into requests."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from techcamp.notifications.adapters.subscriptions import SqlAlchemyPushSubscriptionRepository
from techcamp.shared.db import SessionDep


async def get_push_subscription_repository(
    session: SessionDep,
) -> SqlAlchemyPushSubscriptionRepository:
    return SqlAlchemyPushSubscriptionRepository(session)


PushSubscriptionRepoDep = Annotated[
    SqlAlchemyPushSubscriptionRepository, Depends(get_push_subscription_repository)
]
