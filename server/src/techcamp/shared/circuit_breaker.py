"""The circuit breaker both external providers use (docs/09-cuellos-de-botella.md:15,51).

One implementation, because the rule is the same wherever a provider is: stop
calling something that is answering with failures, and try again later, instead
of spending every call on a provider that is down. `weather`'s Open-Meteo
adapter (docs/06 §6) and the notification outbox's per-provider breaker
(docs/06 §4, ADR-0016) differ only in the numbers they pass and in what they do
with the answer, so the state machine lives here and each adapter keeps its own
error type and its own reaction.

Nothing in this module knows what a provider is: the clock is injected, so a
caller that needs a deadline the caller can name (the outbox writes one into
`next_attempt_at`) reads it from `cooldown_remaining` and converts it, and a
test drives the whole lifecycle without waiting.
"""

from __future__ import annotations

import enum
import time
from collections.abc import Callable

DEFAULT_FAILURE_THRESHOLD = 5
"""Consecutive failures that open the circuit (docs/06 §4 and §6 both say 5)."""

DEFAULT_COOLDOWN_SECONDS = 60.0
"""How long the circuit stays open before a trial is allowed (docs/06 §6)."""


class CircuitState(enum.StrEnum):
    """Lifecycle states of the circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """CLOSED: normal operation, opens after N consecutive failures.

    OPEN: refuses without calling the provider, and goes HALF_OPEN once the
    cooldown has elapsed. HALF_OPEN: allows a single trial, back to CLOSED on
    success and to OPEN on failure.

    State is per process and per instance. That is the whole contract, and it is
    enough for a provider being down: every worker sees the same failures and
    opens its own circuit within its own threshold, so the worst case with N
    workers is N × threshold failed calls instead of `threshold` — all of them
    the same at-least-once calls a single worker would have made anyway, and none
    of them a lost message. Anything that needs a shared count across processes
    would need a table and its own row-level locking, and no doc asks for that.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.last_failure_time: float = 0.0

    def allow_request(self) -> bool:
        now = self._clock()
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if now - self.last_failure_time >= self.cooldown_seconds:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        # HALF_OPEN allows a single trial request
        return True

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        self.last_failure_time = self._clock()
        if (
            self.state == CircuitState.HALF_OPEN
            or self.consecutive_failures >= self.failure_threshold
        ):
            self.state = CircuitState.OPEN

    def cooldown_remaining(self) -> float:
        """Seconds until this circuit allows a trial again; `0.0` when it does.

        A caller that has to answer "when?" — the outbox writing
        `next_attempt_at` — reads it here instead of restating the cooldown,
        which is what keeps the two from drifting apart.
        """
        if self.state != CircuitState.OPEN:
            return 0.0
        return max(0.0, self.cooldown_seconds - (self._clock() - self.last_failure_time))
