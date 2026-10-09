"""The daily limit and the monthly budget as one decision (E12 T1;
docs/06-diseno-detallado.md §9; docs/04-api.md §Límites de uso; D-T12.1, D-T12.3).

docs/06 §9 draws the first fork of the assistant's flow: the user's daily limit
and the monthly budget are checked **before** anything else, and when either is
exhausted the answer is the degraded one — the plot's own facts plus the relevant
documents — with no provider call at all and `model`, tokens and `cost_cop` stored
null.

This use case is that fork and nothing else: two reads through
`UsageQuotaPort`, the pure rules of `assistant.domain.limits`, and a
`QuotaDecision` value. No HTTP, no database, no LLM port — which is what makes
"budget exhausted → no LLM call at all" a structural fact rather than a promise:
this function has no way to reach a provider.

The cap arrives as an argument because it is the one thing that differs per
profile (D-T12.3): USD 5 in total for `seminar` (ADR-0021:30), a monthly cap in
`production` (ADR-0007:17). The 10-questions-per-day rule is the same in both.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from techcamp.assistant.application.ports import UsageQuotaPort
from techcamp.assistant.domain.limits import (
    budget_exhausted,
    daily_limit_exhausted,
    questions_remaining,
)
from techcamp.assistant.domain.models import Message
from techcamp.shared.dates import local_today


class DegradationReason(StrEnum):
    """Why a question is answered without the model.

    The two branches docs/06 §9 draws, and nothing else: the day's cap, or the
    month's budget. Both produce the same degraded answer (the facts plus the
    relevant documents), so they share one answer and differ only in what the
    producer is told and in the `429` T6 writes for the first one.
    """

    DAILY_LIMIT = "daily_limit"
    BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass(frozen=True, slots=True)
class QuotaDecision:
    """What the caller needs to answer or to degrade, and nothing more.

    `questions_asked` / `questions_remaining` are for the response the producer
    reads; `month_spend_cop` / `budget_cap_cop` are the two figures behind
    `gasto del mes / tope` (docs/11-metricas.md §métricas del asistente). They
    travel together whether the answer is allowed or not, because the endpoint
    reports the budget in both cases and a degraded answer must still say why it
    is degraded.
    """

    allowed: bool
    reason: DegradationReason | None
    questions_asked: int
    questions_remaining: int
    month_spend_cop: Decimal
    budget_cap_cop: Decimal


async def evaluate_quota(
    *,
    user_id: UUID,
    budget_cap_cop: Decimal,
    quota: UsageQuotaPort,
    question: Message,
    now: datetime | None = None,
) -> QuotaDecision:
    """Whether this user may ask the provider one more question today.

    "Today" is the local day in America/Bogota (docs/06 §9: "el límite diario
    cuenta por `created_at` en la zona horaria del usuario"), read through
    `shared.dates.local_today` so one place owns the timezone, and the month is
    that local day first-of-month. `now` takes the instant so the decision is
    decided by the value under test; a naive one is read as UTC, the convention
    of every other read in the application.

    The daily limit is reported before the budget because it is the one the
    producer can act on tonight: the count resets at local midnight, and
    `seconds_until_local_midnight` is what the endpoint puts in `Retry-After`. A
    budget that ran out is a fact about the month, not about this evening.

    Admission itself is the port's and is atomic: this function hands the question
    to `quota.admit_question`, which stores it and says whether it may be asked at
    all. A refusal is what degrades it. Deciding here from a count that was read
    earlier would let two concurrent callers that both saw nine questions both take
    a slot (R3-QUOTA-RACE), which is the opposite of a hard limit.

    The question is written on both outcomes, by the port that owns the
    reservation: it was asked either way.
    """
    instant = now if now is not None else datetime.now(UTC)
    day = local_today(instant)
    month = day.replace(day=1)

    spend = await quota.month_spend_cop(user_id=user_id, month=month)
    admitted = await quota.admit_question(
        user_id=user_id,
        conversation_id=question.conversation_id,
        question=question,
        day=day,
        month=month,
        budget_cap_cop=budget_cap_cop,
    )
    # Read after the reservation, so the reported figures include this very
    # question: `questions_remaining` is what the producer can still ask, not what
    # he could have asked before this turn was admitted.
    asked_today = await quota.questions_asked_on(user_id=user_id, day=day)

    reason: DegradationReason | None = None
    if not admitted:
        # The day's limit is the reported one when the day is exhausted, because
        # that is the branch the producer can act on tonight. The budget is only
        # reported when the day still has room and the month is what stopped him.
        # A refusal neither figure explains keeps `reason is None` beside
        # `allowed is False`, so the producer reads a degraded answer rather than a
        # reason the system did not give (R3-QUOTA-REASON-PRECEDENCE).
        if daily_limit_exhausted(asked_today):
            reason = DegradationReason.DAILY_LIMIT
        elif budget_exhausted(spend, budget_cap_cop):
            reason = DegradationReason.BUDGET_EXHAUSTED

    return QuotaDecision(
        allowed=admitted,
        reason=reason,
        questions_asked=asked_today,
        questions_remaining=questions_remaining(asked_today),
        month_spend_cop=spend,
        budget_cap_cop=budget_cap_cop,
    )
