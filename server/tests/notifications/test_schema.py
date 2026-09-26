"""Notification and push subscription schema behavior (docs/03-modelo-datos.md:298-312, 486)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.orm import AlertRow, AlertRuleRow
from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import AppUserRow, OrganizationRow
from techcamp.notifications.adapters.orm import NotificationRow, PushSubscriptionRow
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)


async def _create_test_context(
    db_session: AsyncSession,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    org_id = uuid7()
    org = OrganizationRow(id=org_id, name="Org Test", kind="individual")
    db_session.add(org)
    await db_session.commit()

    user_id = uuid7()
    user = AppUserRow(
        id=user_id,
        phone="+573001234567",
        full_name="Productor Test",
    )
    db_session.add(user)
    await db_session.commit()

    farm_id = uuid7()
    farm = FarmRow(
        id=farm_id,
        org_id=org_id,
        name="Finca Test",
        municipality_code="47001",
        location=_POINT,
    )
    db_session.add(farm)
    await db_session.commit()

    plot_id = uuid7()
    plot = PlotRow(
        id=plot_id,
        org_id=org_id,
        farm_id=farm_id,
        name="Lote 1",
        boundary=_BOUNDARY,
        irrigation_system="drip",
    )
    db_session.add(plot)
    await db_session.commit()

    result = await db_session.execute(
        select(AlertRuleRow).where(AlertRuleRow.code == "heat_stress")
    )
    rule_id = result.scalar_one().id

    alert_id = uuid7()
    alert = AlertRow(
        id=alert_id,
        org_id=org_id,
        rule_id=rule_id,
        plot_id=plot_id,
        node_id=None,
        state="open",
        severity="warning",
        opened_at=datetime.now(UTC),
    )
    db_session.add(alert)
    await db_session.commit()

    return org_id, user_id, alert_id


async def test_push_subscription_endpoint_is_unique(db_session: AsyncSession) -> None:
    """push_subscription.endpoint must be unique across all subscriptions."""
    user = AppUserRow(id=uuid7(), phone="+573007654321", full_name="User 1")
    user2 = AppUserRow(id=uuid7(), phone="+573007654322", full_name="User 2")
    db_session.add_all([user, user2])
    await db_session.commit()

    sub1 = PushSubscriptionRow(
        id=uuid7(),
        user_id=user.id,
        endpoint="https://updates.push.services.mozilla.com/wpush/v2/gAAAAAB...",
        keys={"p256dh": "key1", "auth": "auth1"},
        created_at=datetime.now(UTC),
    )
    db_session.add(sub1)
    await db_session.commit()

    sub2 = PushSubscriptionRow(
        id=uuid7(),
        user_id=user2.id,
        endpoint="https://updates.push.services.mozilla.com/wpush/v2/gAAAAAB...",
        keys={"p256dh": "key2", "auth": "auth2"},
        created_at=datetime.now(UTC),
    )
    db_session.add(sub2)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_notification_creation_and_defaults(db_session: AsyncSession) -> None:
    """Notification row creates with default pending status and attempts=0."""
    _org_id, user_id, alert_id = await _create_test_context(db_session)

    notif = NotificationRow(
        id=uuid7(),
        alert_id=alert_id,
        user_id=user_id,
        channel="push",
        next_attempt_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )
    db_session.add(notif)
    await db_session.commit()

    result = await db_session.execute(select(NotificationRow).where(NotificationRow.id == notif.id))
    saved = result.scalar_one()
    assert saved.status == "pending"
    assert saved.attempts == 0
    assert saved.sent_at is None
    assert saved.last_error is None


async def test_notification_channel_and_status_checks(db_session: AsyncSession) -> None:
    """channel must be push/sms/whatsapp, status must be pending/sent/failed."""
    _org_id, user_id, alert_id = await _create_test_context(db_session)

    # Invalid channel
    notif_bad_channel = NotificationRow(
        id=uuid7(),
        alert_id=alert_id,
        user_id=user_id,
        channel="email",
        next_attempt_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )
    db_session.add(notif_bad_channel)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    # Invalid status
    notif_bad_status = NotificationRow(
        id=uuid7(),
        alert_id=alert_id,
        user_id=user_id,
        channel="push",
        status="delivered",
        next_attempt_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )
    db_session.add(notif_bad_status)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()
