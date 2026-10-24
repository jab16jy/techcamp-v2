"""The assistant's two endpoints (E12 T5; D-T12.4; ADR-0027;
docs/04-api.md §Riesgo, métricas y asistente; docs/06-diseno-detallado.md §9;
D-T12.1, D-T12.5; docs/09-cuellos-de-botella.md §Seguridad).

```
POST /assistant/messages                              { conversation_id?, plot_id?, content }
     → text/event-stream (tokens + fuentes citadas)
POST /assistant/messages/{message_id}:feedback        { helpful } → 204
```

**The session is opened and closed around the work, never during the tokens**
(D-T12.4). This is the `R3-stream-holds-db-session` lesson of
`telemetry/adapters/api/router.py:446-485` applied to a stream that ends: FastAPI
tears a `yield` dependency down only after the whole response finishes, so a
session alive across tokens keeps a pooled connection checked out for as long as
the producer reads — and starves every REST endpoint of the pool. So:

1. one short session resolves the conversation, checks the plot, gathers the
   facts, retrieves the fragments, builds the prompt and **stores the question**;
2. the `StreamingResponse` is returned with a body that holds no session at all —
   it calls a `persist` callable which opens and closes its own.

**Order of the gates**, each one before the next, so a question about a plot the
caller may not see never reaches a repository:
conversation ownership (`404`), plot access through `resolve_plot_access`
(`404`, the same gate `home` and `risk` use), facts, retrieval, prompt, write.

**What is not here.** The 10-questions/day limit, the budget and the `429`
are T6's (D-T12.3, docs/04:22) and share this router; the degraded no-LLM
answer (`application.degraded_answer`, T4) is written when T6 wires the branch
that produces it. This task is the LLM path and its two endpoints, and it leaves
the degradation out rather than half-wiring it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.alerts.adapters.repositories import SqlAlchemyAlertRepository
from techcamp.assistant.adapters.api.deps import EmbedderDep, LLMClientDep, PricesDep
from techcamp.assistant.adapters.api.prompt import build_prompt
from techcamp.assistant.adapters.api.stream import PersistAnswer, stream_assistant_answer
from techcamp.assistant.adapters.repositories import (
    SqlAlchemyConversationRepository,
    SqlAlchemyKnowledgeRepository,
    SqlAlchemyMessageRepository,
)
from techcamp.assistant.application.plot_facts import gather_plot_facts
from techcamp.assistant.application.retrieval import RetrievedEvidence, retrieve_fragments
from techcamp.assistant.domain.facts import PlotFacts
from techcamp.assistant.domain.models import Conversation, Message, MessageEvidence, MessageRole
from techcamp.farms.adapters.repositories import SqlAlchemyPlotRepository
from techcamp.farms.domain.errors import PlotNotFoundError
from techcamp.identity.adapters.api.deps import CurrentUserId
from techcamp.identity.adapters.repositories import SqlAlchemyMembershipRepository
from techcamp.irrigation.adapters.repositories import (
    SqlAlchemyIrrigationRecommendationRepository,
)
from techcamp.risk.adapters.repositories import SqlAlchemyRiskRepository
from techcamp.shared.db import async_session_factory
from techcamp.shared.errors import ProblemError
from techcamp.shared.ids import uuid7
from techcamp.telemetry.adapters.repositories import SqlAlchemyReadingRepository

router = APIRouter(tags=["assistant"])

MAX_QUESTION_CHARS = 2000
"""An upper bound on `content`, because the question is sent to a provider and its
answer is paid for (ADR-0007:9, docs/02:78). Long enough for a producer's real
question, short enough that a pasted document is a `422` and not an invoice. The
limit belongs to this adapter, not to the domain: it is a property of the wire."""


class AskRequest(BaseModel):
    """`POST /assistant/messages` (docs/04-api.md:227)."""

    conversation_id: UUID | None = None
    plot_id: UUID | None = None
    content: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)

    model_config = {
        "extra": "forbid",
        "json_schema_extra": {
            "examples": [
                {
                    "plot_id": "0f9d2c1e-7a44-4a1f-9c8e-2b6d5f3a1c77",
                    "content": "¿Por qué no regué ayer?",
                }
            ]
        },
    }


class FeedbackRequest(BaseModel):
    """`POST /assistant/messages/{message_id}:feedback` (docs/04-api.md:228)."""

    helpful: bool

    model_config = {"extra": "forbid"}


@router.post("/assistant/messages")
async def ask(
    body: AskRequest,
    user_id: CurrentUserId,
    llm: LLMClientDep,
    embedder: EmbedderDep,
    prices: PricesDep,
) -> StreamingResponse:
    """One question, answered as a stream of tokens with its cited sources.

    The status code is decided before the first token: everything that can answer
    `404` (a conversation of another user, a plot of another organization)
    happens inside the short session below, and everything that can fail a
    provider call is resolved before the response exists.
    """
    async with async_session_factory() as session:
        knowledge = SqlAlchemyKnowledgeRepository(session)
        conversations = SqlAlchemyConversationRepository(session)
        messages = SqlAlchemyMessageRepository(session)

        conversation, facts = await _prepare(
            session,
            knowledge=knowledge,
            conversations=conversations,
            user_id=user_id,
            body=body,
        )
        evidence: RetrievedEvidence = await retrieve_fragments(
            question=body.content, embedder=embedder, knowledge=knowledge
        )
        prompt = build_prompt(facts=facts, evidence=evidence, question=body.content)
        await messages.add(
            user_id=user_id,
            conversation_id=conversation.id,
            message=Message(
                id=uuid7(),
                conversation_id=conversation.id,
                role=MessageRole.USER,
                content=body.content,
                created_at=datetime.now(UTC),
            ),
        )

    return StreamingResponse(
        stream_assistant_answer(
            llm=llm,
            prompt=prompt,
            citations=evidence.citations,
            prices=prices,
            persist=_persist_answer(user_id=user_id, conversation_id=conversation.id),
        ),
        media_type="text/event-stream",
        # Same two headers as the hub stream (`telemetry/adapters/api/router.py`):
        # no caching of a per-user answer, and no proxy buffering that would turn
        # a token stream into one response.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/assistant/messages/{message_id}:feedback", status_code=204)
async def set_feedback(
    message_id: UUID,
    body: FeedbackRequest,
    user_id: CurrentUserId,
) -> Response:
    """Rate one answer as helpful or not (docs/04-api.md:228).

    `204`, and `404` for a message of another user: `set_helpful` answers `False`
    for anything that user may not see, and a rating endpoint that answered `403`
    would reveal that the message exists (docs/04 §Convenciones HTTP).
    """
    async with async_session_factory() as session:
        messages = SqlAlchemyMessageRepository(session)
        marked = await messages.set_helpful(
            user_id=user_id, message_id=message_id, helpful=body.helpful
        )
    if not marked:
        raise ProblemError(status=404, title="Message not found")
    return Response(status_code=204)


async def _prepare(
    session: AsyncSession,
    *,
    knowledge: SqlAlchemyKnowledgeRepository,
    conversations: SqlAlchemyConversationRepository,
    user_id: UUID,
    body: AskRequest,
) -> tuple[Conversation, PlotFacts | None]:
    """The conversation this turn belongs to, and the facts the prompt may carry.

    Three cases, in the order the endpoint's signature implies:

    * **a conversation and no plot** — the turn continues that transcript and is
      answered about its plot, if the conversation names one. A `plot_id` sent
      with it is answered about *that* plot, and the conversation keeps the plot
      it was created with: a transcript is one subject, and re-reading it would
      make its `plot_id` a value that changes with the last question asked;
    * **a plot and no conversation** — the plot is checked first and the
      conversation is created after, so a question about a plot the caller may
      not see leaves no conversation behind;
    * **neither** — a general question, whose conversation is about no plot and
      whose prompt carries no fact block.

    `facts` is `None` exactly when there is no plot, which is a question about no
    plot rather than a question whose facts are missing.
    """
    if body.conversation_id is not None:
        conversation = await conversations.get_for_user(
            user_id=user_id, conversation_id=body.conversation_id
        )
        if conversation is None:
            raise ProblemError(status=404, title="Conversation not found")
        plot_id = body.plot_id or conversation.plot_id
        facts = await _facts_for_plot(
            session, knowledge=knowledge, user_id=user_id, plot_id=plot_id
        )
        return conversation, facts

    facts = await _facts_for_plot(
        session, knowledge=knowledge, user_id=user_id, plot_id=body.plot_id
    )
    conversation = await conversations.create(user_id=user_id, plot_id=body.plot_id)
    return conversation, facts


async def _facts_for_plot(
    session: AsyncSession,
    *,
    knowledge: SqlAlchemyKnowledgeRepository,
    user_id: UUID,
    plot_id: UUID | None,
) -> PlotFacts | None:
    """The plot's facts, or `None` when the question names no plot.

    `gather_plot_facts` resolves `resolve_plot_access` as its **first read**
    (docs/09-cuellos-de-botella.md §Seguridad), so a plot of another organization
    raises `PlotNotFoundError` before any facade is reached, and this maps that
    one error to `404` — never `403` and never an empty `200`.

    `plot_id=None` answers `None` without reading anything: the facades are
    about a plot, and a general question has none.
    """
    if plot_id is None:
        return None
    try:
        return await gather_plot_facts(
            user_id=user_id,
            plot_id=plot_id,
            now=datetime.now(UTC),
            readings=SqlAlchemyReadingRepository(session),
            alerts=SqlAlchemyAlertRepository(session),
            recommendations=SqlAlchemyIrrigationRecommendationRepository(session),
            risk=SqlAlchemyRiskRepository(session),
            knowledge=knowledge,
            plots=SqlAlchemyPlotRepository(session),
            memberships=SqlAlchemyMembershipRepository(session),
        )
    except PlotNotFoundError as exc:
        raise ProblemError(status=404, title="Plot not found") from exc


def _persist_answer(*, user_id: UUID, conversation_id: UUID) -> PersistAnswer:
    """The body callable that stores the finished answer.

    It opens and closes **its own** session, around one insert, and returns the
    stored message so `done` carries the id of a row that exists (D-T12.4:
    nothing of the database's lifetime crosses a token).
    """

    async def persist(evidence: MessageEvidence, text: str) -> Message:
        async with async_session_factory() as session:
            messages = SqlAlchemyMessageRepository(session)
            return await messages.add(
                user_id=user_id,
                conversation_id=conversation_id,
                message=Message(
                    id=uuid7(),
                    conversation_id=conversation_id,
                    role=MessageRole.ASSISTANT,
                    content=text,
                    created_at=datetime.now(UTC),
                    evidence=evidence,
                ),
            )

    return persist
