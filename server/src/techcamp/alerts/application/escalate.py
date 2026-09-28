"""The escalation of a critical alert nobody acknowledged (docs/06 §3
"Reloj de escalamiento"; docs/01:57 RNF-05; D3, D4, D5, D12, D21, D30, D37).

Two hours after `opened_at`, a critical that is still `open` escalates: the
alert gets `escalated_at` (D3: escalation is not a state) and the farm's
technician gets one `sms` row (D4, D5). Nothing is sent from here — the row goes
through the outbox like every other notice (ADR-0016), so the dispatcher owns the
delivery, its retries and its at-least-once guarantee (D30).

The decision is the domain's (`is_eligible_for_escalation`); this use case only
supplies the row the sweep read `FOR UPDATE SKIP LOCKED` and the recipients D4
names. The clock runs from `opened_at` and never from the upgrade, so a
`water_stress` that became critical at 48 h is due on its next check (D12).
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from techcamp.alerts.application.ports import AlertRepository
from techcamp.alerts.domain import is_eligible_for_escalation
from techcamp.notifications.application import Channel, NotificationDraft, next_attempt_at

logger = logging.getLogger(__name__)


async def escalate_due_alerts(*, org_id: UUID, at: datetime, alerts: AlertRepository) -> int:
    """Escalate every critical of one org whose 2 h are up; how many it was.

    One alert per call of the lock, because `save` commits per alert and that
    commit is what releases the lock (D42): the row is decided and escalated under
    its own lock, and the next one is locked only after the previous escalation is
    committed. The lock's own filter and the domain's `is_eligible_for_escalation`
    agree by construction here, and the domain has the last word: a row that is
    not eligible escalates nothing and costs no notice.

    A second sweep over the same org finds nothing: the escalated alert is no
    longer a candidate, so one escalation is one `sms` row however often the
    5-minute sweep runs.
    """
    escalated = 0
    while (alert := await alerts.lock_escalation_candidate(org_id=org_id, at=at)) is not None:
        if not is_eligible_for_escalation(alert, at):
            continue
        target = await alerts.get_escalation_target(
            org_id=alert.org_id, plot_id=alert.plot_id, node_id=alert.node_id
        )
        if not target.recipients:
            # The third state, not "false": a farm with no technician and an org
            # with no owner to fall back to has nobody to act on the alert. The
            # clock is the alert's own, so it escalates anyway and the gap is
            # visible here instead of in a notification row nobody would read.
            logger.warning(
                "alerts: org %s has no technician and no owner to notify of the escalation "
                "of alert %s",
                alert.org_id,
                alert.id,
            )
        await alerts.save(
            alert.escalate(at),
            _sms_drafts(target.recipients, at=at),
            target.farm_id,
            expected_state=alert.state,
            expected_severity=alert.severity,
        )
        escalated += 1
    return escalated


def _sms_drafts(recipients: tuple[UUID, ...], *, at: datetime) -> list[NotificationDraft]:
    """One `sms` row per recipient, due now (D5; docs/06 §4).

    `next_attempt_at(critical=True, ...)` rather than `at` stated twice: the hour
    and the date of the silence live in one function (D39), and a critical is the
    one severity that may break it.
    """
    return [
        NotificationDraft(
            user_id=recipient,
            channel=Channel.SMS,
            next_attempt_at=next_attempt_at(critical=True, now=at),
        )
        for recipient in recipients
    ]
