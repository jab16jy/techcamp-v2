"""The one use case that decides whether a question may reach the provider (E12 T1;
docs/06-diseno-detallado.md §9; docs/04-api.md §Límites de uso; D-T12.1, D-T12.3).

The decision is taken before anything else happens (docs/06 §9: `API->>API: límite
diario del usuario y presupuesto mensual`), and when it is a no the answer is the
degraded one with no provider call at all. This module therefore holds two reads,
no arithmetic of its own and no dependency on HTTP or a database: the cap
comparison, the remaining count and the month sum are the pure rules of
`assistant.domain.limits`.

The day and the month are resolved in America/Bogota (docs/06 §9), which is the
case a `date(created_at)` gets wrong for five hours every evening.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from assistant.doubles import (
    FakeConversationRepository,
    FakeMessageRepository,
    FakeUsageQuota,
)
from techcamp.assistant.application.usage import (
    DegradationReason,
    QuotaDecision,
    evaluate_quota,
)
from techcamp.assistant.domain.errors import ConversationNotFoundError
from techcamp.assistant.domain.models import Message, MessageEvidence, MessageRole

pytestmark = pytest.mark.anyio

_USER_ID = UUID("11111111-1111-1111-1111-111111111111")
_OTHER_USER_ID = UUID("22222222-2222-2222-2222-222222222222")
_PLOT_ID = UUID("33333333-3333-3333-3333-333333333333")
_CAP = Decimal("5")
"""The seminar profile's USD 5 total (ADR-0021:30); the production profile's cap
arrives through the same argument (ADR-0007:17)."""


async def _quota_with(
    *,
    questions: int = 0,
    costs: list[Decimal | None] | None = None,
    at: datetime = datetime(2026, 10, 6, 14, 0, tzinfo=UTC),
    user_id: UUID = _USER_ID,
) -> FakeUsageQuota:
    """A quota store holding `questions` questions and `costs` answers."""
    conversations = FakeConversationRepository()
    messages = FakeMessageRepository(conversations=conversations)
    quota = FakeUsageQuota(messages=messages)
    conversation = await conversations.create(user_id=user_id, plot_id=_PLOT_ID)
    for index in range(questions):
        await messages.add(
            user_id=user_id,
            conversation_id=conversation.id,
            message=Message(
                id=uuid4(),
                conversation_id=conversation.id,
                role=MessageRole.USER,
                content="¿Por qué no regué ayer?",
                created_at=at + timedelta(minutes=index),
            ),
        )
    for index, cost in enumerate(costs or []):
        await messages.add(
            user_id=user_id,
            conversation_id=conversation.id,
            message=Message(
                id=uuid4(),
                conversation_id=conversation.id,
                role=MessageRole.ASSISTANT,
                content="Respuesta",
                created_at=at + timedelta(minutes=questions + index),
                evidence=(
                    MessageEvidence.no_llm()
                    if cost is None
                    else MessageEvidence.from_call(
                        model="deepseek-chat",
                        prompt_tokens=1,
                        completion_tokens=1,
                        cost_cop=cost,
                    )
                ),
            ),
        )
    return quota


async def _decide(
    quota: FakeUsageQuota,
    *,
    cap: Decimal = _CAP,
    conversation_id: UUID | None = None,
) -> QuotaDecision:
    """The decision for one question, in the conversation `quota` already holds.

    `now` is the default instant the rest of this module decides with, so the
    question handed to `admit_question` is stamped at the same moment the decision
    is taken rather than at the wall clock.
    """
    now = datetime(2026, 10, 6, 23, 0, tzinfo=UTC)
    if conversation_id is None:
        owned = await quota.messages.conversations.list_for_user(user_id=_USER_ID)
        conversation_id = owned[0].id
    return await evaluate_quota(
        user_id=_USER_ID,
        budget_cap_cop=cap,
        quota=quota,
        question=Message(
            id=uuid4(),
            conversation_id=conversation_id,
            role=MessageRole.USER,
            content="una pregunta",
            created_at=now,
        ),
        now=now,
    )


async def test_a_first_question_of_the_day_is_allowed() -> None:
    decision = await _decide(await _quota_with())

    assert decision.allowed is True
    assert decision.reason is None
    # The admitted question is part of the count it reports: the figures answer
    # "how many of the day's ten may I still ask", and this turn already used one.
    assert decision.questions_asked == 1
    assert decision.questions_remaining == 9
    assert decision.month_spend_cop == Decimal(0)
    assert decision.budget_cap_cop == _CAP


async def test_the_tenth_question_of_the_day_is_allowed() -> None:
    """docs/04-api.md:22 — the tenth succeeds; only the eleventh is refused."""

    decision = await _decide(await _quota_with(questions=9))

    assert decision.allowed is True
    assert decision.questions_asked == 10
    assert decision.questions_remaining == 0


async def test_the_eleventh_question_of_the_day_is_refused() -> None:
    decision = await _decide(await _quota_with(questions=10))

    assert decision.allowed is False
    assert decision.reason is DegradationReason.DAILY_LIMIT
    assert decision.questions_remaining == 0


async def test_two_callers_racing_for_the_tenth_slot_take_one_between_them() -> None:
    """R3-QUOTA-RACE — the reason admission is the port's and not a read here.

    Both callers arrive when the day holds nine questions. A read followed by a
    later decision would answer both; the atomic reservation answers one.
    """
    quota = await _quota_with(questions=9)

    first = await _decide(quota)
    second = await _decide(quota)

    assert first.allowed is True
    assert first.questions_asked == 10
    assert second.allowed is False
    assert second.reason is DegradationReason.DAILY_LIMIT


async def test_the_counter_is_the_local_day_and_not_the_utc_one() -> None:
    """The case a naive `date(created_at)` misses.

    Ten questions asked at 2026-10-07T02:00Z are still the 6th in Bogota (21:00),
    so the eleventh of that local day is refused even though the UTC day has only
    just turned. The `now` below is 2026-10-06 local, the same local day.
    """
    quota = await _quota_with(questions=10, at=datetime(2026, 10, 7, 2, 0, tzinfo=UTC))

    decision = await _decide(quota)

    assert decision.allowed is False
    assert decision.reason is DegradationReason.DAILY_LIMIT


async def test_yesterdays_questions_do_not_count_against_today() -> None:
    quota = await _quota_with(questions=10, at=datetime(2026, 10, 5, 20, 0, tzinfo=UTC))

    decision = await _decide(quota)

    assert decision.allowed is True
    assert decision.questions_asked == 1


async def test_a_month_below_the_cap_is_allowed() -> None:
    quota = await _quota_with(costs=[Decimal("2.00"), Decimal("2.99"), None])

    decision = await _decide(quota)

    assert decision.allowed is True
    assert decision.month_spend_cop == Decimal("4.99")


async def test_the_degraded_answers_cost_nothing_and_are_not_summed_as_zero() -> None:
    """D-T12.1: a `None` cost is missing evidence. Summing it as `0.00` would
    look the same here and would not in a budget that has to stop the provider."""
    quota = await _quota_with(costs=[None, None, Decimal("5")])

    decision = await _decide(quota)

    assert decision.month_spend_cop == Decimal("5")
    assert decision.allowed is False
    assert decision.reason is DegradationReason.BUDGET_EXHAUSTED


async def test_a_month_that_reached_the_cap_calls_no_provider() -> None:
    """The acceptance case: exhausted budget means the provider double is not
    invoked at all and the answer is the degraded one (docs/06 §9: `alt límite
    (10/día) o presupuesto agotado`)."""
    quota = await _quota_with(costs=[Decimal("5")])

    decision = await _decide(quota)

    assert decision.allowed is False
    assert decision.reason is DegradationReason.BUDGET_EXHAUSTED


async def test_the_cap_is_the_caller_s_and_only_the_caller_s() -> None:
    """D-T12.3: the 10/day cap is the same in both profiles and only the budget
    differs — USD 5 total in `seminar` (ADR-0021:30), the monthly cap in
    `production` (ADR-0007:17)."""
    quota = await _quota_with(costs=[Decimal("4.99")])

    assert (await _decide(quota, cap=Decimal("5"))).allowed is True
    assert (await _decide(quota, cap=Decimal("4.98"))).allowed is False


async def test_the_daily_limit_is_the_same_in_both_profiles() -> None:
    """The 10/day rule does not depend on the cap, so a profile with a large
    budget still answers only ten questions."""

    decision = await _decide(await _quota_with(questions=10), cap=Decimal("1000"))

    assert decision.reason is DegradationReason.DAILY_LIMIT


async def test_another_users_questions_and_costs_are_not_this_users() -> None:
    quota = await _quota_with(questions=10, costs=[Decimal("5")], user_id=_OTHER_USER_ID)
    mine = await quota.messages.conversations.create(user_id=_USER_ID, plot_id=_PLOT_ID)

    decision = await _decide(quota, conversation_id=mine.id)

    assert decision.allowed is True
    assert decision.questions_asked == 1
    assert decision.month_spend_cop == Decimal(0)


async def test_an_admission_in_a_strangers_conversation_is_refused() -> None:
    quota = await _quota_with()

    with pytest.raises(ConversationNotFoundError):
        await _decide(quota, conversation_id=uuid4())


async def test_the_month_spend_counts_the_local_month_and_not_the_utc_one() -> None:
    """2026-10-01T02:00Z is 2026-09-30 21:00 in Bogota: it belongs to September's
    spend, and reading it as October would charge this month's cap for it."""
    quota = await _quota_with(costs=[Decimal("4.90")], at=datetime(2026, 10, 1, 2, 0, tzinfo=UTC))

    decision = await _decide(quota)

    assert decision.month_spend_cop == Decimal(0)
    assert decision.allowed is True


async def test_the_reasons_read_as_one_of_the_two_documented_degradations() -> None:
    assert {reason.value for reason in DegradationReason} == {"daily_limit", "budget_exhausted"}


async def test_an_exhausted_day_and_an_exhausted_budget_read_as_the_day() -> None:
    """R3-QUOTA-REASON-PRECEDENCE: both limits refuse the turn, and the reported
    reason is the one the producer can act on tonight.

    The day resets at local midnight and `Retry-After` counts the seconds to it; a
    budget that ran out is a fact about the month. Naming the budget here would
    tell him to wait for something that does not free a single question, and would
    send `Retry-After` nowhere useful.
    """
    quota = await _quota_with(questions=10, costs=[Decimal("5")])

    decision = await _decide(quota)

    assert decision.allowed is False
    assert decision.reason is DegradationReason.DAILY_LIMIT
    assert decision.month_spend_cop == Decimal("5")


async def test_room_in_the_day_and_an_exhausted_budget_read_as_the_budget() -> None:
    quota = await _quota_with(costs=[Decimal("5")])

    decision = await _decide(quota)

    assert decision.allowed is False
    assert decision.reason is DegradationReason.BUDGET_EXHAUSTED


async def test_a_refusal_neither_figure_explains_has_no_reason() -> None:
    """A port that refuses without either limit being reached is not one of the
    two documented degradations, and naming one would be inventing a reason.

    The decision still says `allowed is False`, so the caller degrades; it simply
    does not claim to know why.
    """

    class _StubbornQuota(FakeUsageQuota):
        async def admit_question(self, **_kwargs: object) -> bool:
            return False

    conversations = FakeConversationRepository()
    conversation = await conversations.create(user_id=_USER_ID, plot_id=_PLOT_ID)
    quota = _StubbornQuota(messages=FakeMessageRepository(conversations=conversations))

    decision = await _decide(quota, conversation_id=conversation.id)

    assert decision.allowed is False
    assert decision.reason is None


async def test_the_question_is_stored_even_when_it_is_refused() -> None:
    """The port owns the reservation and writes the question either way: a refused
    turn was still asked, and the degraded answer belongs to a transcript that
    holds the question it answers (docs/06 §9: "guardar el mensaje")."""
    quota = await _quota_with(questions=10)
    conversation = (await quota.messages.conversations.list_for_user(user_id=_USER_ID))[0]

    decision = await _decide(quota)

    stored = await quota.messages.list_for_user(user_id=_USER_ID, conversation_id=conversation.id)
    assert decision.allowed is False
    assert [message.content for message in stored if message.role is MessageRole.USER][-1] == (
        "una pregunta"
    )


async def test_a_question_of_a_conversation_that_is_not_the_callers_is_refused() -> None:
    """The same check `MessageRepository.add` carries (T5's R3-ownership-mismatch
    fix): the row written below belongs to `conversation_id`, so a caller who
    verified one conversation must not be able to write the turn into another."""
    quota = await _quota_with()
    stranger = await quota.messages.conversations.create(user_id=_OTHER_USER_ID, plot_id=_PLOT_ID)

    with pytest.raises(ConversationNotFoundError):
        await _decide(quota, conversation_id=stranger.id)
