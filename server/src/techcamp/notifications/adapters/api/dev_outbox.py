"""Seminar-only view of the simulated SMS/WhatsApp messages (docs/04-api.md:182;
docs/06 §4; ADR-0021; D8).

Registered only when the profile is seminar. A seminar runs with no SMS
provider, so this tray is how a room sees that a critical alert actually
reached its channel instead of disappearing into a provider that does not exist
(RNF-05: "en el seminario, hasta la bandeja `/dev/outbox`").

No session and no `org_id`, like the other `/dev` routes: the audience is one
room with one stack behind it, and every row in the table belongs to that room.
"""

from __future__ import annotations

import datetime
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from techcamp.alerts.adapters.orm import AlertRow, AlertRuleRow
from techcamp.notifications.adapters.orm import NotificationRow
from techcamp.shared.db import SessionDep

router = APIRouter(prefix="/dev/outbox", tags=["dev-outbox"])

TRAY_LIMIT = 50
"""A live window, not an archive: the room asks what went out recently, and the
sweep takes at most 50 rows per pass anyway (docs/06 §4 `LIMIT 50`)."""


class OutboxMessageView(BaseModel):
    """One simulated message with the alert it belongs to (D8).

    `rule_code` and `severity` come from the alert because a tray row that only
    said "sms sent" would not tell the room what arrived. `last_error` is
    included on purpose: a message the dispatcher gave up on is the one a room
    needs to see.
    """

    id: UUID
    alert_id: UUID
    user_id: UUID
    org_id: UUID
    channel: str
    status: str
    attempts: int
    rule_code: str
    severity: str
    created_at: datetime.datetime
    sent_at: datetime.datetime | None
    last_error: str | None


@router.get("", response_model=list[OutboxMessageView])
async def get_dev_outbox(session: SessionDep) -> list[OutboxMessageView]:
    """The simulated SMS/WhatsApp rows, newest first.

    `push` rows are not here: a push is a browser notification, and the service
    worker is what shows it (D8, T9).
    """
    result = await session.execute(
        select(NotificationRow, AlertRow.org_id, AlertRow.severity, AlertRuleRow.code)
        .join(AlertRow, AlertRow.id == NotificationRow.alert_id)
        .join(AlertRuleRow, AlertRuleRow.id == AlertRow.rule_id)
        .where(NotificationRow.channel.in_(("sms", "whatsapp")))
        .order_by(NotificationRow.created_at.desc(), NotificationRow.id.desc())
        .limit(TRAY_LIMIT)
    )
    return [
        OutboxMessageView(
            id=row.id,
            alert_id=row.alert_id,
            user_id=row.user_id,
            org_id=org_id,
            channel=row.channel,
            status=row.status,
            attempts=row.attempts,
            rule_code=code,
            severity=severity,
            created_at=row.created_at,
            sent_at=row.sent_at,
            last_error=row.last_error,
        )
        for row, org_id, severity, code in result
    ]
