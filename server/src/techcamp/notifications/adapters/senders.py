"""The channel senders the dispatcher registers (docs/06 §4; ADR-0016, ADR-0021).

Two adapters, and they are not the same kind of thing. The seminar's simulated
SMS/WhatsApp is deliberately not a stub that "will send for real later" — it is
the documented behavior of the seminar profile (ADR-0021: "Adaptador que
escribe en el log y en una bandeja visible en `/dev/outbox`"), and
`GET /dev/outbox` is that tray. Web Push is the opposite: ADR-0021:26 puts it in
BOTH profiles, because the push service is the browser's own and there is nothing
to simulate, so one adapter serves seminar and production alike.
"""

from __future__ import annotations

import json
import logging

from techcamp.notifications.adapters.push_transport import PywebPushTransport
from techcamp.notifications.application.ports import (
    NotificationSender,
    PushSubscriptionRepository,
    PushTransport,
)
from techcamp.notifications.domain.errors import (
    NoPushSubscriptionError,
    PushSubscriptionGoneError,
)
from techcamp.notifications.domain.models import Channel, PendingNotification
from techcamp.shared.config import is_seminar_profile, vapid_private_key, vapid_subject

logger = logging.getLogger(__name__)

_TTL_SECONDS = 3600
"""How long a push service may hold a push nobody has collected yet.

An hour, not the protocol's much longer defaults, because a critical alert stops
meaning what it said after docs/06 §4's own two-hour escalation clock has run
(D12): by then the SMS to the technician is the message that matters, and a
critical turning up on a lock screen two days late is worse than not turning up.
An hour still covers RNF-05's p95 under two minutes by a very wide margin, and a
`push` row is only ever due once the quiet hours have released it.
"""

_TITLES = {"critical": "Alerta crítica"}
"""Severity as the farmer reads it. Anything else is an `Alerta`. docs/01:64
(RNF-12) puts the interface in Spanish and the code in English, and this string
is interface."""

_BODIES = {
    "water_stress": "Tus cultivos necesitan agua.",
    "waterlogging": "Hay demasiada agua en el suelo.",
    "heat_stress": "Hace demasiado calor para el cultivo.",
    "fungal_risk": "Hay riesgo de hongos en el cultivo.",
    "heavy_rain_forecast": "Se espera lluvia fuerte.",
    "flood_risk": "Riesgo de inundación en la finca.",
    "drought_risk": "Riesgo de sequía en la finca.",
    "node_offline": "Un sensor dejó de enviar datos.",
}
"""The factory rules of docs/06 §3, said the way the alert is about to matter.

docs/04 justifies carrying `rule_code` and `severity` into the seminar tray
because "una bandeja que solo dijera `sms enviado` no diría qué llegó", and a
push is read in less time than a tray row: a lock screen that said `water_stress`
would say nothing at all. An organization may define its own rules (D11), so the
default below is the reachable path, not an impossible one.
"""

_DEFAULT_BODY = "Hay una alerta nueva."


def _payload(notification: PendingNotification) -> str:
    """The JSON T9's `pushPayload.ts` already parses, and nothing else.

    Three of the four keys it reads, on purpose. `route` is left out: D33 fixes
    `notificationclick` on the `/alertas` tab and says the payload carries no deep
    link of its own, and the server naming a web route would put docs/07's screen
    map into a module that has no business knowing it. `icon` is not read from the
    payload at all — the client owns it. The `tag` is the alert's, so the second
    push of the same alert replaces the first instead of stacking a second copy of
    the same news, which is the visible half of D30's at-least-once.
    """
    return json.dumps(
        {
            "title": _TITLES.get(notification.severity, "Alerta"),
            "body": _BODIES.get(notification.rule_code, _DEFAULT_BODY),
            "tag": f"alert-{notification.alert_id}",
        }
    )


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


class WebPushSender:
    """One `push` row, every browser its user registered (docs/06 §4).

    Three outcomes, and the difference between them is the whole point of this
    class:

    - **Gone** (`404`/`410`): the subscription is deleted and the next one is
      tried. It costs the outbox row nothing, because the browser is gone and no
      number of retries would reach it.
    - **Delivered**: returning, which is what marks the row `sent`. One live
      browser is enough, and a second delivery to it would be the duplicate D30
      already accepts but does not need to manufacture.
    - **Anything else**, or no subscription left at all: raised, so T7a's backoff
      answers for it. Never a silent success — a `push` row marked `sent` that no
      farmer ever saw would make `/dev/outbox` and docs/11's delivery ratio
      describe a delivery that did not happen.
    """

    def __init__(
        self,
        subscriptions: PushSubscriptionRepository,
        transport: PushTransport,
    ) -> None:
        self._subscriptions = subscriptions
        self._transport = transport

    async def send(self, notification: PendingNotification) -> None:
        subscriptions = await self._subscriptions.list_for_user(notification.user_id)
        if not subscriptions:
            raise NoPushSubscriptionError(notification.user_id)
        payload = _payload(notification)
        # The outbox row's own id as the Web Push `Topic`, which is the push
        # service's dedup key: `hex` is exactly the protocol's 32-character cap
        # (the dashed uuid is 36) and every hex character is inside the base64url
        # alphabet, so it needs no truncation that could collide.
        topic = notification.id.hex
        delivered = False
        failure: Exception | None = None
        for subscription in subscriptions:
            try:
                await self._transport.deliver(
                    subscription, payload=payload, topic=topic, ttl=_TTL_SECONDS
                )
            except PushSubscriptionGoneError:
                await self._subscriptions.delete_owned(subscription.id, notification.user_id)
                logger.info(
                    "push subscription %s is gone; deleted and trying the next one",
                    subscription.id,
                )
            except Exception as exc:  # noqa: BLE001 — the port says raise on failure
                failure = exc
                logger.warning("push to subscription %s failed: %s", subscription.id, exc)
            else:
                delivered = True
        if delivered:
            return
        # A provider that refused is the reason the row has to wait, and its own
        # error is the one worth keeping in `last_error`. With nothing to show for
        # it — every subscription gone, or none ever registered — the reason is
        # that there was no browser to deliver to.
        if failure is not None:
            raise failure
        raise NoPushSubscriptionError(notification.user_id)


def build_senders(
    subscriptions: PushSubscriptionRepository,
) -> dict[Channel, NotificationSender]:
    """The senders this profile has (ADR-0016; ADR-0021; D31, D32, D34).

    `push` is registered wherever there is a VAPID key that can SIGN with, in
    both profiles (ADR-0021:26), and left out when there is none — or when the one
    there is cannot sign (#140). Both are the same soft failure the web client has
    when its `VITE_VAPID_PUBLIC_KEY` is unset, and the same shape as D31's rule: a
    channel nobody can send is not registered, so its rows are never even claimed
    and no attempts are spent on a provider that cannot be built.

    The production SMS/WhatsApp provider is future work: the seminar adapter is
    the only simulated one, so production registers nothing rather than pretending
    a message was delivered, and a row whose channel is missing stays `pending`
    (`DispatchReport.skipped`) until its adapter exists.

    `subscriptions` is bound to its own session by the caller, never the
    dispatcher's: deleting a gone subscription commits, and a commit on the
    dispatcher's session would end the transaction holding the claim's
    `FOR UPDATE SKIP LOCKED` locks and let a second worker send the rest of the
    batch.
    """
    senders: dict[Channel, NotificationSender] = {}
    private_key = vapid_private_key()
    if private_key:
        transport = PywebPushTransport(private_key=private_key, subject=vapid_subject())
        signing_error = transport.signing_error()
        if signing_error is None:
            senders[Channel.PUSH] = WebPushSender(
                subscriptions=subscriptions,
                transport=transport,
            )
        else:
            # A key that cannot sign is a channel nobody can send, so D31's rule
            # answers for it exactly as it does for a missing one: the rows are
            # never claimed and no attempt is spent on a provider that cannot
            # exist. It is logged rather than raised because a worker that refuses
            # to start over a configuration mistake also refuses to deliver every
            # other channel, and the warning repeats until an operator fixes the
            # key — which is the moment it was asked to be seen (#140).
            logger.warning(
                "push is not registered: the configured VAPID key cannot sign (%s). "
                "Set TECHCAMP_VAPID_PRIVATE_KEY to the base64 DER of an EC2 "
                "(prime256v1) private key and TECHCAMP_VAPID_SUBJECT to a mailto: "
                "or https: URI (D32, D31)",
                signing_error,
            )
    if is_seminar_profile():
        sms = SeminarSmsSender()
        senders[Channel.SMS] = sms
        senders[Channel.WHATSAPP] = sms
    return senders
