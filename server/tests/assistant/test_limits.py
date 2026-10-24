"""Pure limit rules of the assistant: the daily question cap, the retrieval
constants and the month spend (E12 T1).

No database and no clock read of their own: every rule takes the values it
decides on, so the cases below are decided by the numbers under test. The
timezone is not a detail here — the cap resets at America/Bogota midnight
(docs/06-diseno-detallado.md §9), which is not the UTC day boundary a naive
`created_at.date()` would use, and that difference is a test of its own.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from techcamp.assistant.domain.limits import (
    DAILY_QUESTION_LIMIT,
    RETRIEVAL_TOP_K,
    SIMILARITY_FLOOR,
    budget_exhausted,
    daily_limit_exhausted,
    month_spend_cop,
    questions_remaining,
    seconds_until_local_midnight,
)
from techcamp.shared.dates import BOGOTA_TZ


def test_the_daily_cap_is_ten_questions_in_both_profiles() -> None:
    # docs/04-api.md §Límites de uso: 10 preguntas/día por usuario, enforced in
    # both profiles (D-T12.3). Only the budget cap differs per profile.
    assert DAILY_QUESTION_LIMIT == 10


def test_retrieval_takes_five_fragments_at_or_above_point_seven_five() -> None:
    # ADR-0008:12 — "los 5 fragmentos más cercanos con similitud ≥ 0,75". A named
    # constant, because the acceptance criterion forbids a literal in a query.
    assert RETRIEVAL_TOP_K == 5
    assert SIMILARITY_FLOOR == 0.75


def test_questions_remaining_counts_down_to_the_cap() -> None:
    assert questions_remaining(0) == 10
    assert questions_remaining(9) == 1


def test_questions_remaining_is_never_negative() -> None:
    """An eleventh question answered before the count was read (a retry, a job
    re-run) must not produce a remaining count below zero: the number is shown to
    the producer, and a negative one is a claim the system never made."""
    assert questions_remaining(10) == 0
    assert questions_remaining(37) == 0


def test_the_tenth_question_is_allowed_and_the_eleventh_is_not() -> None:
    # docs/04-api.md:22 — the tenth succeeds, the eleventh answers 429.
    assert daily_limit_exhausted(9) is False
    assert daily_limit_exhausted(10) is True


def test_a_day_with_no_question_is_not_exhausted() -> None:
    assert daily_limit_exhausted(0) is False


def test_retry_after_counts_the_seconds_to_the_next_bogota_midnight() -> None:
    # docs/04-api.md:22 asks for `Retry-After` in seconds, computed against the
    # user's timezone: America/Bogota (docs/06 §9).
    now = datetime(2026, 10, 6, 21, 0, tzinfo=UTC)  # 16:00 in Bogota

    assert seconds_until_local_midnight(now) == 8 * 3600


def test_retry_after_does_not_wait_for_utc_midnight() -> None:
    """The case a naive `date(created_at)` misses.

    2026-10-07T02:00Z is 2026-10-06 21:00 in Bogota: the local day has **not**
    rolled over yet, so the wait until the quota resets is three hours, not the 22
    hours until UTC midnight. Waiting for the UTC boundary would keep denying the
    producer questions he is entitled to for the whole evening.
    """
    now = datetime(2026, 10, 7, 2, 0, tzinfo=UTC)

    assert now.date() == date(2026, 10, 7)  # the UTC day already rolled over
    assert seconds_until_local_midnight(now) == 3 * 3600


def test_retry_after_is_always_at_least_one_second() -> None:
    """At 23:59:59.5 local the wait is half a second; a `Retry-After: 0` would
    invite the client to hammer the endpoint, so the value is rounded up."""
    now = datetime(2026, 10, 6, 23, 59, 59, 500_000, tzinfo=BOGOTA_TZ)

    assert seconds_until_local_midnight(now) == 1


def test_retry_after_from_local_midnight_waits_a_whole_day() -> None:
    """A request that lands exactly at local midnight starts a new day, so the
    quota resets one full day later and not immediately."""
    midnight_bogota = datetime(2026, 10, 7, 5, 0, tzinfo=UTC)  # 00:00 in Bogota

    assert seconds_until_local_midnight(midnight_bogota) == 24 * 3600


def test_month_spend_adds_the_costs_and_ignores_the_missing_ones() -> None:
    """D-T12.1: the no-LLM answer stores `cost_cop IS NULL`, and SQL `SUM`
    ignores NULL — which is the budget behavior the design wants."""
    assert month_spend_cop([Decimal("12.5"), None, Decimal("0.5"), None]) == Decimal("13.0")


def test_month_spend_of_a_month_with_no_billed_answer_is_zero() -> None:
    """No billed answer is no spend, not an unknown spend: the budget decision
    needs a number to compare against the cap."""
    assert month_spend_cop([]) == Decimal(0)
    assert month_spend_cop([None, None]) == Decimal(0)


def test_a_real_zero_cost_answer_does_not_change_the_sum() -> None:
    # A call that really cost nothing (`cost_cop = 0`) is counted; a missing cost
    # is not (D-T12.1: la ausencia de evidencia es `null`, nunca `0`).
    assert month_spend_cop([Decimal(0), None]) == Decimal(0)


def test_the_budget_is_exhausted_once_the_month_reaches_the_cap() -> None:
    assert budget_exhausted(Decimal("4.99"), Decimal("5")) is False
    assert budget_exhausted(Decimal("5"), Decimal("5")) is True
    assert budget_exhausted(Decimal("5.01"), Decimal("5")) is True


def test_a_month_that_has_spent_nothing_is_not_over_a_positive_cap() -> None:
    assert budget_exhausted(Decimal(0), Decimal("5")) is False


def test_a_cap_of_zero_is_already_exhausted() -> None:
    """A profile configured without a budget must not answer every question: the
    cap of zero is the degenerate cap, and it is exhausted."""
    assert budget_exhausted(Decimal(0), Decimal(0)) is True


def test_retry_after_of_a_naive_instant_reads_it_as_utc() -> None:
    """The application's own convention (docs/06 §5): instants travel aware, and a
    naive value is read as UTC so it cannot silently become local time."""
    assert seconds_until_local_midnight(datetime(2026, 10, 7, 2, 0)) == 3 * 3600


def test_retry_after_defaults_to_the_current_instant() -> None:
    """The only rule here that reads a clock, and only when the caller omits the
    instant: every test above passes one, so each case is decided by its value."""
    assert 1 <= seconds_until_local_midnight() <= 24 * 3600
