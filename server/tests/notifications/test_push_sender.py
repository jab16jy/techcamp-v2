"""The Web Push sender: one push per registered browser, a subscription the push
service no longer knows deleted and the next one tried, and the payload T9's
service worker already parses (docs/06-diseno-detallado.md §4; ADR-0016,
ADR-0021; D30, D32, D33, D34).

Against real Postgres for the same reason `test_dispatch` is: the half that can
be got wrong here is the subscription read and the delete that has to survive
it, and neither is provable against a double. The push service is the OTHER kind
of external I/O — a network endpoint no test may reach — so it sits behind
`PushTransport` and is replaced here by a recording double, which is also what
makes the 404/410 mapping testable without a live service.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from py_vapid.utils import b64urlencode
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.orm import AlertRow, AlertRuleRow
from techcamp.farms.adapters.orm import FarmRow, PlotRow
from techcamp.identity.adapters.orm import AppUserRow, OrganizationRow
from techcamp.notifications.adapters import push_transport as push_transport_module
from techcamp.notifications.adapters.circuits import InProcessProviderCircuits
from techcamp.notifications.adapters.orm import NotificationRow, PushSubscriptionRow
from techcamp.notifications.adapters.outbox import SqlAlchemyOutboxRepository
from techcamp.notifications.adapters.push_transport import PywebPushTransport
from techcamp.notifications.adapters.senders import WebPushSender, build_senders
from techcamp.notifications.adapters.subscriptions import SqlAlchemyPushSubscriptionRepository
from techcamp.notifications.application import dispatch_due_notifications
from techcamp.notifications.domain.errors import (
    PushSubscriptionGoneError,
)
from techcamp.notifications.domain.models import Channel, PushSubscription
from techcamp.shared.db import async_session_factory
from techcamp.shared.ids import uuid7

pytestmark = pytest.mark.anyio

_DUE = datetime(2026, 9, 27, 15, 0, tzinfo=UTC)  # 10:00 Bogotá, outside quiet hours
_PHONE_SEQ = iter(range(1, 10_000))

# The keys the browser sends (docs/04:145). `pywebpush` only needs them to be
# present — the fake transport never decodes them, so a real key pair would only
# make the fixtures lie about what is under test.
_KEYS = {"p256dh": "public-key", "auth": "auth-secret"}


@dataclass
class _RecordingTransport:
    """The push service, replaced.

    `gone_for` names the endpoints the service answers 404/410 for; `error` is
    what every other call raises, standing in for a provider that is down.
    """

    delivered: list[tuple[PushSubscription, str, str, int]] = field(default_factory=list)
    gone_for: set[str] = field(default_factory=set)
    error: Exception | None = None

    async def deliver(
        self, subscription: PushSubscription, *, payload: str, topic: str, ttl: int
    ) -> None:
        if subscription.endpoint in self.gone_for:
            raise PushSubscriptionGoneError(subscription.id)
        if self.error is not None:
            raise self.error
        self.delivered.append((subscription, payload, topic, ttl))


@dataclass(frozen=True, slots=True)
class Seeded:
    user_id: UUID
    alert_id: UUID
    org_id: UUID
    plot_id: UUID
    rule_id: UUID


async def _seed(session: AsyncSession) -> Seeded:
    """One organization, one recipient and one open `water_stress` alert.

    A plot alert rather than a node alert because `ck_alert_target_exactly_one`
    wants one of the two and the plot chain is the one the fan-out already uses.
    """
    org_id, user_id, farm_id, plot_id = uuid7(), uuid7(), uuid7(), uuid7()
    session.add(AppUserRow(id=user_id, phone=f"+57300{next(_PHONE_SEQ):06d}"))
    session.add(OrganizationRow(id=org_id, name="Test Org", kind="individual"))
    await session.commit()
    session.add(
        FarmRow(
            id=farm_id,
            org_id=org_id,
            name="Finca Principal",
            municipality_code="47001",
            location="SRID=4326;POINT(-74.1 10.9)",
        )
    )
    await session.commit()
    session.add(
        PlotRow(
            id=plot_id,
            org_id=org_id,
            farm_id=farm_id,
            name="Lote 1",
            boundary=(
                "SRID=4326;POLYGON((-74.10 10.90, -74.10 10.91, -74.09 10.91, -74.09 10.90, "
                "-74.10 10.90))"
            ),
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
    return Seeded(
        user_id=user_id, alert_id=alert_id, org_id=org_id, plot_id=plot_id, rule_id=rule_id
    )


async def _second_alert(
    session: AsyncSession, seeded: Seeded, *, code: str = "heat_stress"
) -> UUID:
    """A second open alert on the seeded plot.

    A DIFFERENT rule because the partial unique index allows one non-resolved alert
    per (rule, plot) (docs/06 §3), and a different alert is what grouping needs: two
    rows of one alert would be one message for a reason that has nothing to do with
    the farm.
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
            plot_id=seeded.plot_id,
            state="open",
            severity="warning",
            opened_at=_DUE,
        )
    )
    await session.commit()
    return alert_id


async def _pending_push(
    session: AsyncSession,
    seeded: Seeded,
    *,
    alert_id: UUID | None = None,
    user_id: UUID | None = None,
) -> NotificationRow:
    row = NotificationRow(
        id=uuid7(),
        alert_id=alert_id or seeded.alert_id,
        user_id=user_id or seeded.user_id,
        channel=Channel.PUSH.value,
        status="pending",
        attempts=0,
        next_attempt_at=_DUE,
        created_at=_DUE,
    )
    session.add(row)
    await session.commit()
    return row


async def _subscribe(session: AsyncSession, user_id: UUID, suffix: str) -> PushSubscriptionRow:
    row = PushSubscriptionRow(
        id=uuid7(),
        user_id=user_id,
        endpoint=f"https://push.example.com/sub/{suffix}",
        keys=dict(_KEYS),
        created_at=_DUE,
    )
    session.add(row)
    await session.commit()
    return row


def _subs(session: AsyncSession) -> SqlAlchemyPushSubscriptionRepository:
    return SqlAlchemyPushSubscriptionRepository(session)


async def _remaining(session: AsyncSession) -> list[PushSubscriptionRow]:
    # `populate_existing` rather than `expire_all()`: the sender committed the
    # delete on this same session, and expiring everything here would leave the
    # outbox row expired too, so reading it back would lazy-load outside the
    # greenlet and blow up.
    result = await session.execute(
        select(PushSubscriptionRow).execution_options(populate_existing=True)
    )
    return list(result.scalars())


async def _row(session: AsyncSession, notification_id: UUID) -> NotificationRow:
    session.expire_all()
    return (
        await session.execute(select(NotificationRow).where(NotificationRow.id == notification_id))
    ).scalar_one()


class _LockProbingTransport(_RecordingTransport):
    """The push service, plus a look at who holds the outbox row behind it.

    The probe runs on the SECOND delivery of a batch — which is after the first
    row's gone subscription has been deleted and that delete has committed. If
    the subscription repository had been bound to the dispatcher's own session,
    that commit would have ended the transaction holding the claim's
    `FOR UPDATE SKIP LOCKED` locks, and the row this batch has not sent yet would
    be up for grabs by a second worker.
    """

    def __init__(self, *, probe: UUID, control: UUID) -> None:
        super().__init__(gone_for={"https://push.example.com/sub/phone"})
        self._probe = probe
        self._control = control
        # Every delivery of the batch is recorded, not just the last one: the
        # first is the one taken while the delete commits, which is the only moment
        # the claim's locks are in question, and a later delivery would take the
        # row's own `hold()` lock and read as fine whatever happened before it.
        self.observations: list[tuple[bool, bool]] = []

    async def deliver(
        self, subscription: PushSubscription, *, payload: str, topic: str, ttl: int
    ) -> None:
        if subscription.endpoint.endswith("laptop"):
            # `control` is the negative half: a row this claim never took has to be
            # lockable, so a refused lock above means the row is held rather than
            # the probe failing for some other reason.
            self.observations.append(
                (await _is_locked(self._probe), not await _is_locked(self._control))
            )
        await super().deliver(subscription, payload=payload, topic=topic, ttl=ttl)


async def _is_locked(notification_id: UUID) -> bool:
    """Whether some other transaction still holds this row's lock.

    `NOWAIT` instead of a wait, so the answer is the database's and not a timeout: a
    row the claim holds cannot be locked at all. asyncpg refuses with
    `LockNotAvailableError` and SQLAlchemy's asyncpg adapter translates it into
    `DBAPIError` — read out of the driver, not from its docs, which is the only
    reason this catches a public class instead of the adapter's private `Error`.
    """
    async with async_session_factory() as other:
        try:
            await other.execute(
                select(NotificationRow.id)
                .where(NotificationRow.id == notification_id)
                .with_for_update(nowait=True)
            )
        except DBAPIError:
            return True
    return False


async def test_deleting_a_gone_subscription_never_releases_the_claims_own_locks(
    db_session: AsyncSession,
) -> None:
    """The sharpest hazard in the outbox, pinned at last.

    `WebPushSender` deletes a subscription the push service reported as gone, and
    a delete is a commit. Bound to the dispatcher's session, that commit would
    end the transaction holding the whole batch's `FOR UPDATE SKIP LOCKED` locks
    and let a second worker claim and send the rows this batch has not reached
    yet — a `push` row sent twice, which is the one duplicate D30 does not buy.
    `dispatch_outbox` therefore opens a SECOND session for the subscription
    repository, and this is the test that says so: the batch's second row is
    still locked while the first row's delete commits, and a row outside the
    batch is free.

    Checked by putting the hazard back: binding the subscription repository to the
    dispatcher's session makes this fail on the first observation, which is the
    whole reason it records every observation instead of the last.
    """
    seeded = await _seed(db_session)
    first = await _pending_push(db_session, seeded)
    second = await _pending_push(db_session, seeded)
    not_claimed = await _pending_push(db_session, seeded)
    not_claimed.next_attempt_at = _DUE + timedelta(hours=1)
    await db_session.commit()
    await _subscribe(db_session, seeded.user_id, "phone")
    await _subscribe(db_session, seeded.user_id, "laptop")
    transport = _LockProbingTransport(probe=second.id, control=not_claimed.id)

    async with async_session_factory() as push_session:
        report = await dispatch_due_notifications(
            outbox=SqlAlchemyOutboxRepository(db_session),
            senders={Channel.PUSH: WebPushSender(_subs(push_session), transport)},
            circuits=InProcessProviderCircuits(),
            now=_DUE,
        )

    # Both rows go: the first has the laptop to fall back on after the phone is
    # deleted, and the second only ever had the laptop. What matters is that the
    # batch's locks survived the delete in between.
    assert (report.claimed, report.sent) == (2, 2)
    assert transport.observations, "the probe never ran"
    assert all(locked and control_free for locked, control_free in transport.observations)
    assert (await _row(db_session, first.id)).status == "sent"


async def test_a_push_row_reaches_every_browser_the_user_registered(
    db_session: AsyncSession,
) -> None:
    """One `push` row, every subscription of its user: a farmer with a phone and
    a laptop has to hear about the alert on both (docs/06 §4; D5)."""
    seeded = await _seed(db_session)
    row = await _pending_push(db_session, seeded)
    await _subscribe(db_session, seeded.user_id, "phone")
    await _subscribe(db_session, seeded.user_id, "laptop")
    transport = _RecordingTransport()

    report = await dispatch_due_notifications(
        outbox=SqlAlchemyOutboxRepository(db_session),
        senders={Channel.PUSH: WebPushSender(_subs(db_session), transport)},
        circuits=InProcessProviderCircuits(),
        now=_DUE,
    )

    assert (report.claimed, report.sent) == (1, 1)
    assert [subscription.endpoint for subscription, _, _, _ in transport.delivered] == [
        "https://push.example.com/sub/phone",
        "https://push.example.com/sub/laptop",
    ]
    assert (await _row(db_session, row.id)).status == "sent"


async def test_the_payload_is_exactly_what_the_service_worker_parses(
    db_session: AsyncSession,
) -> None:
    """D33: T9's `pushPayload.ts` reads four optional strings and derives the rest
    — `icon` and the `notificationclick` route are the client's own. `route` is
    deliberately absent so the handler keeps falling back to `/alertas` (D33)
    rather than the server inventing a deep link docs/07 does not describe."""
    seeded = await _seed(db_session)
    await _pending_push(db_session, seeded)
    await _subscribe(db_session, seeded.user_id, "phone")
    transport = _RecordingTransport()

    await dispatch_due_notifications(
        outbox=SqlAlchemyOutboxRepository(db_session),
        senders={Channel.PUSH: WebPushSender(_subs(db_session), transport)},
        circuits=InProcessProviderCircuits(),
        now=_DUE,
    )

    _, payload, _, _ = transport.delivered[0]
    assert json.loads(payload) == {
        "title": "Alerta",
        "body": "Tus cultivos necesitan agua.",
        "tag": f"alert-{seeded.alert_id}",
    }


async def test_a_grouped_push_is_one_message_that_says_all_of_it(
    db_session: AsyncSession,
) -> None:
    """docs/06 §4 "Agrupación": several non-critical alerts of one farm go out as
    ONE notification. The payload is still the three keys T9 parses, so a grouped
    message needs no client change: the sentences join into the `body` and the
    `tag` is the group's own alert, so the whole message is replaced on a retry
    rather than stacking a copy of each alert."""
    seeded = await _seed(db_session)
    first = await _pending_push(db_session, seeded)
    await _subscribe(db_session, seeded.user_id, "phone")
    other_alert = await _second_alert(db_session, seeded)
    await _pending_push(db_session, seeded, alert_id=other_alert)
    transport = _RecordingTransport()

    await dispatch_due_notifications(
        outbox=SqlAlchemyOutboxRepository(db_session),
        senders={Channel.PUSH: WebPushSender(_subs(db_session), transport)},
        circuits=InProcessProviderCircuits(),
        now=_DUE,
    )

    assert len(transport.delivered) == 1
    _, payload, _, _ = transport.delivered[0]
    parsed = json.loads(payload)
    assert set(parsed) == {"title", "body", "tag"}
    # Due order, oldest first, so the message reads as the alerts happened.
    assert parsed["body"] == "Tus cultivos necesitan agua. Hace demasiado calor para el cultivo."
    assert parsed["tag"] == f"alert-{first.alert_id}"
    assert parsed["title"] == "Alerta"


async def test_a_message_never_carries_two_recipients_alerts(
    db_session: AsyncSession,
) -> None:
    """D4 sends a plot alert to the farm's producers and owners, so two rows of
    ONE farm for two users are the common case. Grouping is keyed on (user, farm)
    (D6): a message that carried both would be delivered to one recipient's
    browsers and tell them about the other's farm, which is a farm's business
    leaking to a neighbour in the same organization."""
    seeded = await _seed(db_session)
    await _pending_push(db_session, seeded)
    other_user = uuid7()
    db_session.add(AppUserRow(id=other_user, phone=f"+57300{next(_PHONE_SEQ):06d}"))
    await db_session.commit()
    await _pending_push(db_session, seeded, user_id=other_user)
    await _subscribe(db_session, seeded.user_id, "phone")
    # A different endpoint: `push_subscription.endpoint` is UNIQUE (docs/03).
    await _subscribe(db_session, other_user, "tablet")
    transport = _RecordingTransport()

    await dispatch_due_notifications(
        outbox=SqlAlchemyOutboxRepository(db_session),
        senders={Channel.PUSH: WebPushSender(_subs(db_session), transport)},
        circuits=InProcessProviderCircuits(),
        now=_DUE,
    )

    # Two messages, and the sender only ever sees the rows of one user: the
    # subscription it read belongs to the first row's user, and the group a second
    # row of the other user joined would have named both farms in one push.
    assert len(transport.delivered) == 2
    for _, payload, _, _ in transport.delivered:
        assert json.loads(payload)["body"] == "Tus cultivos necesitan agua."


async def test_a_retry_of_the_same_row_replaces_its_own_notification(
    db_session: AsyncSession,
) -> None:
    """D30: delivery is at least once, so a duplicate is real. The `tag` the client
    uses for replacement is the alert's and the Web Push `Topic` is the group's
    oldest outbox row, so the second push of one message replaces the first
    instead of stacking a second copy of the same news. Two rows of one farm are
    ONE message (docs/06 §4 "Agrupación"), so the group and both rows keep their
    identity across the retry — that is what has to hold for the replacement."""
    seeded = await _seed(db_session)
    first = await _pending_push(db_session, seeded)
    await _subscribe(db_session, seeded.user_id, "phone")
    other_alert = await _second_alert(db_session, seeded)
    second = await _pending_push(db_session, seeded, alert_id=other_alert)
    transport = _RecordingTransport()

    for _ in range(2):
        await dispatch_due_notifications(
            outbox=SqlAlchemyOutboxRepository(db_session),
            senders={Channel.PUSH: WebPushSender(_subs(db_session), transport)},
            circuits=InProcessProviderCircuits(),
            now=_DUE,
        )
        # The rows are `sent` now, so re-arm them the way a retry would: same
        # rows, same ids, due again.
        for candidate in (first, second):
            candidate.status = "pending"
        await db_session.commit()

    # Two rows, grouped, sent again on the second pass: two messages, not four.
    assert len(transport.delivered) == 2
    assert {json.loads(payload)["tag"] for _, payload, _, _ in transport.delivered} == {
        f"alert-{seeded.alert_id}"
    }
    assert {topic for _, _, topic, _ in transport.delivered} == {first.id.hex}
    # 32 characters is the protocol's cap and `hex` fits it exactly, which is why
    # the topic is the undashed uuid.
    assert all(len(topic) <= 32 for _, _, topic, _ in transport.delivered)


async def test_a_gone_subscription_is_deleted_and_the_next_one_is_still_delivered(
    db_session: AsyncSession,
) -> None:
    """docs/06 §4: `410 Gone` deletes the `push_subscription` and tries the next
    one. It costs the outbox row nothing — the alert did reach the other
    browser, so the row is `sent`, not retried."""
    seeded = await _seed(db_session)
    row = await _pending_push(db_session, seeded)
    dead = await _subscribe(db_session, seeded.user_id, "dead")
    alive = await _subscribe(db_session, seeded.user_id, "alive")
    transport = _RecordingTransport(gone_for={dead.endpoint})

    report = await dispatch_due_notifications(
        outbox=SqlAlchemyOutboxRepository(db_session),
        senders={Channel.PUSH: WebPushSender(_subs(db_session), transport)},
        circuits=InProcessProviderCircuits(),
        now=_DUE,
    )

    assert (report.sent, report.retried) == (1, 0)
    assert [subscription.id for subscription, _, _, _ in transport.delivered] == [alive.id]
    assert [remaining.id for remaining in await _remaining(db_session)] == [alive.id]
    assert (await _row(db_session, row.id)).attempts == 0


async def test_a_row_whose_every_subscription_is_gone_is_never_called_delivered(
    db_session: AsyncSession,
) -> None:
    """Nothing was delivered, so returning — which is what marks a row `sent` —
    would put a lie in the outbox. The row costs one attempt and T7a's backoff
    decides its future, and the dead rows are already gone from the table, so the
    next attempt has nothing left to try and says so."""
    seeded = await _seed(db_session)
    row = await _pending_push(db_session, seeded)
    only = await _subscribe(db_session, seeded.user_id, "dead")
    transport = _RecordingTransport(gone_for={only.endpoint})

    report = await dispatch_due_notifications(
        outbox=SqlAlchemyOutboxRepository(db_session),
        senders={Channel.PUSH: WebPushSender(_subs(db_session), transport)},
        circuits=InProcessProviderCircuits(),
        now=_DUE,
    )

    assert (report.sent, report.retried) == (0, 1)
    assert [remaining.id for remaining in await _remaining(db_session)] == []
    backed_off = await _row(db_session, row.id)
    assert (backed_off.status, backed_off.attempts) == ("pending", 1)
    assert "no push subscription" in str(backed_off.last_error)


async def test_a_user_who_never_subscribed_costs_one_attempt_and_no_more(
    db_session: AsyncSession,
) -> None:
    """`plan_notifications` writes a `push` row per recipient whoever they are
    (D5), so rows exist for users who never enabled notifications. That is a
    real, retryable failure of the delivery — not a missing adapter (D31) — and
    the honest terminal state for it is `failed` after the documented five
    attempts, never `sent`."""
    seeded = await _seed(db_session)
    row = await _pending_push(db_session, seeded)
    transport = _RecordingTransport()

    report = await dispatch_due_notifications(
        outbox=SqlAlchemyOutboxRepository(db_session),
        senders={Channel.PUSH: WebPushSender(_subs(db_session), transport)},
        circuits=InProcessProviderCircuits(),
        now=_DUE,
    )

    assert (report.sent, report.retried) == (0, 1)
    backed_off = await _row(db_session, row.id)
    assert (backed_off.status, backed_off.attempts) == ("pending", 1)
    assert "no push subscription" in str(backed_off.last_error)


async def test_a_push_service_outage_costs_the_row_one_attempt_and_a_1_min_wait(
    db_session: AsyncSession,
) -> None:
    """docs/06 §4's `error temporal` branch: attempts++ and the exponential
    backoff, through the dispatcher T7a already wrote — the sender only has to
    raise."""
    seeded = await _seed(db_session)
    row = await _pending_push(db_session, seeded)
    await _subscribe(db_session, seeded.user_id, "phone")
    transport = _RecordingTransport(error=RuntimeError("push service unreachable"))

    report = await dispatch_due_notifications(
        outbox=SqlAlchemyOutboxRepository(db_session),
        senders={Channel.PUSH: WebPushSender(_subs(db_session), transport)},
        circuits=InProcessProviderCircuits(),
        now=_DUE,
    )

    assert (report.retried, report.failed) == (1, 0)
    backed_off = await _row(db_session, row.id)
    assert backed_off.last_error == "push service unreachable"
    assert backed_off.next_attempt_at > _DUE


async def test_one_live_browser_is_enough_even_while_another_one_is_down(
    db_session: AsyncSession,
) -> None:
    """The alert reached the farmer, so the row is `sent` and no other browser
    gets a duplicate. A device that is merely unreachable keeps its row: only a
    `404`/`410` says the subscription itself is gone."""
    seeded = await _seed(db_session)
    row = await _pending_push(db_session, seeded)
    alive = await _subscribe(db_session, seeded.user_id, "alive")
    await _subscribe(db_session, seeded.user_id, "flaky")
    sent_to_alive = _RecordingTransport()
    flaky = _RecordingTransport(error=RuntimeError("push service unreachable"))

    class _OnlyAlive:
        """A transport that is down for one endpoint only."""

        def __init__(self) -> None:
            self.inner = sent_to_alive

        async def deliver(
            self, subscription: PushSubscription, *, payload: str, topic: str, ttl: int
        ) -> None:
            if subscription.id == alive.id:
                await self.inner.deliver(subscription, payload=payload, topic=topic, ttl=ttl)
            else:
                await flaky.deliver(subscription, payload=payload, topic=topic, ttl=ttl)

    report = await dispatch_due_notifications(
        outbox=SqlAlchemyOutboxRepository(db_session),
        senders={Channel.PUSH: WebPushSender(_subs(db_session), _OnlyAlive())},
        circuits=InProcessProviderCircuits(),
        now=_DUE,
    )

    assert (report.sent, report.retried) == (1, 0)
    assert len(await _remaining(db_session)) == 2
    assert (await _row(db_session, row.id)).status == "sent"


def _refusing(status: int, calls: list[dict[str, Any]]) -> Callable[..., Awaitable[None]]:
    """A push service that answers one HTTP status and records the call.

    A factory rather than a closure over the loop variable, so the status each
    iteration refuses with is the one that was asked for.
    """

    async def _refuse(**kwargs: Any) -> None:
        calls.append(kwargs)
        raise _web_push_exception(status)

    return _refuse


async def test_the_transport_treats_both_404_and_410_as_a_gone_subscription() -> None:
    """pywebpush's own API summary names 404 and 410 for this, and docs/06 §4
    only wrote 410. Both mean the same thing to us — the subscription is no
    longer there — so both delete the row and try the next one."""
    subscription = PushSubscription(
        id=uuid7(), endpoint="https://push.example.com/sub/gone", keys=dict(_KEYS)
    )

    for status in (404, 410):
        calls: list[dict[str, Any]] = []
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(push_transport_module, "webpush_async", _refusing(status, calls))
            with pytest.raises(PushSubscriptionGoneError):
                await PywebPushTransport(
                    private_key="private-key", subject="mailto:soporte@techcamp.local"
                ).deliver(subscription, payload="{}", topic="t", ttl=60)

        # The VAPID `sub` claim and the `Topic` header are what make the send
        # authenticate (D32) and collapse a duplicate (D30).
        assert calls[0]["vapid_claims"] == {"sub": "mailto:soporte@techcamp.local"}
        assert calls[0]["headers"] == {"Topic": "t"}
        assert calls[0]["ttl"] == 60
        assert calls[0]["subscription_info"] == {
            "endpoint": subscription.endpoint,
            "keys": dict(_KEYS),
        }


async def test_any_other_push_service_status_is_an_ordinary_failure() -> None:
    """A 429 or a 503 says nothing about the subscription, so it must cost the
    outbox row an attempt (docs/06 §4's backoff) rather than delete a browser
    the farmer still uses."""
    subscription = PushSubscription(
        id=uuid7(), endpoint="https://push.example.com/sub/busy", keys=dict(_KEYS)
    )

    calls: list[dict[str, Any]] = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(push_transport_module, "webpush_async", _refusing(503, calls))
        with pytest.raises(push_transport_module.WebPushException):
            await PywebPushTransport(
                private_key="private-key", subject="mailto:soporte@techcamp.local"
            ).deliver(subscription, payload="{}", topic="t", ttl=60)


def _a_signable_vapid_key() -> str:
    """A real, throwaway VAPID private key in the shape the config holds (D32).

    `build_senders` checks that the key can actually SIGN (#140), so a test that
    wants the channel registered needs a key that really signs. Generated per test
    rather than committed: a private key in a repository is a private key in a
    repository, however obviously it is only a test.
    """
    generated = Vapid()
    generated.generate_keys()
    return b64urlencode(
        generated.private_key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


async def test_push_is_registered_only_where_there_is_a_vapid_key_to_sign_with(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """docs/04:143 leaves the key pair to the server's configuration (D32), and a
    deployment without one must behave like the client does when
    `VITE_VAPID_PUBLIC_KEY` is unset: push off, everything else intact. Registering
    the sender anyway would spend all five attempts of every `push` row on a
    provider that cannot be built — the bug D31 fixed for a missing adapter."""
    from techcamp.notifications.adapters import senders as senders_module

    monkeypatch.setattr(senders_module, "is_seminar_profile", lambda: True)
    monkeypatch.setattr(senders_module, "vapid_private_key", lambda: None)
    monkeypatch.setattr(senders_module, "vapid_subject", lambda: "mailto:soporte@techcamp.local")

    senders = build_senders(_subs(db_session))

    assert Channel.PUSH not in senders
    assert set(senders) == {Channel.SMS, Channel.WHATSAPP}


async def test_a_vapid_key_that_cannot_sign_leaves_push_unregistered_and_says_so(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A *wrong* key must be no stronger a signal than an absent one (#140).

    `build_senders` used to register `push` for any non-empty string, so a
    truncated base64, a PEM pasted without its header or a key of the wrong curve
    produced a channel that looks configured and can never deliver: every row
    walks the whole 1 min / 5 min / 30 min / 2 h backoff and ends `failed`, and
    nothing says why. D31's rule already covers "a channel nobody can send is not
    even claimed", and this is the same rule for a value that cannot sign.
    """
    from techcamp.notifications.adapters import senders as senders_module

    monkeypatch.setattr(senders_module, "is_seminar_profile", lambda: True)
    monkeypatch.setattr(senders_module, "vapid_private_key", lambda: "a-private-key")
    monkeypatch.setattr(senders_module, "vapid_subject", lambda: "mailto:soporte@techcamp.local")

    with caplog.at_level(logging.WARNING):
        senders = build_senders(_subs(db_session))

    assert Channel.PUSH not in senders
    # The negative half, and the part that makes it a D31 rule and not a broken
    # profile: the channels that CAN send are untouched.
    assert set(senders) == {Channel.SMS, Channel.WHATSAPP}
    # Silent misconfiguration is the other half of the bug: an operator has to be
    # able to find this in the worker's log.
    assert any("VAPID" in record.message for record in caplog.records)


async def test_a_vapid_key_that_can_sign_registers_push(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the pair above: a key that really signs still gets the
    channel, so the check cannot be satisfied by rejecting everything. The key is
    generated per test rather than committed, because a committed private key in
    a repository is a private key in a repository."""
    from techcamp.notifications.adapters import senders as senders_module

    monkeypatch.setattr(senders_module, "is_seminar_profile", lambda: True)
    monkeypatch.setattr(senders_module, "vapid_private_key", _a_signable_vapid_key)
    monkeypatch.setattr(senders_module, "vapid_subject", lambda: "mailto:soporte@techcamp.local")

    senders = build_senders(_subs(db_session))

    assert isinstance(senders.get(Channel.PUSH), WebPushSender)
    assert set(senders) == {Channel.PUSH, Channel.SMS, Channel.WHATSAPP}


async def test_the_production_profile_registers_push_too(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0021:26 and ADR-0016:5 put Web Push in BOTH profiles — only the
    SMS/WhatsApp provider is simulated in the seminar. The production profile
    still registers no SMS/WhatsApp sender, because that provider is future work
    and pretending to send is worse than not sending."""
    from techcamp.notifications.adapters import senders as senders_module

    monkeypatch.setattr(senders_module, "is_seminar_profile", lambda: False)
    monkeypatch.setattr(senders_module, "vapid_private_key", _a_signable_vapid_key)
    monkeypatch.setattr(senders_module, "vapid_subject", lambda: "mailto:soporte@techcamp.local")

    senders = build_senders(_subs(db_session))

    assert set(senders) == {Channel.PUSH}
    assert isinstance(senders[Channel.PUSH], WebPushSender)


def _web_push_exception(status: int) -> push_transport_module.WebPushException:
    """The exception pywebpush raises for any status above 202.

    Built through the real class with a response-shaped object, because the
    transport reads `WebPushException.status_code` and that property is exactly
    what adapts the sync (`status_code`) and async (`status`) response objects.
    """
    return push_transport_module.WebPushException(
        f"Push failed: {status}",
        response=SimpleNamespace(status=status, status_code=status),
    )
