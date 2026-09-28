"""The browser push service over HTTP (`pywebpush`; docs/06-diseno-detallado.md §4).

The one module that knows Web Push exists. Everything above it deals in
`PushTransport`, so the sender is testable without a service and the protocol's
own details — signing, encrypting, which status means what — stay in one place
instead of being spread over the outbox code that has to stay small.

`webpush_async` rather than the synchronous `webpush`: the dispatcher is async and
shares its event loop with the `alerts`, `weather` and `irrigation` periodics, so
a blocking round trip to a third party's endpoint inside a claim would stall all
of them. The async call is a first-class citizen of the same library and takes the
same `ttl` and `timeout`, so nothing is given up to get it.
"""

from __future__ import annotations

import logging

from py_vapid import Vapid
from pywebpush import WebPushException, webpush_async

from techcamp.notifications.domain.errors import PushSubscriptionGoneError
from techcamp.notifications.domain.models import PushSubscription

logger = logging.getLogger(__name__)

_GONE_STATUSES = frozenset({404, 410})
"""The two statuses that mean the subscription itself is gone.

docs/06 §4 named 410; `pywebpush`'s own API summary names 404 and 410 for the
same "remove this subscription" case, and there is no world in which a 404 from a
push service means the browser is still there. Treating only 410 would leave a
row that can never be delivered to sitting in the table forever, retried five
times per alert.
"""

_PROBE_AUDIENCE = "https://push.invalid"
"""The `aud` the signing probe claims.

The audience of a REAL delivery is the endpoint's own, which `webpush_async`
derives per call; the probe only has to prove a token can be signed at all, and
`.invalid` is the reserved TLD for exactly this.
"""

_TIMEOUT_SECONDS = 10
"""A push service that has not answered in ten seconds is down for us.

The outbox row stays claimed for the whole send, so an unanswered endpoint would
hold the row and the dispatcher's transaction for as long as the socket lives.
`aiohttp` treats this as a total timeout, connection included.
"""


class PywebPushTransport:
    """Signs with VAPID and sends one encrypted push (docs/04:143; D32, D34).

    `private_key` is the base64 DER of the EC2 private key (D32) and `subject` is
    the `sub` claim: RFC 8292 has the push service use it to reach the operator
    when a delivery cannot be made, so it is configuration and not a constant.
    `aud` and `exp` are left to the library, which derives `aud` from the endpoint
    being pushed to and keeps `exp` twelve hours out — inventing either here
    would only create a second place to get them wrong.
    """

    def __init__(self, *, private_key: str, subject: str) -> None:
        self._private_key = private_key
        self._subject = subject

    def signing_error(self) -> str | None:
        """Why this transport cannot sign a push, or `None` when it can.

        docs/04:143 leaves the key pair to the server's configuration, and a value
        that cannot sign is not a configured provider: truncated base64, a PEM
        pasted without its header, a key of the wrong curve, a `sub` that is not
        the `mailto:`/`https:` URI RFC 8292 asks for. Registering the channel
        anyway spends all five attempts of every `push` row on a delivery that
        cannot happen, and says nothing until a farmer reports no alert (#140).

        The probe makes the two calls `webpush_async` makes — `Vapid.from_string`
        and `sign`, with THIS transport's own `sub` — so the check is the real
        path rather than a second opinion about it, and it costs one signature at
        the moment the senders are built. A pure query: it says whether a push
        could be signed, and `build_senders` is what decides what to do about it
        and tells the operator.
        """
        try:
            Vapid.from_string(self._private_key).sign(
                {"sub": self._subject, "aud": _PROBE_AUDIENCE}
            )
        except Exception as exc:  # noqa: BLE001 — any configuration value fails softly
            return f"{type(exc).__name__}: {exc}"
        return None

    async def deliver(
        self, subscription: PushSubscription, *, payload: str, topic: str, ttl: int
    ) -> None:
        try:
            await webpush_async(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": dict(subscription.keys),
                },
                data=payload,
                vapid_private_key=self._private_key,
                vapid_claims={"sub": self._subject},
                # `pywebpush` has no first-class `topic`, but it forwards unknown
                # headers verbatim to the endpoint, and `Topic` is the Web Push
                # request's own header (draft-dalton-httpbis-message-collapsing,
                # which the major push services implement). A service that does
                # not know it ignores it, so this can only ever help.
                headers={"Topic": topic},
                ttl=ttl,
                timeout=_TIMEOUT_SECONDS,
            )
        except WebPushException as exc:
            if exc.status_code in _GONE_STATUSES:
                raise PushSubscriptionGoneError(subscription.id) from exc
            # Every other status — a 429, a 5xx, a 400 from a malformed payload —
            # says nothing about the subscription, so it stays a plain failure and
            # the outbox row costs an attempt (docs/06 §4's `error temporal`).
            raise
