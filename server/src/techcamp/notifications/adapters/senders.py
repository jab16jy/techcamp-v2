"""The channel senders the dispatcher registers (docs/06 §4; ADR-0016, ADR-0021).

One adapter here today: the seminar's simulated SMS/WhatsApp, which logs and
returns. It is deliberately not a stub that "will send for real later" — it is
the documented behavior of the seminar profile (ADR-0021: "Adaptador que
escribe en el log y en una bandeja visible en `/dev/outbox`"), and
`GET /dev/outbox` is that tray.
"""

from __future__ import annotations

import logging

from techcamp.notifications.domain.models import Channel, PendingNotification
from techcamp.shared.config import is_seminar_profile

logger = logging.getLogger(__name__)


class SeminarSmsSender:
    """ADR-0021: the seminar "provider" writes the message to the log.

    Returning is what marks the row `sent` (D8): in a seminar there is no
    provider to fail, so a simulated message that reached the log has reached
    the room, and `GET /dev/outbox` is the other half of the same delivery.
    """

    async def send(self, notification: PendingNotification) -> None:
        logger.info(
            "simulated %s to user %s: %s alert %s (%s)",
            notification.channel.value,
            notification.user_id,
            notification.severity,
            notification.rule_code,
            notification.alert_id,
        )


def build_senders() -> dict[Channel, SeminarSmsSender]:
    """The senders this profile has (ADR-0016; ADR-0021).

    `push` is absent in both profiles until T7b wires Web Push, and the
    production SMS/WhatsApp provider is future work: the seminar adapter is the
    only simulated one, so production registers nothing rather than pretending
    a message was delivered. A row whose channel is missing stays pending
    (`DispatchReport.deferred`) until its adapter exists.
    """
    if not is_seminar_profile():
        return {}
    sender = SeminarSmsSender()
    return {Channel.SMS: sender, Channel.WHATSAPP: sender}
