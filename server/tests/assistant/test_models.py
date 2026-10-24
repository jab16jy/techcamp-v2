"""What one turn of a conversation stores, and the evidence behind it (E12 T1;
docs/06-diseno-detallado.md §9; docs/03-modelo-datos.md §`kb_document`, `kb_chunk`,
`conversation` y `message`; D-T12.1).

`MessageEvidence` exists because of one rule: missing evidence is `null`, never
`0`, and `model IS NULL` is what tells "the LLM was never called" apart from "it
was called and cost nothing" (D-T12.1). A single row of four independent optional
columns cannot hold that rule by itself — `None` here is what makes the budget's
`SUM` ignore the degraded answers, and `0` would silently pay for them.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from techcamp.assistant.domain.errors import (
    HelpfulFlagOnUserMessageError,
    InvalidMessageEvidenceError,
)
from techcamp.assistant.domain.models import (
    ChatMessage,
    Conversation,
    Message,
    MessageEvidence,
    MessageRole,
)

_AT = datetime(2026, 10, 6, 14, 30, tzinfo=UTC)
_MESSAGE_ID = UUID("11111111-1111-1111-1111-111111111111")
_CONVERSATION_ID = UUID("22222222-2222-2222-2222-222222222222")
_USER_ID = UUID("33333333-3333-3333-3333-333333333333")


def test_a_turn_is_written_by_the_user_or_by_the_assistant() -> None:
    assert {role.value for role in MessageRole} == {"user", "assistant"}


def test_a_conversation_carries_its_owner_its_plot_and_nothing_else() -> None:
    """`conversation` is exactly `id`, `user_id` and `plot_id`
    (docs/03-modelo-datos.md:363-366) — **no `org_id`**: the isolation is the
    owning user plus the access to the plot (D-T12.5, row `org_id` en
    `conversation`). A second source of truth that can disagree with the plot is
    what docs/03 decides against, so the field list is pinned here."""
    assert [field.name for field in fields(Conversation)] == ["id", "user_id", "plot_id"]


def test_a_question_that_names_no_plot_still_has_a_conversation() -> None:
    """docs/04-api.md §Riesgo, métricas y asistente:
    `POST /assistant/messages { conversation_id?, plot_id?, content }` — a question
    without a plot is part of the contract, so the plot is optional."""
    conversation = Conversation(id=_CONVERSATION_ID, user_id=_USER_ID)

    assert conversation.plot_id is None
    assert conversation.user_id == _USER_ID


def test_the_outbound_chat_message_carries_only_a_role_and_its_text() -> None:
    """The provider receives these and nothing else (ADR-0007:39, D-T12.6): the
    role decides who is speaking, the content is the block T5 validated."""
    message = ChatMessage(role=MessageRole.USER, content="¿Por qué no regué ayer?")

    assert message.role is MessageRole.USER
    assert message.content == "¿Por qué no regué ayer?"


def test_the_no_llm_answer_reports_no_model_no_tokens_and_no_cost() -> None:
    evidence = MessageEvidence.no_llm()

    assert evidence.model is None
    assert evidence.prompt_tokens is None
    assert evidence.completion_tokens is None
    assert evidence.cost_cop is None
    assert evidence.called is False
    assert evidence.costs_nothing is False


def test_a_call_reports_its_model_its_tokens_and_its_cost() -> None:
    evidence = MessageEvidence.from_call(
        model="deepseek-chat",
        prompt_tokens=420,
        completion_tokens=180,
        cost_cop=Decimal("12.5"),
    )

    assert evidence.model == "deepseek-chat"
    assert evidence.prompt_tokens == 420
    assert evidence.completion_tokens == 180
    assert evidence.cost_cop == Decimal("12.5")
    assert evidence.called is True


def test_a_call_that_cost_nothing_is_not_an_answer_that_was_never_produced() -> None:
    """The distinction D-T12.1 exists for: `model IS NULL` means "no se llamó al
    LLM", and a call that happened and came free reports its model."""
    free_call = MessageEvidence.from_call(
        model="deepseek-chat",
        prompt_tokens=420,
        completion_tokens=180,
        cost_cop=Decimal(0),
    )

    assert free_call.costs_nothing is True
    assert free_call.called is True
    assert free_call != MessageEvidence.no_llm()


def test_evidence_that_reports_a_model_but_no_tokens_is_refused() -> None:
    with pytest.raises(InvalidMessageEvidenceError):
        MessageEvidence(
            model="deepseek-chat", prompt_tokens=None, completion_tokens=180, cost_cop=None
        )


def test_evidence_that_reports_tokens_for_a_model_never_called_is_refused() -> None:
    """A `0` where the model is missing is missing evidence written as a number,
    which is what D-T12.1 forbids."""
    with pytest.raises(InvalidMessageEvidenceError):
        MessageEvidence(model=None, prompt_tokens=0, completion_tokens=0, cost_cop=Decimal(0))


def test_negative_tokens_are_refused() -> None:
    with pytest.raises(InvalidMessageEvidenceError):
        MessageEvidence.from_call(
            model="deepseek-chat",
            prompt_tokens=-1,
            completion_tokens=180,
            cost_cop=Decimal("1"),
        )


def test_a_negative_cost_is_refused() -> None:
    with pytest.raises(InvalidMessageEvidenceError):
        MessageEvidence.from_call(
            model="deepseek-chat",
            prompt_tokens=420,
            completion_tokens=180,
            cost_cop=Decimal("-1"),
        )


def test_a_call_without_a_model_name_is_refused() -> None:
    with pytest.raises(InvalidMessageEvidenceError):
        MessageEvidence.from_call(
            model="", prompt_tokens=420, completion_tokens=180, cost_cop=Decimal(0)
        )


def _assistant_message(**overrides: object) -> Message:
    defaults: dict[str, object] = {
        "id": _MESSAGE_ID,
        "conversation_id": _CONVERSATION_ID,
        "role": MessageRole.ASSISTANT,
        "content": "Riegue postponido por falta de humedad.",
        "created_at": _AT,
    }
    return Message(**{**defaults, **overrides})


def test_a_stored_message_defaults_to_the_no_llm_evidence() -> None:
    message = _assistant_message()

    assert message.evidence == MessageEvidence.no_llm()
    assert message.helpful is None


def test_an_assistant_answer_carries_its_evidence() -> None:
    message = _assistant_message(
        evidence=MessageEvidence.from_call(
            model="deepseek-chat",
            prompt_tokens=420,
            completion_tokens=180,
            cost_cop=Decimal("12.5"),
        )
    )

    assert message.evidence.model == "deepseek-chat"


def test_only_an_assistant_answer_can_be_marked_helpful() -> None:
    """D-T12.1's CHECK, one layer earlier: `helpful IS NULL OR role =
    'assistant'` (docs/03-modelo-datos.md §`kb_document`, `kb_chunk`,
    `conversation` y `message`)."""

    marked = _assistant_message(helpful=True)

    assert marked.helpful is True


def test_a_question_of_the_user_cannot_be_marked_helpful() -> None:
    with pytest.raises(HelpfulFlagOnUserMessageError):
        _assistant_message(role=MessageRole.USER, helpful=True)
