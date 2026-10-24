"""Postgres repositories of the assistant (E12 T5; ADR-0008; docs/03-modelo-datos.md
§`kb_document`, `kb_chunk`, `conversation` y `message`: el asistente (E12);
D-T12.1, D-T12.5; docs/09-cuellos-de-botella.md §Seguridad).

Three adapters over this module's own tables, and two rules that are different
for each of them:

* **The corpus filters by nothing.** `kb_document` and `kb_chunk` carry no
  `org_id` because the corpus is shared institutional reference — the Boletín
  Agroclimático Nacional and the mesas técnicas bulletins of the Caribe
  (D-T12.5) — the same row for every organization, exactly like `municipality`,
  `model_version` and `risk_prediction`
  (docs/03 §`municipality`, `model_version` y `risk_prediction`, fila `org_id`).
  Its isolation is applied where the data leaves the server, when the answer
  cites the source.
* **The transcript filters by its owner.** `conversation` also carries no
  `org_id` (D-T12.5) and belongs to a **user**, so every read and every write
  below takes the `user_id`: another user's turn is *absent*, which the endpoint
  turns into `404` and never into `403` (docs/04-api.md §Convenciones HTTP,
  docs/09 §Seguridad).

Two things this module decides, because the schema alone cannot:

* **`add` refuses a conversation the caller does not own.** The foreign key from
  `message.conversation_id` to `conversation.id` says nothing about *whose*
  conversation it is, so without this check a turn could be written into another
  user's transcript — a leak the database cannot prevent by itself.
* **`created_at` is written by the database** (`server_default=now()`) and read
  back, never passed in. The daily limit counts by it and the monthly budget sums
  over it (docs/06 §9, docs/11-metricas.md:132), so a writer that stamped its own
  clock would make both readings a function of the writer.

The vector search is the one place a query has to speak pgvector: `ORDER BY
embedding <=> :query` ascending is the form the `vector_cosine_ops` HNSW index
of docs/03 §Índices principales accelerates, and the similarity the caller
compares against `SIMILARITY_FLOOR` is `1 - distance`, which is the cosine the
index is built on.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.assistant.adapters.orm import (
    ConversationRow,
    KbChunkRow,
    KbDocumentRow,
    MessageRow,
)
from techcamp.assistant.application.ports import KnowledgeDocument, RetrievedFragment
from techcamp.assistant.domain.errors import ConversationNotFoundError
from techcamp.assistant.domain.models import Conversation, Message, MessageEvidence, MessageRole
from techcamp.shared.ids import uuid7

_DOCUMENT_COLUMNS = (
    KbDocumentRow.id,
    KbDocumentRow.title,
    KbDocumentRow.kind,
    KbDocumentRow.source_url,
    KbDocumentRow.license,
    KbDocumentRow.published_on,
    KbDocumentRow.department,
    KbDocumentRow.enso_state,
)


class SqlAlchemyKnowledgeRepository:
    """The shared institutional corpus (ADR-0008; docs/06-diseno-detallado.md §9).

    **No `org_id` anywhere in this class**, by the decision D-T12.5 records and
    docs/03's per-table rows state. A read here is scoped to the corpus, never to
    an organization: the question is which documents answer this, and the
    answer is the same for every producer.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search_by_embedding(
        self, embedding: Sequence[float], *, top_k: int, similarity_floor: float
    ) -> list[RetrievedFragment]:
        """The closest chunks to `embedding`, best first, at or above the floor.

        `ORDER BY embedding <=> :query` ascending is the operator order the
        cosine HNSW index accelerates (docs/03 §Índices principales), and the
        score reported back is `1 - distance`: **the cosine the index is built
        on**, so the number a caller compares against `SIMILARITY_FLOOR` is the
        same one the ranking used.

        The floor is applied here *and* re-applied by `application.retrieval`:
        the use case does not trust this method to have honoured the port's
        contract, and neither of them trusts the other.
        """
        distance = KbChunkRow.embedding.cosine_distance(list(embedding))
        statement = (
            select(
                KbChunkRow.id,
                KbChunkRow.document_id,
                KbChunkRow.content,
                distance.label("distance"),
            )
            .order_by(distance)
            .limit(top_k)
        )
        rows = (await self._session.execute(statement)).all()
        return [
            RetrievedFragment(
                id=row.id,
                document_id=row.document_id,
                content=row.content,
                similarity=1.0 - float(row.distance),
            )
            for row in rows
            if 1.0 - float(row.distance) >= similarity_floor
        ]

    async def get_document(self, document_id: uuid.UUID) -> KnowledgeDocument | None:
        """The document behind a retrieved fragment, or `None`.

        `license` travels too: ADR-0008:12 records source **and** licence per
        document, and a corpus whose licence was never checked must not look
        like one whose licence is known.
        """
        statement = select(*_DOCUMENT_COLUMNS).where(KbDocumentRow.id == document_id)
        row = (await self._session.execute(statement)).one_or_none()
        return _document_from_row(row) if row is not None else None

    async def latest_climate_bulletin(self, *, department: str | None) -> KnowledgeDocument | None:
        """The latest `agroclimatic_bulletin` of a department, else the national one.

        docs/06-diseno-detallado.md §9: "el último boletín agroclimático del
        departamento de la finca, o del nacional si no hay uno regional, con su
        fecha de publicación". `department IS NULL` **is** the national bulletin
        (docs/03:351), not a missing department, so the fallback is the same
        query with a null department rather than a title that looks national.
        """
        regional = await self._latest_bulletin(department=department)
        if regional is not None:
            return regional
        return await self._latest_bulletin(department=None)

    async def _latest_bulletin(self, *, department: str | None) -> KnowledgeDocument | None:
        statement = (
            select(*_DOCUMENT_COLUMNS)
            .where(
                KbDocumentRow.kind == "agroclimatic_bulletin",
                KbDocumentRow.department.is_(None)
                if department is None
                else KbDocumentRow.department == department,
            )
            .order_by(KbDocumentRow.published_on.desc(), KbDocumentRow.id.desc())
            .limit(1)
        )
        row = (await self._session.execute(statement)).one_or_none()
        return _document_from_row(row) if row is not None else None


class SqlAlchemyConversationRepository:
    """The transcripts of one user (D-T12.5; docs/03 §`conversation`).

    No `org_id`: the conversation belongs to a user and the plot it speaks about
    is behind `resolve_plot_access`, so a second source of truth here could only
    disagree with the plot.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, user_id: uuid.UUID, plot_id: uuid.UUID | None) -> Conversation:
        row = ConversationRow(id=uuid7(), user_id=user_id, plot_id=plot_id)
        self._session.add(row)
        await self._session.commit()
        return _conversation_from_row(row)

    async def get_for_user(
        self, *, user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> Conversation | None:
        """The conversation if it is the caller's; `None` covers both "no such
        conversation" and "another user's", which is the point (docs/04-api.md
        §Convenciones HTTP)."""
        statement = select(ConversationRow).where(
            ConversationRow.id == conversation_id, ConversationRow.user_id == user_id
        )
        row = (await self._session.execute(statement)).scalars().one_or_none()
        return _conversation_from_row(row) if row is not None else None

    async def list_for_user(self, *, user_id: uuid.UUID) -> list[Conversation]:
        """The caller's conversations, oldest first.

        Ordered by `id` because the ids are `uuid7()` (`shared.ids`), which is
        time-ordered by construction: a `created_at` column the conversation does
        not have (docs/03:363-366) is not invented to sort by, and an unordered
        list would make "oldest first" a claim nothing holds.
        """
        statement = (
            select(ConversationRow)
            .where(ConversationRow.user_id == user_id)
            .order_by(ConversationRow.id)
        )
        rows = (await self._session.execute(statement)).scalars().all()
        return [_conversation_from_row(row) for row in rows]


class SqlAlchemyMessageRepository:
    """The turns of one conversation, scoped by the conversation's owner (D-T12.5).

    Every method carries the `user_id` even though `message` has no such column:
    ownership is reached through the conversation, and a filter that only took a
    `conversation_id` would let any caller read any transcript that id reached.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self, *, user_id: uuid.UUID, conversation_id: uuid.UUID, message: Message
    ) -> Message:
        """Store one turn in a conversation the caller owns.

        Raises `ConversationNotFoundError` when that conversation is not the
        caller's: the foreign key would accept the row, and a turn written into
        another user's transcript is a leak the schema cannot prevent on its own.
        """
        # The row below writes `message.conversation_id`, so it must be this one.
        if message.conversation_id != conversation_id:
            raise ConversationNotFoundError(conversation_id)
        owned = await self._session.execute(
            select(ConversationRow.id).where(
                ConversationRow.id == conversation_id, ConversationRow.user_id == user_id
            )
        )
        if owned.scalar_one_or_none() is None:
            raise ConversationNotFoundError(conversation_id)

        row = MessageRow(
            id=message.id,
            conversation_id=message.conversation_id,
            role=message.role.value,
            content=message.content,
            input_tokens=message.evidence.prompt_tokens,
            output_tokens=message.evidence.completion_tokens,
            model=message.evidence.model,
            cost_cop=message.evidence.cost_cop,
            helpful=message.helpful,
        )
        self._session.add(row)
        await self._session.commit()
        # `created_at` is the database's (`server_default=now()`), so the
        # returned turn carries the timestamp the daily limit will count, not
        # the one this process would have guessed.
        await self._session.refresh(row)
        return _message_from_row(row)

    async def list_for_user(
        self, *, user_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> list[Message]:
        """The turns of that conversation, oldest first, or none at all."""
        statement = (
            select(MessageRow)
            .join(ConversationRow, ConversationRow.id == MessageRow.conversation_id)
            .where(
                ConversationRow.user_id == user_id,
                MessageRow.conversation_id == conversation_id,
            )
            .order_by(MessageRow.created_at, MessageRow.id)
        )
        rows = (await self._session.execute(statement)).scalars().all()
        return [_message_from_row(row) for row in rows]

    async def set_helpful(
        self, *, user_id: uuid.UUID, message_id: uuid.UUID, helpful: bool
    ) -> bool:
        """Mark an assistant answer as helpful or not.

        `False` when no such message is visible to that user, so the feedback
        endpoint answers `404` for a message of another user. The write is a
        single conditional `UPDATE` through the owning conversation rather than a
        read followed by a write: the read-then-write version has a window in
        which the message a test just saw is one the caller cannot write.
        """
        statement = (
            update(MessageRow)
            .where(
                MessageRow.id == message_id,
                MessageRow.conversation_id.in_(
                    select(ConversationRow.id).where(ConversationRow.user_id == user_id)
                ),
            )
            .values(helpful=helpful)
            .returning(MessageRow.id)
        )
        updated = (await self._session.execute(statement)).one_or_none()
        await self._session.commit()
        return updated is not None


def _document_from_row(row: Any) -> KnowledgeDocument:
    return KnowledgeDocument(
        id=row.id,
        title=row.title,
        kind=row.kind,
        source_url=row.source_url,
        license=row.license,
        published_on=row.published_on if isinstance(row.published_on, date) else None,
        department=row.department,
        enso_state=row.enso_state,
    )


def _conversation_from_row(row: Any) -> Conversation:
    return Conversation(id=row.id, user_id=row.user_id, plot_id=row.plot_id)


def _message_from_row(row: Any) -> Message:
    return Message(
        id=row.id,
        conversation_id=row.conversation_id,
        role=MessageRole(row.role),
        content=row.content,
        created_at=row.created_at,
        evidence=MessageEvidence(
            model=row.model,
            prompt_tokens=row.input_tokens,
            completion_tokens=row.output_tokens,
            cost_cop=row.cost_cop,
        ),
        helpful=row.helpful,
    )
