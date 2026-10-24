"""The assistant's usage limits as pure arithmetic (E12 T1; docs/04-api.md §Límites
de uso; docs/06-diseno-detallado.md §9; D-T12.1, D-T12.3).

Three rules the whole assistant answers to, with no database and no provider
behind them:

* **10 questions per day per user**, enforced in both profiles (D-T12.3), with the
  counter counted by `created_at` **in America/Bogota** (docs/06 §9). The day is
  the user's, not UTC's: a `date(created_at)` on a UTC instant is wrong for five
  hours of every evening, and it is wrong precisely when a producer is most
  likely to ask why he did not irrigate.
* **Top 5 fragments at similarity ≥ 0.75** for retrieval (ADR-0008:12), named
  constants because the acceptance criterion forbids the literal inside a query.
* **The month's spend against the profile's cap**, summing `message.cost_cop`
  where a missing cost is ignored rather than added as a zero (D-T12.1).

The timezone is read from `techcamp.shared.dates.BOGOTA_TZ`, the one place that
owns it (docs/06 §5): a second `ZoneInfo("America/Bogota")` here would be a
second source of truth for the product's only timezone.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from techcamp.shared.dates import BOGOTA_TZ

DAILY_QUESTION_LIMIT = 10
"""Questions per user per day (docs/04-api.md §Límites de uso: "asistente 10
preguntas/día por usuario"), enforced in **both** profiles with the same `429`
contract; only the budget cap changes per profile (D-T12.3)."""

RETRIEVAL_TOP_K = 5
"""How many fragments a question retrieves (ADR-0008:12: "los 5 fragmentos más
cercanos"; docs/06 §9: "top 5 fragmentos (similitud ≥ 0,75)")."""

SIMILARITY_FLOOR = 0.75
"""Minimum cosine similarity a fragment needs to be part of the answer
(ADR-0008:12). A float, not a `Decimal`: it is a read of pgvector's distance,
never a stored amount."""


def questions_remaining(asked_today: int) -> int:
    """How many of the day's questions are still available, never below zero.

    A count read after an eleventh question was already accepted (a retried
    request, a re-run job) reports `0`, not a negative number: the producer is
    shown this figure and a negative one is a claim the system never made.
    """
    return max(0, DAILY_QUESTION_LIMIT - asked_today)


def daily_limit_exhausted(asked_today: int) -> bool:
    """True once the day holds `DAILY_QUESTION_LIMIT` questions.

    The tenth question is still answered and the eleventh is not
    (docs/04-api.md:22), so the test is `>=` and not `>`.
    """
    return asked_today >= DAILY_QUESTION_LIMIT


def seconds_until_local_midnight(now: datetime | None = None) -> int:
    """Seconds until the next America/Bogota midnight: the `Retry-After` value of
    the daily-limit `429` (docs/04-api.md §Límites de uso).

    Seconds, not an HTTP-date: the header is the value the client can act on
    without reading the server's clock.

    The wait is always at least one second and at most a full day. A request that
    lands exactly at local midnight is a request of the **new** day, so it waits a
    whole day rather than nothing.
    """
    local_now = _in_bogota(now)
    next_midnight = datetime.combine(
        local_now.date() + timedelta(days=1), time.min, tzinfo=BOGOTA_TZ
    )
    return max(1, math.ceil((next_midnight - local_now).total_seconds()))


def local_day_bounds(day: date) -> tuple[datetime, datetime]:
    """The UTC instants that bound `day` in America/Bogota, `[start, end)`.

    `created_at` is `timestamptz` and is written by the database's clock, so a row
    is stored as an instant. Counting "the questions of the day"
    (docs/04-api.md:22) is therefore a comparison of instants against the local
    day's boundaries, and the boundaries are the local midnight translated to
    UTC: 2026-10-06 in Bogotá is `05:00Z` to `05:00Z`, not `00:00Z` to `00:00Z`.

    Pure, because the answer is a property of the calendar and not of the row: the
    same rule has to hold in the query that counts and in the test that proves it.
    """
    start = datetime.combine(day, time.min, tzinfo=BOGOTA_TZ)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def local_month_bounds(month: date) -> tuple[datetime, datetime]:
    """The UTC instants that bound the local month `month` belongs to.

    Same rule as `local_day_bounds`, for the monthly budget (docs/11:132,
    `SUM(message.cost_cop)` over `created_at`). Only the month of the argument is
    read, so a date from the middle of the month is the month it belongs to, and a
    29th to 31st that does not exist in the next month still bounds its own.

    December is named explicitly because `start.replace(month=13)` is a
    `ValueError`: the month after December is January of the next year.
    """
    start = datetime.combine(month.replace(day=1), time.min, tzinfo=BOGOTA_TZ)
    if month.month == 12:
        following = start.replace(year=month.year + 1, month=1)
    else:
        following = start.replace(month=month.month + 1)
    return start.astimezone(UTC), following.astimezone(UTC)


def month_spend_cop(costs: Iterable[Decimal | None]) -> Decimal:
    """Total pesos the given costs add up to, ignoring the missing ones.

    D-T12.1: the no-LLM answer stores `cost_cop IS NULL` and SQL `SUM` ignores
    NULL, which is exactly the budget behaviour wanted — a day without a billed
    answer does not spend. A missing cost is skipped, never added as `0.00`, so a
    `None` cannot hide behind a zero in an audit.

    A month with no costs at all sums to `0`, not to "unknown": the budget
    decision compares a number against the cap, and no evidence of spending is
    evidence of no spending.
    """
    return sum((cost for cost in costs if cost is not None), Decimal(0))


def budget_exhausted(spend_cop: Decimal, cap_cop: Decimal) -> bool:
    """True once the month's spend has reached the profile's cap.

    Reaching the cap is enough (`>=`), so the cap is a budget that is spent, not
    one that can be exceeded. A cap of `0` is a profile that measures money and has
    no cap configured, and is therefore exhausted: it stops answering instead of
    answering for free. The caller passes a cap only when the profile measures
    money (`None` never reaches this function, D-T6.1).
    """
    return spend_cop >= cap_cop


def _in_bogota(now: datetime | None) -> datetime:
    """`now` (defaulting to the current instant) in America/Bogota.

    A naive `now` is read as UTC, the convention `shared.dates.local_today`
    applies to every instant the application passes around (docs/06 §5): a naive
    value can never silently become local time.
    """
    if now is None:
        return datetime.now(UTC).astimezone(BOGOTA_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(BOGOTA_TZ)
