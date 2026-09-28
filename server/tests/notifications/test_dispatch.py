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
from techcamp.notifications.adapters.circuits import InProcessProviderCircuits
from techcamp.notifications.adapters.jobs import dispatch_outbox
from techcamp.notifications.adapters.orm import NotificationRow
from techcamp.notifications.adapters.outbox import SqlAlchemyOutboxRepository
from techcamp.notifications.adapters.repositories import SqlAlchemyNotificationRepository
from techcamp.notifications.adapters.senders import SeminarSmsSender, build_senders
from techcamp.notifications.adapters.subscriptions import SqlAlchemyPushSubscriptionRepository
from techcamp.notifications.application import (
    NotificationSender,
    dispatch_due_notifications,
)
from techcamp.notifications.domain.errors import NoPushSubscriptionError
from techcamp.notifications.domain.models import (
    BREAKER_COOLDOWN,
    BREAKER_FAILURE_THRESHOLD,
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


class _LandingSender:
    """A provider that answers."""

    def __init__(self) -> None:
        self.sent: list[UUID] = []

    async def send(self, notification: PendingNotification) -> None:
        self.sent.append(notification.id)


class _Clock:
    """A monotonic clock a test moves by hand: a five-minute cooldown is not a
    five-minute test."""

    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


def _circuits(clock: _Clock) -> InProcessProviderCircuits:
    """Circuits whose cooldown a test can expire without waiting for it."""
    return InProcessProviderCircuits(clock=clock)


def _open(circuits: InProcessProviderCircuits, channel: Channel) -> None:
    """The state docs/06 §4 reaches after five consecutive failures."""
    for _ in range(BREAKER_FAILURE_THRESHOLD):
        circuits.record_failure(channel)


@pytest.fixture
def circuits() -> InProcessProviderCircuits:
    """Circuits that have never seen a failure, which is what every test that is
    not about the breaker wants. A fresh one per test so the failures another test
    invents never open this one's channel."""
    return InProcessProviderCircuits(clock=_Clock())


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


def _senders(session: AsyncSession) -> dict[Channel, NotificationSender]:
    """Whatever this profile registers, which in the test session is the seminar
    SMS/WhatsApp pair and no `push`: `TECHCAMP_VAPID_PRIVATE_KEY` is unset, and
    an unset key leaves the channel unregistered rather than failing every send
    (D31, D32)."""
    return build_senders(SqlAlchemyPushSubscriptionRepository(session))


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

    claimed = await _outbox(db_session).claim_due(
        now=_DUE + timedelta(minutes=5), channels=[Channel.SMS]
    )

    assert [row.id for row in claimed] == [sms.id, later.id]


async def test_claim_due_stops_at_the_batch_limit(db_session: AsyncSession, seeded: Seeded) -> None:
    """One pass takes 50 rows (docs/06 §4 `LIMIT 50`); the sweep takes the rest
    on a later pass rather than holding a worker on an unbounded batch."""
    for _ in range(3):
        await _pending_row(db_session, seeded, channel=Channel.SMS)

    claimed = await _outbox(db_session).claim_due(now=_DUE, channels=[Channel.SMS], limit=2)

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
        held = await _outbox(other_session).claim_due(now=_DUE, channels=[Channel.SMS], limit=1)

        claimed = await _outbox(db_session).claim_due(now=_DUE, channels=[Channel.SMS])

    assert [row.id for row in held] == [first.id]
    assert [row.id for row in claimed] == [row.id for row in free]


async def test_hold_takes_only_a_row_that_is_still_pending_and_due(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """docs/06 §4: the claim's lock over a whole batch dies with that
    transaction, so the second lock has to check the row is STILL `pending` and
    due. Holding by id alone let a row a second worker finished in that window be
    sent again."""
    finished = await _pending_row(db_session, seeded, channel=Channel.SMS)
    finished.status = "sent"
    finished.sent_at = _DUE
    early = await _pending_row(
        db_session, seeded, channel=Channel.SMS, due_at=_DUE + timedelta(minutes=5)
    )
    due = await _pending_row(db_session, seeded, channel=Channel.SMS)
    await db_session.commit()
    outbox = _outbox(db_session)

    assert await outbox.hold(finished.id, now=_DUE) is False
    assert await outbox.hold(early.id, now=_DUE) is False
    assert await outbox.hold(due.id, now=_DUE) is True


async def test_a_non_critical_row_waits_while_its_channel_circuit_is_open(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """docs/06 §4's "Por proveedor. Con 5 fallos seguidos se abre 5 min" reaches the
    outbox: while the circuit is open a due row is not sent and does not cost an
    attempt. docs/06 §4 answers a critical by moving it to another channel, and
    says nothing about a warning — there is no other place a warning can go (D5),
    so the only honest thing left is to wait for the provider."""
    row = await _pending_row(db_session, seeded, channel=Channel.PUSH)
    circuits = _circuits(_Clock())
    _open(circuits, Channel.PUSH)
    sender = _FailingSender()

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.PUSH: sender},
        circuits=circuits,
        now=_DUE,
    )

    assert (report.claimed, report.deferred, report.sent, report.retried) == (1, 1, 0, 0)
    # The negative half: the provider is not called at all, so the row costs
    # nothing — a refused call would be an attempt a retry cannot fix.
    assert sender.sent == []
    held = await _row(db_session, row.id)
    assert (held.status, held.attempts) == ("pending", 0)
    assert held.next_attempt_at == _DUE + BREAKER_COOLDOWN
    assert held.last_error == "the push circuit is open"


async def test_five_failed_deliveries_open_the_circuit_and_the_next_one_is_not_attempted(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """The threshold is counted by the dispatcher, not configured into it: the
    fifth failed delivery is what opens the circuit, and the row behind it waits.
    That the SIXTH is the one held is the negative half — one failure early would
    silence a provider that is answering."""
    ids = [(await _pending_row(db_session, seeded, channel=Channel.PUSH)).id for _ in range(6)]
    sender = _FailingSender()
    circuits = _circuits(_Clock())

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.PUSH: sender},
        circuits=circuits,
        now=_DUE,
    )

    assert (report.retried, report.deferred) == (5, 1)
    assert sender.sent == ids[:5]
    assert (await _row(db_session, ids[4])).attempts == 1
    sixth = await _row(db_session, ids[5])
    assert (sixth.status, sixth.attempts) == ("pending", 0)


async def test_a_row_with_no_browser_does_not_open_the_push_circuit(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """docs/06 §4 counts a PROVIDER's failures, and a user who never enabled push
    is not the push service being down (D36). If those rows counted, five farmers
    without a browser would silence push for every other farmer and start moving
    criticals to another channel for no reason at all."""
    ids = [(await _pending_row(db_session, seeded, channel=Channel.PUSH)).id for _ in range(6)]
    sender = _FailingSender(error=NoPushSubscriptionError(seeded.user_id))
    circuits = _circuits(_Clock())

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.PUSH: sender},
        circuits=circuits,
        now=_DUE,
    )

    assert (report.deferred, report.retried) == (0, 6)
    assert sender.sent == ids
    assert circuits.allows(Channel.PUSH) is True


async def test_the_delivery_after_the_cooldown_is_attempted_and_a_landing_reopens_the_channel(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """Five minutes later the provider gets one trial: the row is sent, and a
    delivery that lands closes the circuit so the next row is not held behind a
    stale outage. The negative half is `deferred == 0` — a circuit that never
    reopens would strand every later row forever."""
    clock = _Clock()
    circuits = _circuits(clock)
    _open(circuits, Channel.PUSH)
    row = await _pending_row(db_session, seeded, channel=Channel.PUSH)
    sender = _LandingSender()
    clock.advance(BREAKER_COOLDOWN.total_seconds())

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.PUSH: sender},
        circuits=circuits,
        now=_DUE + BREAKER_COOLDOWN,
    )

    assert (report.deferred, report.sent) == (0, 1)
    assert sender.sent == [row.id]
    assert circuits.allows(Channel.PUSH) is True


async def test_a_circuit_opened_for_one_channel_leaves_the_others_alone(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """ "Por proveedor": a push service that is down is not evidence about the SMS
    provider, and a fallback that silenced both channels at once would answer an
    outage with no notifications at all."""
    push = await _pending_row(db_session, seeded, channel=Channel.PUSH)
    sms = await _pending_row(db_session, seeded, channel=Channel.SMS)
    circuits = _circuits(_Clock())
    _open(circuits, Channel.PUSH)
    landing = _LandingSender()

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.PUSH: _FailingSender(), Channel.SMS: landing},
        circuits=circuits,
        now=_DUE,
    )

    assert (report.deferred, report.sent) == (1, 1)
    assert landing.sent == [sms.id]
    assert (await _row(db_session, push.id)).status == "pending"


async def test_a_due_sms_row_is_sent_and_marked_sent(
    db_session: AsyncSession, seeded: Seeded, circuits: InProcessProviderCircuits
) -> None:
    """D8: the seminar adapter only logs, and the row is `sent` afterwards."""
    row = await _pending_row(db_session, seeded, channel=Channel.SMS)

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders=_senders(db_session),
        circuits=circuits,
        now=_DUE,
    )

    assert (report.claimed, report.sent, report.retried, report.failed) == (1, 1, 0, 0)
    sent = await _row(db_session, row.id)
    assert (sent.status, sent.sent_at) == ("sent", _DUE)


async def test_a_deferred_row_waits_without_spending_an_attempt(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """A row held for a reason that is not the provider's fault — the circuit is
    open, the hour is quiet — is not an attempt (D31's rule about a row nobody can
    deliver yet, applied to a row that can). It stays `pending`, keeps its
    attempts, and says why it did not move."""
    row = await _pending_row(db_session, seeded, channel=Channel.SMS)
    outbox = _outbox(db_session)
    due_again = _DUE + timedelta(minutes=5)

    await outbox.mark_deferred(row.id, next_attempt_at=due_again, reason="push circuit is open")

    held = await _row(db_session, row.id)
    assert (held.status, held.attempts) == ("pending", 0)
    assert held.next_attempt_at == due_again
    assert held.last_error == "push circuit is open"
    # The negative half: a deferred row is still claimable the moment it is due
    # again, which a `failed` row would not be.
    assert [held.id for held in await outbox.claim_due(now=due_again, channels=[Channel.SMS])] == [
        row.id
    ]


async def test_a_failing_sender_schedules_the_next_attempt_with_backoff(
    db_session: AsyncSession, seeded: Seeded, circuits: InProcessProviderCircuits
) -> None:
    """A transient provider error costs one attempt and a 1 min wait, not the row."""
    row = await _pending_row(db_session, seeded, channel=Channel.SMS)

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.SMS: _FailingSender()},
        circuits=circuits,
        now=_DUE,
    )

    assert (report.claimed, report.retried, report.failed) == (1, 1, 0)
    backed_off = await _row(db_session, row.id)
    assert backed_off.status == "pending"
    assert backed_off.attempts == 1
    assert backed_off.next_attempt_at == _DUE + timedelta(minutes=1)
    assert backed_off.last_error == "provider unreachable"


async def test_the_fifth_failed_attempt_gives_the_row_up(
    db_session: AsyncSession, seeded: Seeded, circuits: InProcessProviderCircuits
) -> None:
    """docs/06 §4: "máximo 5 intentos, luego `failed`" — the row stops being due,
    so a provider that never recovers cannot keep the sweep busy forever."""
    row = await _pending_row(db_session, seeded, channel=Channel.SMS)
    row.attempts = MAX_ATTEMPTS - 1
    await db_session.commit()

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.SMS: _FailingSender()},
        circuits=circuits,
        now=_DUE,
    )

    assert (report.retried, report.failed) == (0, 1)
    given_up = await _row(db_session, row.id)
    assert (given_up.status, given_up.attempts) == ("failed", MAX_ATTEMPTS)


async def test_a_row_with_no_sender_yet_is_not_even_claimed(
    db_session: AsyncSession, seeded: Seeded, circuits: InProcessProviderCircuits
) -> None:
    """A row nobody can send must not burn its five attempts: the claim does not
    ask for a channel with no registered sender, so the row keeps its status, its
    attempts and its due time for the adapter that will send it. `push` is that
    case here, because this session configures no VAPID key (D31, D32)."""
    row = await _pending_row(db_session, seeded, channel=Channel.PUSH)

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.SMS: SeminarSmsSender()},
        circuits=circuits,
        now=_DUE,
    )

    assert (report.claimed, report.sent) == (0, 0)
    untouched = await _row(db_session, row.id)
    assert (untouched.status, untouched.attempts, untouched.next_attempt_at) == (
        "pending",
        0,
        _DUE,
    )


async def test_a_backed_off_row_waits_for_its_next_attempt(
    db_session: AsyncSession, seeded: Seeded, circuits: InProcessProviderCircuits
) -> None:
    """A row scheduled inside a batch is not sent again in the same pass, and the
    pass after it finds it once its `next_attempt_at` has come."""
    row = await _pending_row(db_session, seeded, channel=Channel.SMS)
    sender = _FailingSender()

    await dispatch_due_notifications(
        outbox=_outbox(db_session), senders={Channel.SMS: sender}, circuits=circuits, now=_DUE
    )
    await dispatch_due_notifications(
        outbox=_outbox(db_session), senders={Channel.SMS: sender}, circuits=circuits, now=_DUE
    )
    await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.SMS: sender},
        circuits=circuits,
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
    claimed = await _outbox(db_session).claim_due(now=_DUE, channels=[Channel.WHATSAPP])

    with caplog.at_level(logging.INFO, logger="techcamp.notifications.adapters.senders"):
        for notification in claimed:
            await sender.send(notification)

    assert "whatsapp" in caplog.text
    assert "water_stress" in caplog.text


async def test_production_registers_no_simulated_sender(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0016: the real SMS/WhatsApp provider is future work, so the production
    profile registers nothing rather than pretending a message was delivered.
    Web Push is the exception and has its own test in `test_push_sender`: it is
    real in both profiles (ADR-0021:26)."""
    monkeypatch.setattr(senders_module, "is_seminar_profile", lambda: False)
    monkeypatch.setattr(senders_module, "vapid_private_key", lambda: None)

    assert _senders(db_session) == {}


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


async def test_hold_refuses_a_row_another_worker_already_holds(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """The claim's locks all end with the first row's commit, so a row takes its
    own lock right before it is sent — and `SKIP LOCKED` means a row another
    worker got in between is simply not ours to send."""
    free = await _pending_row(db_session, seeded, channel=Channel.SMS)
    taken = await _pending_row(db_session, seeded, channel=Channel.SMS)
    outbox = _outbox(db_session)

    async with async_session_factory() as other_session:
        await other_session.execute(
            text("SELECT id FROM notification WHERE id = :id FOR UPDATE"), {"id": taken.id}
        )

        assert await outbox.hold(free.id, now=_DUE) is True
        assert await outbox.hold(taken.id, now=_DUE) is False


async def test_a_row_the_hold_refuses_is_passed_over_and_not_sent(
    db_session: AsyncSession,
    seeded: Seeded,
    circuits: InProcessProviderCircuits,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the same promise: a refused hold costs no attempt and
    no send, so the row stays for the worker that does hold it."""
    first = await _pending_row(db_session, seeded, channel=Channel.SMS)
    second = await _pending_row(db_session, seeded, channel=Channel.SMS)
    sender = _FailingSender(RuntimeError("the first row fails, which commits"))
    outbox = _outbox(db_session)
    real_hold = outbox.hold

    async def _hold(notification_id: UUID, *, now: datetime) -> bool:
        return await real_hold(notification_id, now=now) and notification_id == first.id

    monkeypatch.setattr(outbox, "hold", _hold)

    report = await dispatch_due_notifications(
        outbox=outbox, senders={Channel.SMS: sender}, circuits=circuits, now=_DUE
    )

    assert (report.skipped, report.retried) == (1, 1)
    assert sender.sent == [first.id]
    assert (await _row(db_session, second.id)).attempts == 0


async def test_a_backlog_larger_than_one_batch_is_drained_in_one_run(
    db_session: AsyncSession, seeded: Seeded, circuits: InProcessProviderCircuits
) -> None:
    """RNF-05 wants a critical inside p95 2 min. One pass takes 50 rows, so a
    backlog is drained pass after pass instead of waiting for the next minute.
    """
    for _ in range(3):
        await _pending_row(db_session, seeded, channel=Channel.SMS)

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders=_senders(db_session),
        circuits=circuits,
        now=_DUE,
        limit=2,
    )

    assert report.sent == 3


async def test_a_backlog_of_undeliverable_rows_cannot_starve_a_deliverable_one(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """A `push` backlog waits for a VAPID key, and while it waits it must not fill every
    claim: the claim only asks for the channels that have a sender."""
    for _ in range(3):
        await _pending_row(db_session, seeded, channel=Channel.PUSH)
    sms = await _pending_row(db_session, seeded, channel=Channel.SMS)

    claimed = await _outbox(db_session).claim_due(now=_DUE, channels=[Channel.SMS], limit=50)

    assert [row.id for row in claimed] == [sms.id]
