"""The per-provider circuits, in the process that drains the outbox
(docs/06-diseno-detallado.md §4; ADR-0016; D35).

Its own module, like `outbox.py` and `subscriptions.py`, for the same reason
those two have one: the dispatch job needs this, and the senders must not import
the module that builds them. `jobs.py` → this module, one way.

**Why the state is in the process and not in Postgres (D35).** A circuit counts
how a PROVIDER has been answering, and that is knowledge the worker already has
in the only place the failures happen. Two `worker` processes each keep their own
count, and that is a deliberate trade rather than an oversight: while a provider
is down each worker opens its own circuit after its own five failures, so the
worst case is two workers' worth of failed calls instead of one worker's — every
one of them the same at-least-once attempt (D30) a single worker would have made
anyway, and none of them a lost message. The alternative, a table with the count
and its lock, would buy one number across processes at the price of a row written
on every push, a second thing to fail, and a migration for a state no doc asks to
be shared: docs/06 §4 says the breaker is per provider and says nothing about
where the number lives, and docs/09:51 asks for the fallback, not for a
distributed counter. The number of processes is a deployment fact (infra/compose.yaml
runs one `worker`) and the semantics do not depend on it being one.

`provider_circuits()` is a process-wide singleton for the other half of the same
reason: a count rebuilt on every sweep would start at zero sixty times an hour
and the circuit would never open at all.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from techcamp.notifications.domain.models import (
    BREAKER_COOLDOWN,
    BREAKER_FAILURE_THRESHOLD,
    Channel,
)
from techcamp.shared.circuit_breaker import CircuitBreaker


class InProcessProviderCircuits:
    """One `CircuitBreaker` per channel, held for the life of the process."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._breakers: dict[Channel, CircuitBreaker] = {}

    def _breaker(self, channel: Channel) -> CircuitBreaker:
        """The channel's own breaker, created the first time it is asked about.

        Creating it on first use rather than up front for every `Channel` is what
        keeps "per provider" literal: a channel with no registered sender has no
        provider, so it has no circuit and no failures to count.
        """
        breaker = self._breakers.get(channel)
        if breaker is None:
            breaker = CircuitBreaker(
                failure_threshold=BREAKER_FAILURE_THRESHOLD,
                cooldown_seconds=BREAKER_COOLDOWN.total_seconds(),
                clock=self._clock,
            )
            self._breakers[channel] = breaker
        return breaker

    def allows(self, channel: Channel) -> bool:
        return self._breaker(channel).allow_request()

    def record_success(self, channel: Channel) -> None:
        self._breaker(channel).record_success()

    def record_failure(self, channel: Channel) -> None:
        self._breaker(channel).record_failure()

    def cooldown_remaining(self, channel: Channel) -> float:
        return self._breaker(channel).cooldown_remaining()


_CIRCUITS = InProcessProviderCircuits()


def provider_circuits() -> InProcessProviderCircuits:
    """The circuits of this process.

    The dispatch job asks for these on every run and never builds them itself, so
    that "5 fallos seguidos" counts consecutive failures across sweeps instead of
    restarting at one.
    """
    return _CIRCUITS
