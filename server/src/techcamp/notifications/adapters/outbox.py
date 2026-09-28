"""The `notification` outbox as the dispatcher reads and writes it
(docs/06-diseno-detallado.md §4; ADR-0016).

Its own module, not `repositories.py`, for one reason: the dispatch job needs
this repository, and the job must not import the module that enqueues it. The
dependency then runs one way — `repositories.py` (the alert's outbox write) →
`jobs.py` → this module — the same direction `telemetry` and `farms` use to
enqueue a neighbour's job from inside their own transaction.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from techcamp.alerts.adapters.orm import AlertRow, AlertRuleRow
from techcamp.farms.adapters.orm import PlotRow
from techcamp.notifications.adapters.orm import NotificationRow
from techcamp.notifications.domain.models import (
    CLAIM_LIMIT,
    CRITICAL_SEVERITY,
    Channel,
    FinalAttempt,
    PendingNotification,
    RetrySchedule,
)
from techcamp.telemetry.adapters.orm import NodeRow

_RULE_CODE = AlertRuleRow.code.label("rule_code")
"""The factory rule the alert came from, for the message (docs/04:145)."""

_NODE_PLOT = aliased(PlotRow)
"""`plot` a second time, reached through the alert's node.

`alert` stores a `plot_id` or a `node_id` and never a farm (docs/03:284-297), and
docs/06 §4 groups by farm, so a node alert's farm is the farm of the plot its
node hangs on. Two joins to the same table need two names, which is what the
alias is for.
"""


def _critical_of(channels: Sequence[Channel]) -> ColumnElement[bool]:
    """The critical rows of channels that have no sender but DO have an alternate.

    The severity comes off the joined `alert`, which is why this is a clause and
    not a channel list: "critical" is the alert's own fact, not the row's
    (docs/06 §4 "Canales por severidad"; D40).
    """
    return and_(
        AlertRow.severity == CRITICAL_SEVERITY,
        NotificationRow.channel.in_(tuple(channel.value for channel in channels)),
    )


_FARM = func.coalesce(PlotRow.farm_id, _NODE_PLOT.farm_id).label("farm_id")
"""The alert's farm, from whichever of the two targets it has.

`COALESCE` over LEFT joins, never an inner join: a farm that cannot be resolved is
`None` on the row and the dispatcher sends that row on its own, where dropping it
from the claim would lose an alert. Nothing has ever written an alert whose farm
cannot be resolved, so in practice this is `None` never — but the query is the
outbox's claim, and a claim that can drop a row is not a place to be clever.
"""


class SqlAlchemyOutboxRepository:
    """Claim due rows and record each one's outcome (docs/06 §4).

    Every write commits. The claim's `FOR UPDATE SKIP LOCKED` locks live until
    the transaction ends, so the commit is what releases a row once its outcome
    is durable.

    The unit of a commit is one MESSAGE, and it is neither one row nor one claim
    batch: a message that reached the provider must not be sent again because a
    commit happened, so all of its rows are written together (R3-001), and a crash
    in one message must not strand another one's rows, so two messages are never
    written together either. A message of one row is one row's commit.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim_due(
        self,
        *,
        now: datetime,
        channels: Sequence[Channel],
        unconfigured: Sequence[Channel] = (),
        limit: int = CLAIM_LIMIT,
    ) -> Sequence[PendingNotification]:
        """docs/06 §4: `SELECT … WHERE status='pending' AND next_attempt_at <=
        now() FOR UPDATE SKIP LOCKED LIMIT 50`.

        `channels` is what keeps a backlog from starving: a row whose channel has
        no sender yet (a `push` with no VAPID key configured) is not even asked
        for, so it cannot fill every claim while the rows that CAN be delivered
        wait behind it (D31).

        `unconfigured` is the one exception, and it is CRITICAL rows only: a
        channel in it has no sender but has an alternate channel that does, so its
        criticals are claimed and go out through that alternate — "mientras tanto
        las críticas pasan al canal alterno" (docs/06 §4; RF-08, RNF-05; D40).
        Without this the critical could not take the alternate switch at all, since
        the switch needs the row first: a misconfigured deployment would leave a
        critical alert undeliverable forever with nothing in the logs about a
        channel nobody configured. The non-criticals of the same channel are NOT
        claimed, exactly as D31 says, because a `warning` has no second channel
        (D5) and would only spend a pass on a row that cannot leave it.

        The alert's `rule_code` and `severity` join in because they are the
        message: a provider handed an id alone cannot render anything, and
        `farm_id` joins in because docs/06 §4 groups the messages by farm. `of=`
        names the outbox table because the joins bring the rule, the plot and the
        node in too, and locking an `alert`, `alert_rule`, `plot` or `node` row
        would block the very writes that produce the notices. Every join is to the
        row's own alert, so no row of one organization is reachable through
        another's.
        """
        if not channels:
            return []
        deliverable = NotificationRow.channel.in_(tuple(channel.value for channel in channels))
        where = deliverable if not unconfigured else deliverable | _critical_of(unconfigured)
        result = await self._session.execute(
            select(NotificationRow, AlertRow.org_id, AlertRow.severity, _RULE_CODE, _FARM)
            .join(AlertRow, AlertRow.id == NotificationRow.alert_id)
            .join(AlertRuleRow, AlertRuleRow.id == AlertRow.rule_id)
            .outerjoin(PlotRow, PlotRow.id == AlertRow.plot_id)
            .outerjoin(NodeRow, NodeRow.id == AlertRow.node_id)
            .outerjoin(_NODE_PLOT, _NODE_PLOT.id == NodeRow.plot_id)
            .where(
                NotificationRow.status == "pending",
                NotificationRow.next_attempt_at <= now,
                where,
            )
            .order_by(NotificationRow.next_attempt_at, NotificationRow.id)
            .limit(limit)
            .with_for_update(of=NotificationRow, skip_locked=True)
        )
        return [
            PendingNotification(
                id=row.id,
                alert_id=row.alert_id,
                org_id=org_id,
                user_id=row.user_id,
                channel=Channel(row.channel),
                rule_code=rule_code,
                severity=severity,
                attempts=row.attempts,
                farm_id=farm_id,
            )
            for row, org_id, severity, rule_code, farm_id in result
        ]

    async def hold(self, notification_id: UUID, *, now: datetime) -> bool:
        """Take one row's own lock right before its message is sent; `False` if
        another worker holds it already or has already finished it.

        The claim locks the whole batch at once, and the first commit ends that
        transaction and releases every lock it took — including the ones on rows
        not sent yet. This is the lock that protects the send itself, and it
        covers the WHOLE message this row belongs to, so the rows after it in that
        message are held for as long as the send runs and not for the fraction of
        it that had already been written. `SKIP LOCKED` is what lets the row go to
        the worker that already has it rather than sending it twice. `status` and
        `next_attempt_at` are re-checked because that gap is real: in it another
        worker can claim the row, send it and close it, and an id-only lock would
        hand this worker a row that is no longer ours to send.
        """
        return (
            await self._session.execute(
                select(NotificationRow.id)
                .where(
                    NotificationRow.id == notification_id,
                    NotificationRow.status == "pending",
                    NotificationRow.next_attempt_at <= now,
                )
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none() is not None

    async def mark_sent(self, notification_ids: Sequence[UUID], *, at: datetime) -> None:
        for row in (await self._locked_by_id(notification_ids)).values():
            row.status = "sent"
            row.sent_at = at
            row.last_error = None
        await self._session.commit()

    async def mark_retry(self, rows: Sequence[RetrySchedule], *, error: str) -> None:
        locked = await self._locked_by_id([outcome.notification_id for outcome in rows])
        for outcome in rows:
            row = locked[outcome.notification_id]
            row.status = "pending"
            row.attempts = outcome.attempts
            row.next_attempt_at = outcome.next_attempt_at
            row.last_error = error
        await self._session.commit()

    async def mark_failed(self, rows: Sequence[FinalAttempt], *, error: str) -> None:
        locked = await self._locked_by_id([outcome.notification_id for outcome in rows])
        for outcome in rows:
            row = locked[outcome.notification_id]
            row.status = "failed"
            row.attempts = outcome.attempts
            row.last_error = error
        await self._session.commit()

    async def mark_deferred(
        self, notification_id: UUID, *, next_attempt_at: datetime, reason: str
    ) -> None:
        """A due row that is not an attempt: the channel's circuit is open, or a
        non-critical row has come due inside the quiet hours.

        `attempts` is deliberately left alone. The outbox gives a row five
        attempts at a PROVIDER, and holding a row for a condition no delivery
        could have fixed would spend a whole evening's alerts by 23:00 with
        `failed` on every one of them (D31's rule for a row nobody can deliver,
        applied to a row that can).
        """
        row = await self._locked(notification_id)
        row.status = "pending"
        row.next_attempt_at = next_attempt_at
        row.last_error = reason
        await self._session.commit()

    async def _locked_by_id(self, notification_ids: Sequence[UUID]) -> dict[UUID, NotificationRow]:
        """One message's rows, taken under their own locks in ONE statement.

        Every row of a message is re-read under `FOR UPDATE` before it is written,
        and they are locked together, because the write that follows is one
        commit: a message's rows must never be `pending` and unlocked while the
        message has already been delivered (R3-001). A single row is the same
        query as before.

        Keyed by id rather than returned in a list, because the value each row is
        written with comes from the caller in ITS order and `WHERE id IN (...)`
        gives PostgreSQL no order to pair them against.
        """
        if not notification_ids:
            return {}
        result = await self._session.execute(
            select(NotificationRow)
            .where(NotificationRow.id.in_(tuple(notification_ids)))
            .with_for_update()
        )
        return {row.id: row for row in result.scalars().all()}

    async def _locked(self, notification_id: UUID) -> NotificationRow:
        """The claimed row, re-read under its own lock.

        The claim already holds it, so this is a re-read rather than a new
        lookup: the ORM session has no row in its identity map for the claim's
        tuple projection, and the write needs the mapped object.
        """
        return (
            await self._session.execute(
                select(NotificationRow)
                .where(NotificationRow.id == notification_id)
                .with_for_update()
            )
        ).scalar_one()
