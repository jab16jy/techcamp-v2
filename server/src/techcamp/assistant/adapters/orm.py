"""SQLAlchemy table mappings for the assistant (docs/03-modelo-datos.md
§`kb_document`, `kb_chunk`, `conversation` y `message`: el asistente (E12);
ADR-0026; D-T12.1, D-T12.2, D-T12.5).

Mapping only: the repositories, the queries and the ingestion write port are
T3's and T6's. What is here is the shape of the four tables, with the **same
constraint and index names as the migration**, because a name that exists on one
side only is drift — an error message quotes it and a test asserts it.

**No table here carries `org_id`** (D-T12.5). The corpus is shared institutional
reference — the Boletín Agroclimático Nacional, the mesas técnicas bulletins
of the Caribe and the licence-reviewed guides (ADR-0008:32) — the same row for
every organization, exactly like `municipality`, `model_version` and
`risk_prediction` (docs/03-modelo-datos.md §`kb_document`, `kb_chunk`,
`conversation` y `message`, rows `org_id`; the precedent is the row `org_id` of
§`municipality`, `model_version` y `risk_prediction`). Its isolation is applied
where the data leaves the server, when the answer cites the source.
`conversation` carries no `org_id` either: it belongs to a user and the plot it
speaks about is already behind `resolve_plot_access`, so duplicating the
organization here would be a second source of truth that can disagree with the
plot.

The vocabularies (`kind`, `enso_state`) and D-T12.1's `helpful` CHECK are the
decisions docs/03 states, not defensive extras. `role` deliberately has **no**
CHECK: docs/03:368-376 defines no vocabulary for it, and inventing one here would
put a rule in the database that no document owns.

`Vector(EMBEDDING_DIMENSION)` — not a literal `384` — because the width has one
owner, `assistant.application.ports.EMBEDDING_DIMENSION`, which ADR-0026 pins to
`intfloat/multilingual-e5-small`. Changing the embedding model rewrites the
column, and that is a migration of its own (D-T12.2).
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from techcamp.assistant.application.ports import EMBEDDING_DIMENSION
from techcamp.shared.db import Base


class KbDocumentRow(Base):
    """One document of the corpus (docs/03-modelo-datos.md:348-355).

    `(source_url, sha256)` is the ingestion idempotency key (docs/03 row
    `Unicidad de la ingesta`): ingestion is a repeatable job (ADR-0012), so
    re-loading a document must not duplicate it nor leave its chunks orphaned.
    `sha256` is the digest of the fetched bytes, which is what tells a re-run of
    an unchanged file from a genuinely revised one.

    `department = null` **is** the national bulletin (docs/03:353), the value
    docs/06 §9's seasonal-climate fact falls back to, and `enso_state` is filled
    for bulletins only — hence nullable rather than an enum over two of its
    three values.
    """

    __tablename__ = "kb_document"
    __table_args__ = (
        CheckConstraint("kind in ('guide', 'agroclimatic_bulletin')", name="ck_kb_document_kind"),
        CheckConstraint(
            "enso_state in ('neutral', 'el_nino', 'la_nina')", name="ck_kb_document_enso_state"
        ),
        UniqueConstraint("source_url", "sha256", name="uq_kb_document_sha256"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    license: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    published_on: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    department: Mapped[str | None] = mapped_column(Text, nullable=True)
    enso_state: Mapped[str | None] = mapped_column(Text, nullable=True)


class KbChunkRow(Base):
    """One fragment of one document, with its embedding (docs/03:356-362).

    `embedding` is full precision: D-T12.2 rejects `halfvec` for a corpus of
    ~50 MB over ~10 000 fragments (docs/02:90), and the HNSW index below is the
    cosine search the retrieval reads (docs/03 §Índices principales).
    `Mapped[Any]` for the same reason farms' geometries are: pgvector adapts a
    list of floats in and a list of floats out, and the vector itself never
    reaches the domain.
    """

    __tablename__ = "kb_chunk"
    __table_args__ = (
        Index(
            "ix_kb_chunk_embedding_cosine",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("kb_document.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIMENSION), nullable=False)


class ConversationRow(Base):
    """One user's transcript, about one plot or about none (docs/03:363-367;
    docs/04-api.md:227; D-T12.1, D-T12.5).

    `plot_id` is nullable because the endpoint's signature makes it so:
    `POST /assistant/messages { conversation_id?, plot_id?, content }` makes the
    first message's plot optional, so the conversation it creates is about no
    plot — the `plot_id: UUID | None` of `ConversationRepository.create`. docs/03
    annotates no nullability here, and its silence is not a `NOT NULL` assertion:
    the same diagram writes `department "null = nacional"` (docs/03:351) where a
    null is meant.

    `user_id` is `NOT NULL`: every conversation belongs to a user, and the daily
    limit reaches the user through it (D-T12.1/D-T12.5). The plot, when there is
    one, is what `resolve_plot_access` gates.
    """

    __tablename__ = "conversation"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id"), nullable=False)
    plot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("plot.id"), nullable=True)


class MessageRow(Base):
    """One turn of a conversation (docs/03:368-376; D-T12.1).

    The daily limit counts `created_at` in America/Bogota (docs/04-api.md:22)
    and the monthly budget is `SUM(cost_cop)` over it (docs/11:132), so
    `created_at` is `NOT NULL` with a server default — no writer passes the
    clock. `model`, `input_tokens`, `output_tokens` and `cost_cop` are nullable
    because **missing evidence is `null`, never `0`**: the answer without an LLM
    call writes `model IS NULL`, which is what distinguishes "the LLM was never
    called" from "it was called and cost nothing", and `SUM` ignores nulls,
    which is the budget's behaviour.

    `role` carries no CHECK (docs/03:368-376 documents no vocabulary for it) and
    `helpful` does, because that one is a stated decision (D-T12.1): only an
    assistant answer can be rated, so the feedback endpoint cannot record a
    rating of the user's own question.
    """

    __tablename__ = "message"
    __table_args__ = (
        Index("ix_message_conversation_created_at", "conversation_id", "created_at"),
        Index("ix_message_created_at", "created_at"),
        CheckConstraint(
            "helpful is null or role = 'assistant'", name="ck_message_helpful_only_assistant"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversation.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    helpful: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_cop: Mapped[decimal.Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
