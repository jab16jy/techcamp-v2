"""The outbox dispatcher: claim, retry with backoff, max attempts, and the
seminar SMS adapter (docs/06-diseno-detallado.md §4; ADR-0016, ADR-0021; D7, D8).

Against real Postgres, because the claim is a promise only the database can
keep: `FOR UPDATE SKIP LOCKED` is what lets two workers drain the same table
without sending anything twice, and a test double would happily agree to a
claim the real query would refuse.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
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
from techcamp.telemetry.adapters.orm import NodeRow

pytestmark = pytest.mark.anyio

_POINT = "SRID=4326;POINT(-74.1 10.9)"
_BOUNDARY = (
    "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, -74.10 10.90))"
)
_DUE = datetime(2026, 9, 27, 15, 0, tzinfo=UTC)  # 10:00 Bogotá, outside quiet hours


class _FailingSender:
    """A provider that is down, the way Open-Meteo is in `test_open_meteo_adapter`.

    `messages` counts the CALLS, which is what grouping is about: a batch of
    grouped rows is one call, and `sent` names every row the call covered so a
    test can say which rows reached the provider.
    """

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error or RuntimeError("provider unreachable")
        self.sent: list[UUID] = []
        self.messages = 0

    async def send(self, notifications: Sequence[PendingNotification]) -> None:
        self.sent.extend(notification.id for notification in notifications)
        self.messages += 1
        raise self.error


class _LandingSender:
    """A provider that answers."""

    def __init__(self) -> None:
        self.sent: list[UUID] = []
        self.messages = 0

    async def send(self, notifications: Sequence[PendingNotification]) -> None:
        self.sent.extend(notification.id for notification in notifications)
        self.messages += 1


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
    """One organization's recipient, its two farms, and one open plot alert on
    the first of them — the rows every outbox row here hangs off.

    A SECOND farm and a node on it exist so the grouping tests have something to
    keep apart: docs/06 §4 groups by farm, so "the same farm" and "another farm"
    are two different messages and a test that only ever has one farm cannot tell
    a working grouping from a grouping that ignores the farm entirely.
    """

    user_id: UUID
    alert_id: UUID
    org_id: UUID
    farm_id: UUID
    plot_id: UUID
    other_farm_id: UUID
    other_plot_id: UUID
    node_id: UUID


async def _seed_alert(session: AsyncSession, *, severity: str = "warning") -> Seeded:
    """An organization, its one recipient, two farms and one open plot alert.

    The severity is a parameter because docs/06 §4's channels-by-severity rule
    makes it decide the row's whole future: a critical may move to another channel
    and a warning may not (D5, D37).
    """
    org_id, user_id = uuid7(), uuid7()
    session.add(AppUserRow(id=user_id, phone=f"+57{uuid7().int % 10**13:013d}"))
    session.add(OrganizationRow(id=org_id, name="Test Org", kind="individual"))
    await session.commit()
    farm_id, plot_id = uuid7(), uuid7()
    other_farm_id, other_plot_id = uuid7(), uuid7()
    node_id = uuid7()
    session.add(
        FarmRow(
            id=farm_id,
            org_id=org_id,
            name="Finca Principal",
            municipality_code="47001",
            location=_POINT,
        )
    )
    session.add(
        FarmRow(
            id=other_farm_id,
            org_id=org_id,
            name="Finca Norte",
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
    session.add(
        PlotRow(
            id=other_plot_id,
            org_id=org_id,
            farm_id=other_farm_id,
            name="Lote 2",
            boundary=_BOUNDARY,
            irrigation_system="drip",
        )
    )
    await session.commit()
    # A node on the SECOND farm, so the farm of a node alert is only reachable by
    # joining `node → plot → farm` (docs/03: `alert` stores a node id, not a farm).
    session.add(
        NodeRow(
            id=node_id,
            org_id=org_id,
            plot_id=other_plot_id,
            transport="cellular",
            dev_eui=f"eui-{node_id}",
            claim_code=f"claim-{node_id}",
            credential_hash="hash",
            interval_s=300,
            claimed_at=_DUE,
            status="online",
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
            severity=severity,
            opened_at=_DUE,
        )
    )
    await session.commit()
    return Seeded(
        user_id=user_id,
        alert_id=alert_id,
        org_id=org_id,
        farm_id=farm_id,
        plot_id=plot_id,
        other_farm_id=other_farm_id,
        other_plot_id=other_plot_id,
        node_id=node_id,
    )


async def _alert_on(
    session: AsyncSession,
    seeded: Seeded,
    *,
    code: str,
    plot_id: UUID | None = None,
    node_id: UUID | None = None,
) -> UUID:
    """A second open alert, on the seeded org's other plot or on a node.

    The rule is a parameter because the partial unique index allows only one
    non-resolved alert per (rule, target) (docs/06 §3), so two alerts about one
    plot have to be about two different rules — which is also the realistic case:
    a farm with a dry spell and a hot week has two alerts at once.
    """
    rule_id = (
        await session.execute(select(AlertRuleRow.id).where(AlertRuleRow.code == code))
    ).scalar_one()
    alert_id = uuid7()
    session.add(
        AlertRow(
            id=alert_id,
            org_id=seeded.org_id,
            rule_id=rule_id,
            plot_id=plot_id,
            node_id=node_id,
            state="open",
            severity="warning",
            opened_at=_DUE,
        )
    )
    await session.commit()
    return alert_id


async def _farms(session: AsyncSession, org_id: UUID, count: int) -> list[UUID]:
    """`count` more farms of one organization, and the plot of each.

    A row per farm is how a test gets `count` separate MESSAGES out of the
    dispatcher: docs/06 §4 groups by farm, so same-farm rows are one call and
    different-farm rows are one call each.
    """
    plot_ids: list[UUID] = []
    for index in range(count):
        farm_id, plot_id = uuid7(), uuid7()
        session.add(
            FarmRow(
                id=farm_id,
                org_id=org_id,
                name=f"Finca {index}",
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
                name=f"Lote {index}",
                boundary=_BOUNDARY,
                irrigation_system="drip",
            )
        )
        await session.commit()
        plot_ids.append(plot_id)
    return plot_ids


@pytest.fixture
async def seeded(db_session: AsyncSession) -> Seeded:
    """One alert per test, so a test that writes three rows does not end up
    comparing ids across three organizations' alerts."""
    return await _seed_alert(db_session)


@pytest.fixture
async def critical(db_session: AsyncSession) -> Seeded:
    """The same, at the severity that may change channel (docs/06 §4)."""
    return await _seed_alert(db_session, severity="critical")


async def _pending_row(
    session: AsyncSession,
    seeded: Seeded,
    *,
    channel: Channel,
    due_at: datetime = _DUE,
    alert_id: UUID | None = None,
) -> NotificationRow:
    """One `pending` outbox row on the seeded alert, or on a second one."""
    row = NotificationRow(
        id=uuid7(),
        alert_id=alert_id or seeded.alert_id,
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
    fifth failed DELIVERY is what opens the circuit, and the message behind it
    waits. That the SIXTH is the one held is the negative half — one failure early
    would silence a provider that is answering.

    One row per farm, so this is six messages and not one grouped one: the breaker
    counts what the provider refused to take, and a group is one refusal however
    many alerts it speaks for.
    """
    plot_ids = await _farms(db_session, seeded.org_id, 6)
    ids = [
        (
            await _pending_row(
                db_session,
                seeded,
                channel=Channel.PUSH,
                alert_id=await _alert_on(db_session, seeded, code="heat_stress", plot_id=plot_id),
            )
        ).id
        for plot_id in plot_ids
    ]
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


async def test_a_critical_goes_to_the_alternate_channel_while_push_is_down(
    db_session: AsyncSession, critical: Seeded
) -> None:
    """docs/06 §4: "mientras tanto las críticas pasan al canal alterno", and
    docs/01 RF-08 asks for "SMS o WhatsApp como respaldo para alertas críticas".
    The push service is down, so a critical that has no other way to be heard
    waits 5 minutes; a critical that has one goes there now."""
    row = await _pending_row(db_session, critical, channel=Channel.PUSH)
    circuits = _circuits(_Clock())
    _open(circuits, Channel.PUSH)
    push, sms = _FailingSender(), _LandingSender()

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.PUSH: push, Channel.SMS: sms},
        circuits=circuits,
        now=_DUE,
    )

    assert (report.sent, report.deferred) == (1, 0)
    assert sms.sent == [row.id]
    assert push.sent == [], "the push provider was called while its circuit was open"
    assert (await _row(db_session, row.id)).status == "sent"
    # The row is not rewritten: it is still the `push` row the alert's transaction
    # wrote (D34), and it is `sent` because the farmer did hear the alert — over
    # the channel that was left. The escalation to the technician two hours later
    # is T8's own row and a different recipient (D4), so nothing is sent twice.
    assert (await _row(db_session, row.id)).attempts == 0


async def test_a_critical_is_not_moved_by_a_channel_that_never_failed(
    db_session: AsyncSession, critical: Seeded
) -> None:
    """No evidence is not evidence of failure. A circuit that has seen nothing is
    closed, and a closed circuit must be left alone: moving a critical to SMS
    because nobody has called push yet would spend the escalation channel on the
    first outage that never comes, and RNF-05's two minutes would be spent on a
    provider that is answering."""
    row = await _pending_row(db_session, critical, channel=Channel.PUSH)
    circuits = _circuits(_Clock())
    push, sms = _LandingSender(), _LandingSender()

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.PUSH: push, Channel.SMS: sms},
        circuits=circuits,
        now=_DUE,
    )

    assert (report.sent, report.deferred) == (1, 0)
    assert push.sent == [row.id]
    assert sms.sent == []


async def test_a_critical_waits_when_no_other_channel_can_send(
    db_session: AsyncSession, critical: Seeded
) -> None:
    """In production the SMS/WhatsApp provider is future work (ADR-0016, D31), so
    a critical can face an open push circuit with nowhere to go. Holding it is the
    only answer that is not a lost alert: it stays `pending`, spends no attempt,
    and the row is there when push comes back. Marking it `failed`, or counting the
    refusal as an attempt, would turn a provider outage into a dead critical."""
    row = await _pending_row(db_session, critical, channel=Channel.PUSH)
    circuits = _circuits(_Clock())
    _open(circuits, Channel.PUSH)

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.PUSH: _FailingSender()},
        circuits=circuits,
        now=_DUE,
    )

    assert (report.deferred, report.sent, report.failed) == (1, 0, 0)
    held = await _row(db_session, row.id)
    assert (held.status, held.attempts) == ("pending", 0)
    assert held.next_attempt_at == _DUE + BREAKER_COOLDOWN


async def test_a_critical_does_not_fall_back_from_the_escalation_channel(
    db_session: AsyncSession, critical: Seeded
) -> None:
    """docs/06 §4's severity order is `warning` by push and `critical` by push
    then SMS/WhatsApp, and the alternate channel is the one AFTER the channel that
    is down. An escalated `sms` row is the last resort (D34: T8 writes it, D4:
    it goes to the technician), so falling back from it to push would turn the
    escalation back into the notification the farmer already got — the opposite of
    escalating, and a message to a browser that may not even be registered."""
    row = await _pending_row(db_session, critical, channel=Channel.SMS)
    circuits = _circuits(_Clock())
    _open(circuits, Channel.SMS)
    push = _LandingSender()

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.PUSH: push, Channel.SMS: _FailingSender()},
        circuits=circuits,
        now=_DUE,
    )

    assert (report.deferred, report.sent) == (1, 0)
    assert push.sent == []
    assert (await _row(db_session, row.id)).status == "pending"


async def test_a_critical_takes_the_first_alternate_whose_circuit_allows_it(
    db_session: AsyncSession, critical: Seeded
) -> None:
    """The alternates are tried in docs/06 §4's order and the first one that can
    actually send is the one used: an SMS provider that is down does not stop the
    critical, it moves it to WhatsApp."""
    row = await _pending_row(db_session, critical, channel=Channel.PUSH)
    circuits = _circuits(_Clock())
    _open(circuits, Channel.PUSH)
    _open(circuits, Channel.SMS)
    sms, whatsapp = _FailingSender(), _LandingSender()

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.PUSH: _FailingSender(), Channel.SMS: sms, Channel.WHATSAPP: whatsapp},
        circuits=circuits,
        now=_DUE,
    )

    assert (report.sent, report.deferred) == (1, 0)
    assert whatsapp.sent == [row.id]
    assert sms.sent == [], "an open circuit did not stop the SMS provider being used"


async def test_a_critical_waits_when_every_channel_is_refusing(
    db_session: AsyncSession, critical: Seeded
) -> None:
    """Both providers down is not "send it anyway". The row waits, and no circuit
    is touched by the refusal: no delivery was attempted through any of them, so
    there is no evidence about them either way."""
    row = await _pending_row(db_session, critical, channel=Channel.PUSH)
    circuits = _circuits(_Clock())
    for channel in (Channel.PUSH, Channel.SMS, Channel.WHATSAPP):
        _open(circuits, channel)
    whatsapp = _LandingSender()

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={
            Channel.PUSH: _FailingSender(),
            Channel.SMS: _FailingSender(),
            Channel.WHATSAPP: whatsapp,
        },
        circuits=circuits,
        now=_DUE,
    )

    assert (report.deferred, report.sent) == (1, 0)
    assert whatsapp.sent == []
    assert (await _row(db_session, row.id)).status == "pending"


async def test_the_due_non_critical_rows_of_one_farm_go_out_as_one_message(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """docs/06 §4 "Agrupación": "Varias alertas no críticas de la misma finca en
    15 min se envían en una sola notificación". The rows of one farm reach the
    provider as ONE call, and every one of them is closed by it."""
    other_alert = await _alert_on(db_session, seeded, code="heat_stress", plot_id=seeded.plot_id)
    ids = [
        (await _pending_row(db_session, seeded, channel=Channel.PUSH)).id,
        (await _pending_row(db_session, seeded, channel=Channel.PUSH, alert_id=other_alert)).id,
    ]
    sender = _LandingSender()

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.PUSH: sender},
        circuits=_circuits(_Clock()),
        now=_DUE,
    )

    assert (report.claimed, report.sent) == (2, 2)
    # The negative half, and the one that matters: ONE call, not one per row.
    assert sender.messages == 1
    assert (await _row(db_session, ids[0])).status == "sent"
    assert (await _row(db_session, ids[1])).status == "sent"


async def test_two_farms_of_one_user_are_two_notifications(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """ "de la misma finca": the farm is the grouping key, so one recipient's two
    farms do not become one message about a farm the message cannot name."""
    other_alert = await _alert_on(
        db_session, seeded, code="heat_stress", plot_id=seeded.other_plot_id
    )
    await _pending_row(db_session, seeded, channel=Channel.PUSH)
    await _pending_row(db_session, seeded, channel=Channel.PUSH, alert_id=other_alert)
    sender = _LandingSender()

    await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.PUSH: sender},
        circuits=_circuits(_Clock()),
        now=_DUE,
    )

    assert sender.messages == 2


async def test_two_node_alerts_of_one_farm_are_grouped(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """`alert` stores a `node_id`, never a farm (docs/03:284-297), so a node
    alert's farm is only reachable by joining `node → plot → farm`. Grouping that
    silently never happens is the failure this pins: the rows would each be their
    own message, which is the pre-grouping behaviour."""
    first_alert = await _alert_on(db_session, seeded, code="node_offline", node_id=seeded.node_id)
    second_alert = await _alert_on(
        db_session, seeded, code="node_battery_low", node_id=seeded.node_id
    )
    await _pending_row(db_session, seeded, channel=Channel.PUSH, alert_id=first_alert)
    await _pending_row(db_session, seeded, channel=Channel.PUSH, alert_id=second_alert)
    sender = _LandingSender()

    await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.PUSH: sender},
        circuits=_circuits(_Clock()),
        now=_DUE,
    )

    assert sender.messages == 1


async def test_a_critical_is_never_grouped_with_a_warning(
    db_session: AsyncSession, critical: Seeded
) -> None:
    """A critical is the severity RNF-05 gives two minutes, so it goes out on its
    own and is not made to wait behind a message it has nothing to do with."""
    other_alert = await _alert_on(
        db_session, critical, code="heat_stress", plot_id=critical.plot_id
    )
    await _pending_row(db_session, critical, channel=Channel.PUSH)
    await _pending_row(db_session, critical, channel=Channel.PUSH, alert_id=other_alert)
    sender = _LandingSender()

    await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.PUSH: sender},
        circuits=_circuits(_Clock()),
        now=_DUE,
    )

    assert sender.messages == 2


async def test_one_message_never_mixes_two_channels(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """A message goes out through one provider. Two rows of the same farm on
    different channels are two deliveries, even though the farm is the same."""
    await _pending_row(db_session, seeded, channel=Channel.PUSH)
    await _pending_row(db_session, seeded, channel=Channel.SMS)
    push, sms = _LandingSender(), _LandingSender()

    await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.PUSH: push, Channel.SMS: sms},
        circuits=_circuits(_Clock()),
        now=_DUE,
    )

    assert (push.messages, sms.messages) == (1, 1)


async def test_a_group_is_one_message_whose_failing_costs_every_row_an_attempt(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """The group is the message, so its outcome is the outcome of every row it
    covers: one failed delivery is one failed attempt for each of the alerts the
    farmer still has to hear about, and a retry re-sends them together."""
    other_alert = await _alert_on(db_session, seeded, code="heat_stress", plot_id=seeded.plot_id)
    ids = [
        (await _pending_row(db_session, seeded, channel=Channel.PUSH)).id,
        (await _pending_row(db_session, seeded, channel=Channel.PUSH, alert_id=other_alert)).id,
    ]

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.PUSH: _FailingSender()},
        circuits=_circuits(_Clock()),
        now=_DUE,
    )

    assert report.retried == 2
    assert (await _row(db_session, ids[0])).attempts == 1
    assert (await _row(db_session, ids[1])).attempts == 1


async def test_a_group_whose_provider_is_down_is_never_attempted(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """The circuit is asked before the group is formed, so a held group costs
    nothing — a refused call per row would spend a farm's evening of alerts on a
    provider that is not answering."""
    other_alert = await _alert_on(db_session, seeded, code="heat_stress", plot_id=seeded.plot_id)
    ids = [
        (await _pending_row(db_session, seeded, channel=Channel.PUSH)).id,
        (await _pending_row(db_session, seeded, channel=Channel.PUSH, alert_id=other_alert)).id,
    ]
    circuits = _circuits(_Clock())
    _open(circuits, Channel.PUSH)
    sender = _FailingSender()

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.PUSH: sender},
        circuits=circuits,
        now=_DUE,
    )

    assert (report.deferred, report.retried) == (2, 0)
    assert sender.sent == []
    assert (await _row(db_session, ids[0])).attempts == 0
    assert (await _row(db_session, ids[1])).attempts == 0


@pytest.mark.parametrize(
    ("now", "released"),
    [
        pytest.param(
            datetime(2026, 9, 27, 5, 0, tzinfo=UTC),  # 00:00 Bogotá on the 27th
            datetime(2026, 9, 27, 10, 0, tzinfo=UTC),
            id="midnight-in-bogota",
        ),
        pytest.param(
            datetime(2026, 9, 28, 3, 0, tzinfo=UTC),  # 22:00 Bogotá on the 27th
            datetime(2026, 9, 28, 10, 0, tzinfo=UTC),
            id="evening-in-bogota-utc-date-has-moved-on",
        ),
    ],
)
async def test_a_non_critical_row_that_comes_due_at_night_waits_for_0500_bogota(
    db_session: AsyncSession, seeded: Seeded, now: datetime, released: datetime
) -> None:
    """D6 applies the quiet hours when the row is WRITTEN, and that is not enough:
    a row's `next_attempt_at` is also written by a retry backoff and by the
    circuit's cooldown, and neither of those knows about 20:00. So the hour is
    checked again where the row actually leaves.

    One instant for each mistake a UTC implementation makes. 05:00 UTC is 00:00 in
    Bogotá: quiet in the product's clock, the middle of the afternoon on the UTC
    date, so a check that read the UTC HOUR would send this at midnight on a
    farmer's phone. 03:00 UTC on the 28th is 22:00 in Bogotá on the 27th: the UTC
    hour is quiet too, but the DATE has already rolled over, so a check that read
    it would hold the row to 28th 05:00 UTC — which is 00:00 in Bogotá, still the
    middle of the night.
    """
    row = await _pending_row(db_session, seeded, channel=Channel.PUSH, due_at=now)
    sender = _LandingSender()

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.PUSH: sender},
        circuits=_circuits(_Clock()),
        now=now,
    )

    assert (report.claimed, report.deferred, report.sent) == (1, 1, 0)
    assert sender.messages == 0, "a non-critical row was delivered in the quiet hours"
    held = await _row(db_session, row.id)
    assert (held.status, held.attempts) == ("pending", 0)
    assert held.next_attempt_at == released  # 05:00 Bogotá
    assert held.last_error == "the farm is in its quiet hours until 05:00"


async def test_a_row_that_is_only_quiet_in_utc_is_delivered(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """The other direction, because a check can be wrong both ways: 20:00 UTC is
    15:00 in Bogotá, so the afternoon is the middle of the "night" on the UTC
    clock. Reading UTC would silence a whole afternoon of alerts for five hours."""
    afternoon = datetime(2026, 9, 27, 20, 0, tzinfo=UTC)  # 15:00 Bogotá
    row = await _pending_row(db_session, seeded, channel=Channel.PUSH, due_at=afternoon)
    sender = _LandingSender()

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.PUSH: sender},
        circuits=_circuits(_Clock()),
        now=afternoon,
    )

    assert (report.sent, report.deferred) == (1, 0)
    assert (await _row(db_session, row.id)).status == "sent"


async def test_a_critical_is_delivered_at_midnight(
    db_session: AsyncSession, critical: Seeded
) -> None:
    """docs/06 §4 "Horas de silencio": "20:00–05:00: solo notificaciones críticas".
    A critical is the one severity that may break the silence, and it is the one
    RNF-05 gives two minutes, so the quiet hours never hold it."""
    at_night = datetime(2026, 9, 27, 5, 0, tzinfo=UTC)  # 00:00 Bogotá
    row = await _pending_row(db_session, critical, channel=Channel.PUSH, due_at=at_night)
    sender = _LandingSender()

    report = await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.PUSH: sender},
        circuits=_circuits(_Clock()),
        now=at_night,
    )

    assert (report.sent, report.deferred) == (1, 0)
    assert sender.messages == 1
    assert (await _row(db_session, row.id)).status == "sent"


async def test_the_quiet_hours_hold_beats_an_open_circuit_and_its_instant(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """Both gates can refuse the same row and they disagree about when it becomes
    due: the circuit says "in five minutes", the night says "at 05:00". The night
    wins, because a non-critical row that becomes due at 20:02 must not be sent at
    20:07 either — the row's whole point is that the farmer hears it in the
    morning. The reason in `last_error` names the one that decided it."""
    at_night = datetime(2026, 9, 28, 3, 0, tzinfo=UTC)  # 22:00 Bogotá on the 27th
    row = await _pending_row(db_session, seeded, channel=Channel.PUSH, due_at=at_night)
    circuits = _circuits(_Clock())
    _open(circuits, Channel.PUSH)

    await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.PUSH: _FailingSender()},
        circuits=circuits,
        now=at_night,
    )

    held = await _row(db_session, row.id)
    assert held.next_attempt_at == datetime(2026, 9, 28, 10, 0, tzinfo=UTC)
    assert held.last_error == "the farm is in its quiet hours until 05:00"


async def test_a_group_is_not_formed_out_of_rows_the_night_holds(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """Grouping and the quiet hours cannot both apply to a row: a group of
    non-criticals is the 05:00 message, and a row held until 05:00 is not part of
    anything that goes out at 02:00. Both rows of one farm are held, each on its
    own, and the morning sweep groups them."""
    at_night = datetime(2026, 9, 28, 3, 0, tzinfo=UTC)  # 22:00 Bogotá on the 27th
    other_alert = await _alert_on(db_session, seeded, code="heat_stress", plot_id=seeded.plot_id)
    ids = [
        (await _pending_row(db_session, seeded, channel=Channel.PUSH, due_at=at_night)).id,
        (
            await _pending_row(
                db_session, seeded, channel=Channel.PUSH, due_at=at_night, alert_id=other_alert
            )
        ).id,
    ]
    sender = _LandingSender()
    circuits = _circuits(_Clock())

    held = await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.PUSH: sender},
        circuits=circuits,
        now=at_night,
    )
    assert (held.deferred, held.sent) == (2, 0)
    assert sender.messages == 0

    # The morning sweep: both rows are due again, and now they are one message.
    morning = datetime(2026, 9, 28, 10, 0, tzinfo=UTC)  # 05:00 Bogotá on the 28th
    report = await dispatch_due_notifications(
        outbox=_outbox(db_session),
        senders={Channel.PUSH: sender},
        circuits=circuits,
        now=morning,
    )

    assert (report.sent, report.deferred) == (2, 0)
    assert sender.messages == 1
    assert (await _row(db_session, ids[0])).status == "sent"
    assert (await _row(db_session, ids[1])).status == "sent"


async def test_one_message_writes_its_rows_in_one_commit(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """A message's outcome is ONE commit, never one per row (R3-001).

    The per-row commit is why every row takes its own `hold()` lock: the claim's
    locks all die with the first commit. Two commits for one message would put
    the group's second row back in the table as `pending` and UNLOCKED between
    them, and a second worker's claim — which skips locked rows, not pending ones
    — would take it and send it a second time: the farmer gets the same alert twice
    from the same message, and the row that first `hold()` refused would be
    reported as `skipped` after it was already delivered.

    Counted, because a race like that is not observable from the end state: both
    rows end `sent` either way. The negative half is the row group of one, which
    must still be a single call, and the two separate `push` rows of two farms,
    which must still be two.
    """
    other_alert = await _alert_on(db_session, seeded, code="heat_stress", plot_id=seeded.plot_id)
    grouped = [
        (await _pending_row(db_session, seeded, channel=Channel.PUSH)).id,
        (await _pending_row(db_session, seeded, channel=Channel.PUSH, alert_id=other_alert)).id,
    ]
    commits: list[list[UUID]] = []

    class _CountingOutbox(SqlAlchemyOutboxRepository):
        async def mark_sent(self, notification_ids: Sequence[UUID], *, at: datetime) -> None:
            commits.append(list(notification_ids))
            await super().mark_sent(notification_ids, at=at)

    await dispatch_due_notifications(
        outbox=_CountingOutbox(db_session),
        senders={Channel.PUSH: _LandingSender()},
        circuits=_circuits(_Clock()),
        now=_DUE,
    )

    assert commits == [grouped]
    assert (await _row(db_session, grouped[0])).status == "sent"
    assert (await _row(db_session, grouped[1])).status == "sent"


async def test_a_lone_row_and_two_messages_are_still_one_commit_each(
    db_session: AsyncSession, seeded: Seeded
) -> None:
    """The negative half of the pair above, in the two shapes that must NOT be
    batched: a group of one (almost every message) and two rows of two different
    farms (two different messages). Grouping is a message, not a claim batch —
    docs/06 §4's Reclamo row wants the outcome of a message committed, and D31
    wants a crash in one message not to strand another one's rows."""
    commits: list[list[UUID]] = []

    class _CountingOutbox(SqlAlchemyOutboxRepository):
        async def mark_sent(self, notification_ids: Sequence[UUID], *, at: datetime) -> None:
            commits.append(list(notification_ids))
            await super().mark_sent(notification_ids, at=at)

    lone = (await _pending_row(db_session, seeded, channel=Channel.PUSH)).id
    other_alert = await _alert_on(
        db_session, seeded, code="heat_stress", plot_id=seeded.other_plot_id
    )
    other_farm = (
        await _pending_row(db_session, seeded, channel=Channel.PUSH, alert_id=other_alert)
    ).id

    await dispatch_due_notifications(
        outbox=_CountingOutbox(db_session),
        senders={Channel.PUSH: _LandingSender()},
        circuits=_circuits(_Clock()),
        now=_DUE,
    )

    assert commits == [[lone], [other_farm]]


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
            await sender.send([notification])

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
    the outbox through the senders the profile registers.

    The row is a CRITICAL one, and that is the point of this test being able to
    run at any hour: the job takes the wall clock, so a non-critical row would
    legitimately be held while Bogotá sleeps and this test would fail for a third
    of the day. The quiet hours are real, so the test is not what fixes it — a
    severity the silence does not apply to is (D6, D39)."""
    critical = await _seed_alert(db_session, severity="critical")
    row = await _pending_row(
        db_session,
        critical,
        channel=Channel.SMS,
        due_at=datetime.now(UTC) - timedelta(seconds=1),
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
