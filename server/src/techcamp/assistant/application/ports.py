"""Ports of the assistant (E12 T1; ADR-0002, ADR-0007:13-14,26; ADR-0008;
ADR-0026; docs/05-arquitectura.md §Reglas; D-T12.5).

Six interfaces, all of them external I/O, all declared here and implemented by
adapters in later tasks (a port exists only with two real implementations, or when
external I/O needs a test double — AGENTS.md §Architecture rules):

* `LLMClient` — one streamed call per request, provider-agnostic.
* `Embedder` — local embeddings, 384 dimensions.
* `KnowledgeRepository` — vector search over the corpus and the document lookup a
  citation is built from.
* `ConversationRepository` / `MessageRepository` — the transcript, scoped by user.
* `UsageQuotaPort` — what the daily limit and the monthly budget are decided on.

The dataclasses next to a port mirror one row of one table, like
`weather.application.ports.DailyWeatherRow` and the read-model rows of `metrics`:
`kb_chunk` and `kb_document` are projections of this module's own tables, so they
live next to the port that returns them and not in `domain`, which owns what the
assistant *decides* rather than what it stores.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol, runtime_checkable
from uuid import UUID

from techcamp.assistant.domain.models import ChatMessage, Conversation, Message

EMBEDDING_DIMENSION = 384
"""The width of every vector the assistant embeds or retrieves with.

Pinned by ADR-0026: `intfloat/multilingual-e5-small`, MIT, 384 dimensions, run
locally. T2 writes `kb_chunk.embedding` as `vector(384)` with an HNSW cosine
index, and T8 either confirms the pin or lands a rewrite migration of its own — the
dimension is a property of the model, never a preference of the caller."""


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """What the provider says a call cost in tokens (ADR-0026: the `usage` object
    of the last streamed frame)."""

    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True, slots=True)
class StreamToken:
    """One piece of a streamed answer.

    The **last** token of a stream carries `usage` and the `model` that answered:
    that is where `message.input_tokens`, `message.output_tokens`, `message.model`
    and therefore `message.cost_cop` come from (D-T12.1), so the evidence travels
    with the stream instead of being asked for afterwards. An earlier token
    carries text only, and `None` for both is not missing evidence — it is a frame
    that had none.
    """

    text: str
    usage: TokenUsage | None = None
    model: str | None = None


@runtime_checkable
class LLMClient(Protocol):
    """The assistant's one call to a language model (ADR-0007:14,26).

    One call per request, streamed. There is no tool loop, no agent framework and
    no second round trip: the facts are gathered and the fragments retrieved
    before this call, and the model only explains them (ADR-0007:16 — the LLM
    never computes irrigation, risk or doses, H1).

    The port says nothing about the provider. The adapter built in T3 speaks the
    OpenAI-compatible DeepSeek chat endpoint, `deepseek-chat` against
    `POST /chat/completions` with `stream: true` and a `data: [DONE]` close
    (ADR-0026); the Anthropic SDK is not used in v2. Keeping the port
    provider-agnostic is what makes that an adapter decision rather than a change
    to every use case.
    """

    def stream(self, messages: Sequence[ChatMessage]) -> AsyncIterator[StreamToken]:
        """The tokens of one answer, in order, the last one carrying `usage`.

        Declared as a plain method returning an `AsyncIterator` because an adapter
        implements it as an async generator: calling it opens the stream, and
        `async for` drains it.
        """
        ...


@runtime_checkable
class Embedder(Protocol):
    """Turns texts into the vectors the corpus is searched with (ADR-0008).

    Pinned to `intfloat/multilingual-e5-small` (ADR-0026): MIT, 384 dimensions and
    **local**, because the corpus does not leave the server (ADR-0007:39 — minimum
    data to the provider, no personal data). A local model still needs a port: it
    is external I/O to this process, and the ingestion job has to be testable
    without loading torch.
    """

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """One vector per text, in the same order, each `EMBEDDING_DIMENSION`
        floats long.

        A vector of another width is an error, never a padded or truncated one: the
        width is the column's width (`vector(384)`), so a mismatch would only
        surface as a database error after the model had already run.
        """
        ...


@dataclass(frozen=True, slots=True)
class RetrievedFragment:
    """One `kb_chunk` that a question retrieved (docs/03-modelo-datos.md:357-362).

    `similarity` is the cosine score that selected it (ADR-0008:12); the caller
    compares it against `SIMILARITY_FLOOR` and cites it, so it is evidence rather
    than a ranking detail. The stored embedding is not here: it is a column, and a
    384-float vector per fragment has no business in a prompt.
    """

    id: UUID
    document_id: UUID
    content: str
    similarity: float


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    """One `kb_document` of the corpus (docs/03-modelo-datos.md:347-355).

    `license` and `department` are the two fields ingestion has to fill and the
    seasonal-climate fact reads: ADR-0008:12 records source and licence per
    document, and `department = null` is how the national bulletin is told from a
    regional one (docs/06 §9: the department's bulletin, else the national).
    """

    id: UUID
    title: str
    kind: str
    source_url: str | None = None
    license: str | None = None
    published_on: date | None = None
    department: str | None = None
    enso_state: str | None = None


@runtime_checkable
class KnowledgeRepository(Protocol):
    """Reads the shared institutional corpus (ADR-0008; docs/06 §9).

    **The one repository of the whole system that filters by no organization.**
    `kb_document` and `kb_chunk` carry no `org_id` because the corpus is shared
    reference data — the Boletín Agroclimático Nacional and the Caribbean mesas
    técnicas bulletins — exactly like `municipality`, `model_version` and
    `risk_prediction` (D-T12.5; the precedent and its row are at
    docs/03-modelo-datos.md §`municipality`, `model_version` y `risk_prediction`).
    Isolation happens where the data leaves the server, when the answer cites the
    source, not in the table.

    There is no write side here on purpose: ingestion (T3) writes documents and
    chunks with an idempotency key of `(source_url, sha256)`
    (docs/03 §`kb_document`, `kb_chunk`, `conversation` y `message`, row
    `Unicidad de la ingesta`) and against the `vector(384)` column T2 creates, so
    that write is defined together with the schema it fits instead of being frozen
    a task earlier.
    """

    async def search_by_embedding(
        self, embedding: Sequence[float], *, top_k: int, similarity_floor: float
    ) -> list[RetrievedFragment]:
        """The closest chunks to `embedding`, best first, at most `top_k` of them
        and only those at or above `similarity_floor` (ADR-0008:12: five fragments
        at ≥ 0.75).

        Fewer than `top_k` results is a legitimate answer, not an error: a corpus
        that holds nothing close enough is evidence the assistant has to report as
        missing rather than fill in.
        """
        ...

    async def get_document(self, document_id: UUID) -> KnowledgeDocument | None:
        """The document behind a retrieved fragment, or `None` when there is none.

        This is what a citation is built from (source, licence, publication date),
        so a fragment whose document is gone cannot be cited as if it were
        complete.
        """
        ...

    async def latest_climate_bulletin(self, *, department: str | None) -> KnowledgeDocument | None:
        """The latest `agroclimatic_bulletin` of a department, else the national
        one, by `published_on`; `None` when the corpus holds none.

        docs/06-diseno-detallado.md §9: "el último boletín agroclimático del
        departamento de la finca, **o del nacional si no hay uno regional**, con
        su fecha de publicación", with the ENSO state ingestion writes onto
        `kb_document.enso_state`.

        **Why `department` is a nullable argument with a national fallback — the
        decision this task makes, and a deviation from T1's port shape.** The
        seasonal fact belongs to a plot's farm, and a farm's department would be
        the obvious argument, but **nothing in this repository maps a farm or a
        plot to a department**: `Farm.municipality_code` is free-text DIVIPOLA
        (`farms/domain/models.py`, whose own docstring says "no `municipality`
        table exists yet") and there is no lookup from a DIVIPOLA code to a
        department name anywhere in `farms`. Inventing one here would be a second
        place that decides Colombia's administrative divisions, which is
        `municipality`'s table when it exists. So the caller passes what it knows
        — `None` for a general question — and the national bulletin answers, which
        docs/06 §9 already allows as the regional one is absent. T3's ingestion
        stores `department` as free text on the document, so no join is needed for
        the fallback to be correct.
        """
        ...


@runtime_checkable
class ConversationRepository(Protocol):
    """The transcripts of the assistant, scoped by their owner (D-T12.5).

    `conversation` carries no `org_id`: it belongs to a user, and the plot it
    speaks about is already behind `resolve_plot_access` (docs/03 §`kb_document`,
    `kb_chunk`, `conversation` y `message`, row `org_id` en `conversation`).
    **Every method takes the `user_id`**, so another user's conversation is absent
    from every read and no caller can be handed a row that is not theirs
    (docs/09-cuellos-de-botella.md §Seguridad).
    """

    async def create(self, *, user_id: UUID, plot_id: UUID | None) -> Conversation:
        """A new conversation of that user, about that plot or about none."""
        ...

    async def get_for_user(self, *, user_id: UUID, conversation_id: UUID) -> Conversation | None:
        """The conversation if it is the caller's, `None` otherwise.

        `None` covers both "no such conversation" and "another user's", which is
        the point: the caller maps absence to `404` and never to `403` and never to
        an empty `200` (docs/04-api.md §Convenciones HTTP).
        """
        ...

    async def list_for_user(self, *, user_id: UUID) -> list[Conversation]:
        """The caller's conversations, oldest first."""
        ...


@runtime_checkable
class MessageRepository(Protocol):
    """The turns of a conversation, scoped by the conversation's owner (D-T12.5).

    Same rule as `ConversationRepository`: every method takes the `user_id`, and a
    message of another user is absent rather than refused.

    `helpful` belongs to an assistant answer only (D-T12.1): the database carries
    `CHECK (helpful IS NULL OR role = 'assistant')`
    (docs/03 §`kb_document`, `kb_chunk`, `conversation` y `message`, row `Costo y
    tiempo`) and `Message` refuses the same shape in the domain, so the constraint
    is never reached.
    """

    async def add(self, *, user_id: UUID, conversation_id: UUID, message: Message) -> Message:
        """Store one turn in a conversation the caller owns."""
        ...

    async def list_for_user(self, *, user_id: UUID, conversation_id: UUID) -> list[Message]:
        """The turns of that conversation, oldest first."""
        ...

    async def set_helpful(self, *, user_id: UUID, message_id: UUID, helpful: bool) -> bool:
        """Mark an assistant answer as helpful or not.

        `False` when no such message is visible to that user, so
        `POST /assistant/messages/{message_id}:feedback` answers `404` for a
        message of another user (docs/04-api.md §Riesgo, métricas y asistente).
        """
        ...


@runtime_checkable
class UsageQuotaPort(Protocol):
    """What the daily limit and the monthly budget are decided on (docs/04-api.md
    §Límites de uso; D-T12.1, D-T12.3).

    Per user. The port returns evidence and does **no arithmetic of its own**: the
    cap comparison, the remaining count and the month sum are the pure rules of
    `assistant.domain.limits`, so the same figures are decided the same way in the
    endpoint and in a test. What the port owns is the one thing a pure rule cannot:
    `admit_question` decides whether this question may be asked at all, in a single
    atomic step.

    The monthly read deliberately returns the raw `cost_cop` values with their
    nulls instead of a `SUM`, because `month_spend_cop` is the rule that ignores a
    missing cost (D-T12.1) and a `SUM` computed in SQL would hide that. At ADR-0007:9's
    volume — 1.800 questions a day, ~54.000 rows a month — that is the one place a
    review should weigh an aggregate query against the rule; T6 owns the budget and
    can replace this read with a SQL `SUM` without touching the rules.
    """

    async def questions_asked_on(self, *, user_id: UUID, day: date) -> int:
        """How many questions that user asked on that day.

        `day` is a date already resolved in **America/Bogota**
        (docs/06-diseno-detallado.md §9: "el límite diario cuenta por `created_at`
        en la zona horaria del usuario"), and a question counts whether it was
        answered by the model or degraded without it — it was asked either way.
        """
        ...

    async def month_costs_cop(self, *, user_id: UUID, month: date) -> Sequence[Decimal | None]:
        """Every `message.cost_cop` of that month, `None` included, oldest first.

        `month` is the first day of the calendar month in America/Bogota. The
        nulls are the degraded answers' (D-T12.1) and are returned, not dropped:
        the rule that ignores them belongs to the domain.
        """
        ...

    async def admit_question(
        self, *, user_id: UUID, day: date, month: date, budget_cap_cop: Decimal
    ) -> bool:
        """Reserve this user's question for `day`, or refuse it. One atomic step.

        The ten-questions-per-day rule and the monthly cap are **hard** limits
        (docs/04-api.md §Límites de uso; D-T12.3), so the count, the month's spend
        and the reservation are decided together or not at all. Reading the count
        and deciding afterwards lets two concurrent callers that both saw nine
        questions walk away with the tenth and the eleventh slot (R3-QUOTA-RACE).

        An adapter implements this as one conditional write — a reservation whose
        `WHERE` carries the day's count and the month's spend — so the reservation
        and both limits are decided against the same snapshot; the reservation is
        what makes the check and the write the same transaction. `False` means the
        caller answers the degraded answer and no provider is called.

        `day` and `month` are already resolved in America/Bogota, and
        `budget_cap_cop` is passed in because it is the one figure that differs per
        profile (D-T12.3). Whether the refusal was the day or the budget is not
        this method's answer to give: `evaluate_quota` decides it from the figures
        it reads.
        """
        ...
