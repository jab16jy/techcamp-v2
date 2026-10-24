"""`UsageQuotaPort` against real Postgres (E12 T6; D-T12.1, D-T12.3;
docs/06-diseno-detallado.md §9; docs/11-metricas.md §métricas del asistente).

The use case is pure and the double is in memory; what this module proves is the
two things neither of them can: that the day and the month are the **local** ones
inside the database, and that the admission is atomic.

The atomicity is the load-bearing test here. R3-QUOTA-RACE was the CRITICAL T1
found: the port had no reservation, so two requests that both read nine questions
both took a slot. The adapter answers it with one transaction that holds
`pg_advisory_xact_lock` for the user, reads the count and the month inside it, and
writes the question inside it — so two concurrent admissions for the same user run
one after the other, and the second sees the first one's question.

The day and the month are derived from the running clock, because the rows that
`admit_question` writes carry `server_default=now()`: the database's clock, not a
timestamp a test can pass in (that is the point of D-T12.1 — the count has to be a
function of what was written, not of what a writer claimed). The boundaries are
then built backwards from the local day, so the five hours either side of UTC
midnight are still the case under test.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text

from assistant.db_fixtures import seed_domain
from techcamp.assistant.adapters.repositories import SqlAlchemyUsageQuota
from techcamp.assistant.domain.errors import ConversationNotFoundError
from techcamp.assistant.domain.models import Message, MessageRole
from techcamp.shared.dates import BOGOTA_TZ, local_today
from techcamp.shared.db import async_session_factory

pytestmark = pytest.mark.anyio

_NOW = datetime.now(UTC)
_TODAY = local_today(_NOW)
_THIS_MONTH = _TODAY.replace(day=1)
_BOGOTA_MIDNIGHT = datetime.combine(_TODAY, time.min, tzinfo=BOGOTA_TZ)
_CAP = Decimal("20.5")


async def _conversation_of(db_session, user_id):
    result = await db_session.execute(
        text("SELECT id FROM conversation WHERE user_id = :user_id ORDER BY id LIMIT 1"),
        {"user_id": user_id},
    )
    conversation_id = result.scalar_one_or_none()
    if conversation_id is None:
        conversation_id = uuid4()
        await db_session.execute(
            text("INSERT INTO conversation (id, user_id, plot_id) VALUES (:id, :user_id, NULL)"),
            {"id": conversation_id, "user_id": user_id},
        )
        await db_session.commit()
    return conversation_id


async def _ask(db_session, *, user_id, content, at: datetime) -> None:
    """One stored question, stamped by hand rather than by the server's clock."""
    conversation_id = await _conversation_of(db_session, user_id)
    await db_session.execute(
        text(
            "INSERT INTO message (id, conversation_id, role, content, created_at) "
            "VALUES (:id, :conversation_id, 'user', :content, :at)"
        ),
        {"id": uuid4(), "conversation_id": conversation_id, "content": content, "at": at},
    )
    await db_session.commit()


async def _answer_with_cost(
    db_session, *, user_id, cost_cop: Decimal | None, at: datetime | None = None
) -> None:
    conversation_id = await _conversation_of(db_session, user_id)
    await db_session.execute(
        text(
            "INSERT INTO message (id, conversation_id, role, content, model, input_tokens, "
            "output_tokens, cost_cop, created_at) VALUES (:id, :conversation_id, 'assistant', "
            "'respuesta', 'deepseek-chat', 10, 20, :cost, COALESCE(:at, now()))"
        ),
        {"id": uuid4(), "conversation_id": conversation_id, "cost": cost_cop, "at": at},
    )
    await db_session.commit()


def _question(conversation_id) -> Message:
    return Message(
        id=uuid4(),
        conversation_id=conversation_id,
        role=MessageRole.USER,
        content="¿Por qué no regué ayer?",
        created_at=_NOW,
    )


def _admit(quota: SqlAlchemyUsageQuota, *, user_id, conversation_id, day=_TODAY, month=_THIS_MONTH):
    return quota.admit_question(
        user_id=user_id,
        conversation_id=conversation_id,
        question=_question(conversation_id),
        day=day,
        month=month,
        budget_cap_cop=_CAP,
    )


async def test_a_day_with_no_question_counts_zero(db_session) -> None:
    domain = await seed_domain(db_session)

    assert (
        await SqlAlchemyUsageQuota(db_session).questions_asked_on(
            user_id=domain.user_id, day=_TODAY
        )
        == 0
    )


async def test_the_day_is_the_local_one_and_not_the_utc_one(db_session) -> None:
    """Three hours before local midnight is 02:00Z on _today_: the UTC date has
    already rolled over, and a `date(created_at)` would hand the question to the
    day that has not started yet in Colombia."""
    domain = await seed_domain(db_session)

    await _ask(
        db_session,
        user_id=domain.user_id,
        content="p1",
        at=_BOGOTA_MIDNIGHT - timedelta(hours=3),
    )

    quota = SqlAlchemyUsageQuota(db_session)
    assert await quota.questions_asked_on(user_id=domain.user_id, day=_TODAY) == 0
    assert (
        await quota.questions_asked_on(user_id=domain.user_id, day=_TODAY - timedelta(days=1)) == 1
    )


async def test_the_morning_after_utc_midnight_is_still_the_previous_local_day(db_session) -> None:
    """The other side of the same boundary: 07:00Z is 02:00 in Bogotá, so the UTC
    date and the local date agree here — the case a `date(created_at)` gets right by
    accident and that pins the boundary from the other end."""
    domain = await seed_domain(db_session)

    await _ask(
        db_session,
        user_id=domain.user_id,
        content="p1",
        at=_BOGOTA_MIDNIGHT + timedelta(hours=2),
    )

    assert (
        await SqlAlchemyUsageQuota(db_session).questions_asked_on(
            user_id=domain.user_id, day=_TODAY
        )
        == 1
    )


async def test_a_question_of_the_last_evening_of_the_local_day_counts(db_session) -> None:
    domain = await seed_domain(db_session)

    await _ask(
        db_session,
        user_id=domain.user_id,
        content="p1",
        at=_BOGOTA_MIDNIGHT + timedelta(hours=23, minutes=30),
    )

    assert (
        await SqlAlchemyUsageQuota(db_session).questions_asked_on(
            user_id=domain.user_id, day=_TODAY
        )
        == 1
    )


async def test_another_users_questions_are_not_counted(db_session) -> None:
    domain = await seed_domain(db_session)

    for index in range(10):
        await _ask(db_session, user_id=domain.outsider_id, content=f"ajena {index}", at=_NOW)

    quota = SqlAlchemyUsageQuota(db_session)
    assert await quota.questions_asked_on(user_id=domain.user_id, day=_TODAY) == 0
    assert await quota.questions_asked_on(user_id=domain.outsider_id, day=_TODAY) == 10


async def test_only_questions_are_counted_and_not_the_answers(db_session) -> None:
    """A turn of the model is not a question: counting it would halve the daily
    limit for every producer whose answers arrive."""
    domain = await seed_domain(db_session)

    await _answer_with_cost(db_session, user_id=domain.user_id, cost_cop=Decimal("1.0"))

    assert (
        await SqlAlchemyUsageQuota(db_session).questions_asked_on(
            user_id=domain.user_id, day=_TODAY
        )
        == 0
    )


async def test_the_month_spend_sums_the_costs(db_session) -> None:
    domain = await seed_domain(db_session)

    await _answer_with_cost(db_session, user_id=domain.user_id, cost_cop=Decimal("12.5"))
    await _answer_with_cost(db_session, user_id=domain.user_id, cost_cop=Decimal("0.5"))

    assert await SqlAlchemyUsageQuota(db_session).month_spend_cop(
        user_id=domain.user_id, month=_THIS_MONTH
    ) == Decimal("13.0")


async def test_the_month_spend_ignores_a_missing_cost_the_way_the_domain_rule_does(
    db_session,
) -> None:
    """D-T12.1: the no-LLM answer stores `cost_cop IS NULL`. SQL `SUM` skips nulls,
    and `domain.limits.month_spend_cop` skips them too — this test is what keeps
    the two the same rule instead of two rules that happen to agree today."""
    domain = await seed_domain(db_session)

    await _answer_with_cost(db_session, user_id=domain.user_id, cost_cop=None)
    await _answer_with_cost(db_session, user_id=domain.user_id, cost_cop=None)
    await _answer_with_cost(db_session, user_id=domain.user_id, cost_cop=Decimal("5"))

    assert await SqlAlchemyUsageQuota(db_session).month_spend_cop(
        user_id=domain.user_id, month=_THIS_MONTH
    ) == Decimal("5")


async def test_a_month_with_nothing_billed_sums_to_zero_and_not_to_unknown(db_session) -> None:
    """No evidence of spending is evidence of no spending, and `SUM` over no rows is
    `NULL` — which the `coalesce` turns into the zero the cap comparison needs."""
    domain = await seed_domain(db_session)

    assert await SqlAlchemyUsageQuota(db_session).month_spend_cop(
        user_id=domain.user_id, month=_THIS_MONTH
    ) == Decimal("0")


async def test_the_month_is_the_local_one_and_not_the_utc_one(db_session) -> None:
    """21:00 on the last evening of the previous month is 02:00Z on the first of
    this one: it belongs to the month that ended, and reading it as this month's
    would spend the cap before the month started."""
    domain = await seed_domain(db_session)
    instant = _BOGOTA_MIDNIGHT.replace(day=1) - timedelta(hours=3)

    await _answer_with_cost(
        db_session,
        user_id=domain.user_id,
        cost_cop=Decimal("4.90"),
        at=instant,
    )

    quota = SqlAlchemyUsageQuota(db_session)
    previous_month = instant.astimezone(BOGOTA_TZ).date()
    assert instant.astimezone(UTC).date() == _THIS_MONTH
    assert await quota.month_spend_cop(user_id=domain.user_id, month=_THIS_MONTH) == Decimal("0")
    assert await quota.month_spend_cop(user_id=domain.user_id, month=previous_month) == Decimal(
        "4.90"
    )


async def test_another_users_spend_is_not_this_users(db_session) -> None:
    domain = await seed_domain(db_session)

    await _answer_with_cost(db_session, user_id=domain.outsider_id, cost_cop=Decimal("5"))

    assert await SqlAlchemyUsageQuota(db_session).month_spend_cop(
        user_id=domain.user_id, month=_THIS_MONTH
    ) == Decimal("0")


async def test_the_first_question_of_the_day_is_admitted_and_stored(db_session) -> None:
    domain = await seed_domain(db_session)
    conversation_id = await _conversation_of(db_session, domain.user_id)

    admitted = await _admit(
        SqlAlchemyUsageQuota(db_session), user_id=domain.user_id, conversation_id=conversation_id
    )

    assert admitted is True
    assert (
        await SqlAlchemyUsageQuota(db_session).questions_asked_on(
            user_id=domain.user_id, day=_TODAY
        )
        == 1
    )


async def test_the_tenth_question_of_the_day_is_admitted(db_session) -> None:
    """docs/04-api.md:22 — the tenth succeeds; only the eleventh is refused. The
    count is read before the question is written, so a turn does not count itself
    against the limit it is being measured by."""
    domain = await seed_domain(db_session)
    conversation_id = await _conversation_of(db_session, domain.user_id)
    for index in range(9):
        await _ask(db_session, user_id=domain.user_id, content=f"p{index}", at=_NOW)

    admitted = await _admit(
        SqlAlchemyUsageQuota(db_session), user_id=domain.user_id, conversation_id=conversation_id
    )

    assert admitted is True


async def test_the_eleventh_question_of_the_day_is_refused(db_session) -> None:
    domain = await seed_domain(db_session)
    conversation_id = await _conversation_of(db_session, domain.user_id)
    for index in range(10):
        await _ask(db_session, user_id=domain.user_id, content=f"p{index}", at=_NOW)

    admitted = await _admit(
        SqlAlchemyUsageQuota(db_session), user_id=domain.user_id, conversation_id=conversation_id
    )

    assert admitted is False


async def test_a_spent_budget_refuses_a_question_even_with_the_day_empty(db_session) -> None:
    domain = await seed_domain(db_session)
    conversation_id = await _conversation_of(db_session, domain.user_id)
    await _answer_with_cost(db_session, user_id=domain.user_id, cost_cop=Decimal("20.5"))

    admitted = await _admit(
        SqlAlchemyUsageQuota(db_session), user_id=domain.user_id, conversation_id=conversation_id
    )

    assert admitted is False


async def test_two_concurrent_admissions_at_the_ninth_question_take_one_between_them(
    db_session,
) -> None:
    """R3-QUOTA-RACE, as a real race rather than as a sequence.

    Two connections, two transactions, one user at nine questions. Without the
    advisory lock they both read nine, both decide there is room and the eleventh
    reaches the provider; with it, one is admitted and the other is refused. The
    two therefore cannot share a session: they have to be two checkouts of the pool.
    """
    domain = await seed_domain(db_session)
    conversation_id = await _conversation_of(db_session, domain.user_id)
    for index in range(9):
        await _ask(db_session, user_id=domain.user_id, content=f"p{index}", at=_NOW)

    async def admit_once() -> bool:
        async with async_session_factory() as session:
            return await _admit(
                SqlAlchemyUsageQuota(session),
                user_id=domain.user_id,
                conversation_id=conversation_id,
            )

    first, second = await asyncio.gather(admit_once(), admit_once())

    assert sorted([first, second]) == [False, True]
    # The day holds the nine that were there plus the two that were asked: the one
    # that was admitted and the one that was refused, because a refused turn was
    # still asked (the adapter stores it either way).
    assert (
        await SqlAlchemyUsageQuota(db_session).questions_asked_on(
            user_id=domain.user_id, day=_TODAY
        )
        == 11
    )


async def test_an_admission_of_a_conversation_that_is_not_the_callers_is_refused(
    db_session,
) -> None:
    """T5's R3-ownership-mismatch fix, on the quota side: the row written inside
    the admission belongs to `conversation_id`, so verifying one conversation must
    not let a caller write the turn into another user's transcript."""
    domain = await seed_domain(db_session)
    stranger_conversation = await _conversation_of(db_session, domain.outsider_id)
    question = Message(
        id=uuid4(),
        conversation_id=stranger_conversation,
        role=MessageRole.USER,
        content="¿Por qué no regué ayer?",
        created_at=_NOW,
    )

    with pytest.raises(ConversationNotFoundError):
        await SqlAlchemyUsageQuota(db_session).admit_question(
            user_id=domain.user_id,
            conversation_id=stranger_conversation,
            question=question,
            day=_TODAY,
            month=_THIS_MONTH,
            budget_cap_cop=_CAP,
        )


async def test_a_refused_question_is_stored_anyway(db_session) -> None:
    """A refused turn was still asked, and the degraded answer the caller writes
    next belongs to a transcript that holds the question it answers."""
    domain = await seed_domain(db_session)
    conversation_id = await _conversation_of(db_session, domain.user_id)
    for index in range(10):
        await _ask(db_session, user_id=domain.user_id, content=f"p{index}", at=_NOW)

    admitted = await _admit(
        SqlAlchemyUsageQuota(db_session), user_id=domain.user_id, conversation_id=conversation_id
    )

    assert admitted is False
    assert (
        await SqlAlchemyUsageQuota(db_session).questions_asked_on(
            user_id=domain.user_id, day=_TODAY
        )
        == 11
    )
