"""The per-provider circuit in front of each channel (docs/06-diseno-detallado.md
§4; ADR-0016; D31, D35).

Pure, no database and no network: what is under test is the arithmetic docs/06 §4
states — "Por proveedor. Con 5 fallos seguidos se abre 5 min" — and that the state
lives in the process for as long as the worker does, which is the decision D35
records and no test can make for us.

No fake clock: `InProcessProviderCircuits` takes one, and these pass a mutable
one so a test moves time instead of waiting five minutes for it.
"""

from __future__ import annotations

import pytest

from techcamp.notifications.adapters.circuits import InProcessProviderCircuits
from techcamp.notifications.domain.models import (
    BREAKER_COOLDOWN,
    BREAKER_FAILURE_THRESHOLD,
    Channel,
)


class _Clock:
    """A monotonic clock a test moves by hand."""

    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


def _circuits() -> tuple[InProcessProviderCircuits, _Clock]:
    clock = _Clock()
    return InProcessProviderCircuits(clock=clock), clock


def test_the_thresholds_are_the_ones_the_doc_states() -> None:
    """docs/06 §4: "Con 5 fallos seguidos se abre 5 min"."""
    assert (BREAKER_FAILURE_THRESHOLD, BREAKER_COOLDOWN.total_seconds()) == (5, 300.0)


def test_four_consecutive_failures_still_allow_the_call() -> None:
    circuits, _ = _circuits()

    for _ in range(BREAKER_FAILURE_THRESHOLD - 1):
        circuits.record_failure(Channel.SMS)

    assert circuits.allows(Channel.SMS) is True


def test_the_fifth_consecutive_failure_opens_the_circuit() -> None:
    circuits, _ = _circuits()

    for _ in range(BREAKER_FAILURE_THRESHOLD):
        circuits.record_failure(Channel.SMS)

    assert circuits.allows(Channel.SMS) is False


def test_one_success_resets_the_consecutive_count() -> None:
    """ "5 fallos SEGUIDOS": four failures, a delivery that lands, then four more
    is still a healthy provider, and a breaker that only counted failures in its
    lifetime would have opened the circuit on a provider that is answering."""
    circuits, _ = _circuits()

    for _ in range(BREAKER_FAILURE_THRESHOLD - 1):
        circuits.record_failure(Channel.SMS)
    circuits.record_success(Channel.SMS)
    for _ in range(BREAKER_FAILURE_THRESHOLD - 1):
        circuits.record_failure(Channel.SMS)

    assert circuits.allows(Channel.SMS) is True


def test_a_circuit_opens_again_for_ever_after_its_cooldown() -> None:
    """The doc says five minutes and not "at most five minutes", so nothing here
    hands out a trial before the cooldown is over. The negative half is the
    assert right after: a second of clock proves it is not just elapsed time."""
    circuits, clock = _circuits()
    for _ in range(BREAKER_FAILURE_THRESHOLD):
        circuits.record_failure(Channel.SMS)

    clock.advance(BREAKER_COOLDOWN.total_seconds() - 1)
    assert circuits.allows(Channel.SMS) is False
    assert circuits.cooldown_remaining(Channel.SMS) == pytest.approx(1.0)

    clock.advance(1)
    assert circuits.allows(Channel.SMS) is True


def test_a_trial_that_fails_reopens_the_circuit_for_the_whole_cooldown() -> None:
    circuits, clock = _circuits()
    for _ in range(BREAKER_FAILURE_THRESHOLD):
        circuits.record_failure(Channel.SMS)
    clock.advance(BREAKER_COOLDOWN.total_seconds())

    assert circuits.allows(Channel.SMS) is True
    circuits.record_failure(Channel.SMS)
    clock.advance(BREAKER_COOLDOWN.total_seconds() - 1)
    assert circuits.allows(Channel.SMS) is False


def test_a_trial_that_lands_closes_the_circuit() -> None:
    circuits, clock = _circuits()
    for _ in range(BREAKER_FAILURE_THRESHOLD):
        circuits.record_failure(Channel.SMS)
    clock.advance(BREAKER_COOLDOWN.total_seconds())
    circuits.allows(Channel.SMS)

    circuits.record_success(Channel.SMS)

    assert circuits.allows(Channel.SMS) is True
    assert circuits.cooldown_remaining(Channel.SMS) == 0.0


def test_each_channel_has_its_own_circuit() -> None:
    """ "POR PROVEEDOR": a push service that is down says nothing about the SMS
    provider, and one outage must not take the other channel's rows with it."""
    circuits, _ = _circuits()
    for _ in range(BREAKER_FAILURE_THRESHOLD):
        circuits.record_failure(Channel.PUSH)

    assert circuits.allows(Channel.PUSH) is False
    assert circuits.allows(Channel.SMS) is True
    assert circuits.cooldown_remaining(Channel.SMS) == 0.0


def test_the_shared_registry_is_the_same_circuits_for_the_life_of_the_process() -> None:
    """D35: the count has to survive between two runs of the dispatch job, or
    every sweep starts from zero and the breaker never opens at all. One
    registry per process is what makes the count mean anything."""
    from techcamp.notifications.adapters import circuits as circuits_module

    assert circuits_module.provider_circuits() is circuits_module.provider_circuits()
