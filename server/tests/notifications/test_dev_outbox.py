"""`GET /dev/outbox`: the seminar tray of simulated SMS/WhatsApp messages
(docs/04-api.md:182; docs/06 §4; ADR-0021; D8).

A seminar runs with no SMS provider, so the tray is how a room sees that a
critical alert actually reached its channel (RNF-05). It is a `/dev` route: it
exists only in the seminar profile, takes no `org_id` and no session — the same
posture as the other `/dev` routes, whose whole audience is one room with one
stack behind it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.orm import AlertRow, AlertRuleRow
from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import AppUserRow, OrganizationRow
from techcamp.main import app
from techcamp.notifications.adapters.orm import NotificationRow
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)
_DUE = datetime(2026, 9, 27, 15, 0, tzinfo=UTC)


def _client() -> TestClient:
    return TestClient(app, base_url="http://testserver/api/v1")


async def _outbox_row(
    session: AsyncSession, *, channel: str, created_at: datetime
) -> tuple[NotificationRow, UUID]:
    """One delivered outbox row on a `critical` alert of its own organization."""
    org_id, user_id, farm_id, plot_id = uuid7(), uuid7(), uuid7(), uuid7()
    session.add(AppUserRow(id=user_id, phone=f"+57{uuid7().int % 10**13:013d}"))
    session.add(OrganizationRow(id=org_id, name="Test Org", kind="individual"))
    await session.commit()
    session.add(
        FarmRow(
            id=farm_id,
            org_id=org_id,
            name="Finca Principal",
            municipality_code="47001",
            location=_POINT,
        )
    )
    await session.commit()
    session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=_BOUNDARY,
            irrigation_system="drip",
        )
    )
    await session.commit()
    rule_id = (
        await session.execute(select(AlertRuleRow.id).where(AlertRuleRow.code == "water_stress"))
    ).scalar_one()
    alert = AlertRow(
        id=uuid7(),
        org_id=org_id,
        rule_id=rule_id,
        plot_id=plot_id,
        state="open",
        severity="critical",
        opened_at=_DUE,
    )
    session.add(alert)
    row = NotificationRow(
        id=uuid7(),
        alert_id=alert.id,
        user_id=user_id,
        channel=channel,
        status="sent",
        attempts=1,
        next_attempt_at=_DUE,
        created_at=created_at,
        sent_at=created_at + timedelta(seconds=2),
    )
    session.add(row)
    await session.commit()
    return row, org_id


async def test_the_tray_lists_the_simulated_messages_with_their_alert(
    db_session: AsyncSession,
) -> None:
    row, org_id = await _outbox_row(db_session, channel="sms", created_at=_DUE)

    response = _client().get("/dev/outbox")

    assert response.status_code == 200, response.text
    assert response.json() == [
        {
            "id": str(row.id),
            "alert_id": str(row.alert_id),
            "user_id": str(row.user_id),
            "org_id": str(org_id),
            "channel": "sms",
            "status": "sent",
            "attempts": 1,
            "rule_code": "water_stress",
            "severity": "critical",
            "created_at": "2026-09-27T15:00:00Z",
            "sent_at": "2026-09-27T15:00:02Z",
            "last_error": None,
        }
    ]


async def test_the_tray_shows_the_newest_message_first(db_session: AsyncSession) -> None:
    """A room reads the last thing that went out, not the first of the session."""
    older, _ = await _outbox_row(db_session, channel="sms", created_at=_DUE)
    newer, _ = await _outbox_row(
        db_session, channel="whatsapp", created_at=_DUE + timedelta(hours=1)
    )

    listed = [UUID(item["id"]) for item in _client().get("/dev/outbox").json()]

    assert listed == [newer.id, older.id]


async def test_the_tray_hides_the_push_rows(db_session: AsyncSession) -> None:
    """D8: the tray is the SMS/WhatsApp simulation. A `push` row is a browser
    notification, not a simulated message, and the service worker is the thing
    that shows it (T9)."""
    await _outbox_row(db_session, channel="push", created_at=_DUE)

    assert _client().get("/dev/outbox").json() == []
