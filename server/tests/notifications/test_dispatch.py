"""The outbox dispatcher: claim, retry with backoff, max attempts, and the
seminar SMS adapter (docs/06-diseno-detallado.md §4; ADR-0016, ADR-0021; D7, D8).

Against real Postgres, because the claim is a promise only the database can
keep: `FOR UPDATE SKIP LOCKED` is what lets two workers drain the same table
without sending anything twice, and a test double would happily agree to a
claim the real query would refuse.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.orm import AlertRow, AlertRuleRow
from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import AppUserRow, OrganizationRow
from techcamp.notifications.adapters import senders as senders_module
from techcamp.notifications.adapters.jobs import dispatch_outbox
from techcamp.notifications.adapters.orm import NotificationRow
from techcamp.notifications.adapters.outbox import SqlAlchemyOutboxRepository
from techcamp.notifications.adapters.repositories import SqlAlchemyNotificationRepository
from techcamp.notifications.adapters.senders import SeminarSmsSender, build_senders
from techcamp.notifications.application import dispatch_due_notifications
from techcamp.notifications.domain.models import (
    MAX_ATTEMPTS,
    Channel,
    NotificationDraft,
    PendingNotification,
    retry_delay,
)
from techcamp.shared.db import async_session_factory
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)
_DUE = datetime(2026, 9, 27, 15, 0, tzinfo=UTC)  # 10:00 Bogotá, outside quiet hours


class _FailingSender:
    """A provider that is down, the way Open-Meteo is in `test_open_meteo_adapter`."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error or RuntimeError("provider unreachable")
        self.sent: list[UUID] = []

    async def send(self, notification: PendingNotification) -> None:
        self.sent.append(notification.id)
        raise self.error


@pytest.fixture(autouse=True)
def _seminar(monkeypatch: pytest.MonkeyPatch) -> None:
    """The profile decides which senders exist, and the test session must not
    inherit it from the environment (ADR-0021)."""
    monkeypatch.setattr(senders_module, "is_seminar_profile", lambda: True)


@dataclass(frozen=True, slots=True)
class Seeded:
    """One organization's recipient and its open plot alert, the two rows every
    outbox row here hangs off."""

    user_id: UUID
    alert_id: UUID


async def _seed_alert(session: AsyncSession) -> Seeded:
    """An organization, its one recipient and one open plot alert to hang rows off."""
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
    alert_id = uuid7()
    session.add(
        AlertRow(
            id=alert_id,
            org_id=org_id,
            rule_id=rule_id,
            plot_id=plot_id,
            state="open",
            severity="warning",
            opened_at=_DUE,
        )
    )
    await session.commit()
    return Seeded(user_id=user_id, alert_id=alert_id)


@pytest.fixture
async def seeded(db_session: AsyncSession) -> Seeded:
    """One alert per test, so a test that writes three rows does not end up
    comparing ids across three organizations' alerts."""
    return await _seed_alert(db_session)


async def _pending_row(
    session: AsyncSession, seeded: Seeded, *, channel: Channel, due_at: datetime = _DUE
) -> NotificationRow:
    """One `pending` outbox row on the seeded alert."""
    row = NotificationRow(
        id=uuid7(),
        alert_id=seeded.alert_id,
        user_id=seeded.user_id,
        channel=channel.value,
        status="pending",
        attempts=0,
        next_attempt_at=due_at,
        created_at=_DUE,
    )
    session.add(row)
    await session.commit()
    return row


async def _row(session: AsyncSession, notification_id: UUID) -> NotificationRow:
    # The dispatcher writes its outcome on its own session, so the row this
    # session still holds in its identity map has to be dropped first: a query
    # for a row already in the map hands back the cached object, attributes and
    # all (`expire_on_commit=False`).
    session.expire_all()
    return (
        await session.execute(select(NotificationRow).where(NotificationRow.id == notification_id))
    ).scalar_one()


def _outbox(session: AsyncSession) -> SqlAlchemyOutboxRepository:
    return SqlAlchemyOutboxRepository(session)


def _draft(seeded: Seeded) -> NotificationDraft:
    return NotificationDraft(user_id=seeded.user_id, channel=Channel.SMS, next_attempt_at=_DUE)


async def _job_count(session: AsyncSession) -> int:
    return (await session.execute(text("SELECT count(*) FROM procrastinate_jobs"))).scalar_one()


async def test_retry_delays_follow_the_documented_backoff() -> None:
    """docs/06 §4: "Backoff exponencial: 1 min, 5 min, 30 min, 2 h"."""
    assert [retry_delay(attempt) for attempt in range(1, MAX_ATTEMPTS)] == [
        timedelta(minutes=1),
        timedelta(minutes=5),
        timedelta(minutes=30),
        timedelta(hours=2),
    ]


async def test_claim_due_takes_the_due_pending_rows_in_due_order(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """The claim is exactly docs/06 §4's SELECT: due, pending, oldest first."""
    sms = await _pending_row(db_session, seeded, channel=Channel.SMS)
    later = await _pending_row(
        db_session, seeded, channel=Channel.SMS, due_at=_DUE + timedelta(minutes=5)
    )
    await _pending_row(db_session, seeded, channel=Channel.SMS, due_at=_DUE + timedelta(days=1))
    delivered = await _pending_row(db_session, seeded, channel=Channel.SMS)
    delivered.status = "sent"
    delivered.sent_at = _DUE
    await db_session.commit()

    claimed = await _outbox(db_session).claim_due(now=_DUE + timedelta(minutes=5))

    assert [row.id for row in claimed] == [sms.id, later.id]


async def test_claim_due_stops_at_the_batch_limit(db_session: AsyncSession, seeded: Seeded) -> None:
    """One pass takes 50 rows (docs/06 §4 `LIMIT 50`); the sweep takes the rest
    on a later pass rather than holding a worker on an unbounded batch."""
    for _ in range(3):
        await _pending_row(db_session, seeded, channel=Channel.SMS)

    claimed = await _outbox(db_session).claim_due(now=_DUE, limit=2)

    assert len(claimed) == 2


async def test_claim_due_skips_a_row_another_worker_is_sending(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """`SKIP LOCKED` is what keeps two workers from sending the same alert twice:
    the row the other worker holds is passed over, the rest are still claimed."""
    first = await _pending_row(db_session, seeded, channel=Channel.SMS)
    free = [await _pending_row(db_session, seeded, channel=Channel.SMS) for _ in range(2)]

    async with async_session_factory() as other_session:
        # The claim's locks live until that session commits or rolls back, so
        # while it is open `first` is invisible to any other claim.
        held = await _outbox(other_session).claim_due(now=_DUE, limit=1)

        claimed = await _outbox(db_session).claim_due(now=_DUE)

    assert [row.id for row in held] == [first.id]
    assert [row.id for row in claimed] == [row.id for row in free]


async def test_a_due_sms_row_is_sent_and_marked_sent(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """D8: the seminar adapter only logs, and the row is `sent` afterwards."""
    row = await _pending_row(db_session, seeded, channel=Channel.SMS)

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session), senders=build_senders(), now=_DUE
    )

    assert (report.claimed, report.sent, report.retried, report.failed) == (1, 1, 0, 0)
    sent = await _row(db_session, row.id)
    assert (sent.status, sent.sent_at) == ("sent", _DUE)


async def test_a_failing_sender_schedules_the_next_attempt_with_backoff(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """A transient provider error costs one attempt and a 1 min wait, not the row."""
    row = await _pending_row(db_session, seeded, channel=Channel.SMS)

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session), senders={Channel.SMS: _FailingSender()}, now=_DUE
    )

    assert (report.claimed, report.retried, report.failed) == (1, 1, 0)
    backed_off = await _row(db_session, row.id)
    assert backed_off.status == "pending"
    assert backed_off.attempts == 1
    assert backed_off.next_attempt_at == _DUE + timedelta(minutes=1)
    assert backed_off.last_error == "provider unreachable"


async def test_the_fifth_failed_attempt_gives_the_row_up(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """docs/06 §4: "máximo 5 intentos, luego `failed`" — the row stops being due,
    so a provider that never recovers cannot keep the sweep busy forever."""
    row = await _pending_row(db_session, seeded, channel=Channel.SMS)
    row.attempts = MAX_ATTEMPTS - 1
    await db_session.commit()

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session), senders={Channel.SMS: _FailingSender()}, now=_DUE
    )

    assert (report.retried, report.failed) == (0, 1)
    given_up = await _row(db_session, row.id)
    assert (given_up.status, given_up.attempts) == ("failed", MAX_ATTEMPTS)


async def test_a_row_with_no_sender_yet_is_left_alone(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """`push` has no sender until T7b, and a row nobody can send must not burn
    its five attempts: it stays pending, with the same due time, for the adapter
    that will send it."""
    row = await _pending_row(db_session, seeded, channel=Channel.PUSH)

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session), senders={Channel.SMS: SeminarSmsSender()}, now=_DUE
    )

    assert (report.claimed, report.deferred) == (1, 1)
    untouched = await _row(db_session, row.id)
    assert (untouched.status, untouched.attempts, untouched.next_attempt_at) == (
        "pending",
        0,
        _DUE,
    )


async def test_a_backed_off_row_waits_for_its_next_attempt(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """A row scheduled inside a batch is not sent again in the same pass, and the
    pass after it finds it once its `next_attempt_at` has come."""
    row = await _pending_row(db_session, seeded, channel=Channel.SMS)
    sender = _FailingSender()

    await dispatch_due_notifications(
        outbox=_outbox(db_session), senders={Channel.SMS: sender}, now=_DUE
    )
    await dispatch_due_notifications(
        outbox=_outbox(db_session), senders={Channel.SMS: sender}, now=_DUE
    )
    await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.SMS: sender},
        now=_DUE + timedelta(minutes=1),
    )

    assert sender.sent == [row.id, row.id]
    assert (await _row(db_session, row.id)).attempts == 2


async def test_the_seminar_sender_logs_the_sms_it_pretends_to_send(
    db_session: AsyncSession, seeded: Seeded, caplog: pytest.LogCaptureFixture
) -> None:
    """ADR-0021: the simulated provider is a log line and the `/dev/outbox` tray."""
    await _pending_row(db_session, seeded, channel=Channel.WHATSAPP)
    sender = SeminarSmsSender()
    claimed = await _outbox(db_session).claim_due(now=_DUE)

    with caplog.at_level(logging.INFO, logger="techcamp.notifications.adapters.senders"):
        for notification in claimed:
            await sender.send(notification)

    assert "whatsapp" in caplog.text
    assert "water_stress" in caplog.text


async def test_production_registers_no_simulated_sender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0016: the real SMS/WhatsApp provider is future work, so the production
    profile registers nothing rather than pretending a message was delivered."""
    monkeypatch.setattr(senders_module, "is_seminar_profile", lambda: False)

    assert build_senders() == {}


async def test_writing_outbox_rows_defers_the_dispatch_job_in_the_same_transaction(
    db_session: AsyncSession,
) -> None:
    """D7: the alert's own transaction enqueues the dispatch, so a committed alert
    always has a job behind it."""
    seeded = await _seed_alert(db_session)
    await db_session.execute(text("DELETE FROM procrastinate_jobs"))
    await db_session.commit()
    notifications = SqlAlchemyNotificationRepository(db_session)

    await notifications.insert_drafts(seeded.alert_id, [_draft(seeded)])
    await db_session.commit()

    jobs = await db_session.execute(
        text("SELECT task_name, queue_name FROM procrastinate_jobs ORDER BY id")
    )
    assert [tuple(row) for row in jobs] == [("notifications.dispatch_outbox", "notifications")]


async def test_a_rolled_back_outbox_write_leaves_no_dispatch_job(
    db_session: AsyncSession,
) -> None:
    """The other half of D7's same-transaction guarantee: no alert, no row, no job."""
    seeded = await _seed_alert(db_session)
    await db_session.execute(text("DELETE FROM procrastinate_jobs"))
    await db_session.commit()
    notifications = SqlAlchemyNotificationRepository(db_session)

    await notifications.insert_drafts(seeded.alert_id, [_draft(seeded)])
    await db_session.rollback()

    assert await _job_count(db_session) == 0


async def test_a_second_alert_while_a_dispatch_waits_does_not_queue_another_job(
    db_session: AsyncSession,
) -> None:
    """The queueing lock is global, so a second alert written while a dispatch is
    already waiting needs nothing: `procrastinate_jobs_queueing_lock_idx_v1`
    refuses the duplicate inside a savepoint, the caller's transaction survives,
    and the waiting job will find that row too."""
    seeded = await _seed_alert(db_session)
    await db_session.execute(text("DELETE FROM procrastinate_jobs"))
    await db_session.commit()
    notifications = SqlAlchemyNotificationRepository(db_session)

    await notifications.insert_drafts(seeded.alert_id, [_draft(seeded)])
    await db_session.commit()
    await notifications.insert_drafts(seeded.alert_id, [_draft(seeded)])
    await db_session.commit()

    assert await _job_count(db_session) == 1
    assert (await db_session.execute(text("SELECT count(*) FROM notification"))).scalar_one() == 2


async def test_the_dispatch_job_sends_the_due_rows(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """The task the outbox write defers and the minute sweep runs (D7) drains
    the outbox through the senders the profile registers."""
    # The job sends what is due *now*, so the row cannot be dated in the past.
    row = await _pending_row(
        db_session, seeded, channel=Channel.SMS, due_at=datetime.now(UTC) - timedelta(seconds=1)
    )

    await dispatch_outbox(timestamp=0)

    assert (await _row(db_session, row.id)).status == "sent"
