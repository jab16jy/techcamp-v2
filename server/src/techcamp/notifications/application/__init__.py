"""Notifications application facade (docs/06-diseno-detallado.md §4; ADR-0016; D5, D6).

`alerts` plans the rows of its own alerts through this package and writes them
with its own transaction, so the outbox guarantee stays in one place. `Channel`
and `NotificationDraft` are re-exported because docs/05 lets a module import
only another module's public `application` package: a row's shape travels
through the facade, never through `notifications.domain`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from uuid import UUID

from techcamp.notifications.application.manage_subscriptions import (
    delete_push_subscription,
    register_push_subscription,
)
from techcamp.notifications.application.ports import PushSubscriptionRepository
from techcamp.notifications.domain.models import Channel, NotificationDraft, next_attempt_at

__all__ = [
    "Channel",
    "NotificationDraft",
    "PushSubscriptionRepository",
    "delete_push_subscription",
    "next_attempt_at",
    "plan_notifications",
    "register_push_subscription",
]


def plan_notifications(
    recipients: Sequence[UUID],
    *,
    critical: bool,
    now: datetime,
    group_times: Mapping[UUID, datetime] | None = None,
) -> list[NotificationDraft]:
    """One `push` row per recipient, due per D6 (docs/06 §4 channels by severity).

    No call is made for an `info` alert: it is in-app only (D5), and the `sms`
    row of an escalated critical is written by the escalation job, not here.
    `group_times` is per recipient because grouping is per (user, farm): a
    recipient with no pending row in the window has no entry.
    """
    times = group_times or {}
    return [
        NotificationDraft(
            user_id=recipient,
            channel=Channel.PUSH,
            next_attempt_at=next_attempt_at(
                critical=critical, now=now, group_time=times.get(recipient)
            ),
        )
        for recipient in recipients
    ]
