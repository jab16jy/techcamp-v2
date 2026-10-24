"""add the assistant tables

`kb_document` and `kb_chunk` hold the shared institutional corpus and
`conversation` / `message` the transcript (docs/03-modelo-datos.md §`kb_document`,
`kb_chunk`, `conversation` y `message`: el asistente (E12)).

Three decisions of the task are written into this schema:

* **No `org_id`** on `kb_document` / `kb_chunk` (D-T12.5): the corpus is shared
  reference — the same row for every organization, like `model_version` and
  `risk_prediction` — and its isolation is applied where the data leaves the
  server, when the answer cites the source.
* **`created_at`, `model` and `cost_cop`** on `message` (D-T12.1): the daily
  limit counts `created_at` in America/Bogota (docs/04-api.md:22) and the
  monthly budget is `SUM(cost_cop)` over it (docs/11:132). Missing evidence is
  `null`, never `0`, so `model IS NULL` is what tells "the LLM was never
  called" from "it was called and cost nothing".
* **`vector(384)` full precision** with a cosine HNSW index (D-T12.2): the
  width is `intfloat/multilingual-e5-small`'s own, pinned by ADR-0026, and the
  literal is written here on purpose. A migration is a historical record: if T8
  changes the embedding model it rewrites the column in a new revision of its
  own, and this one must keep describing `vector(384)` forever.

Revision ID: a7d3f1c95b28
Revises: b6e1c4a7f2d9
Create Date: 2026-10-07 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = "a7d3f1c95b28"
down_revision: Union[str, Sequence[str], None] = "b6e1c4a7f2d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_EMBEDDING_DIMENSION = 384
"""ADR-0026: `intfloat/multilingual-e5-small`, MIT, 384 dimensions, local. The
width is a property of the model, not a preference of the caller, and
`assistant.application.ports.EMBEDDING_DIMENSION` owns it for the code."""


def upgrade() -> None:
    """Upgrade schema."""
    # ADR-0003: the schema must not depend on the compose init script (CI's
    # Postgres service has none, same lesson as postgis in 9098dc0927a3 and
    # timescaledb in 8c3983dc2dfd). The seminar image already installs `vector`
    # (infra/postgres/init-extensions.sql:6); `IF NOT EXISTS` makes the second
    # install a no-op. Not dropped on downgrade: other schemas share it.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "kb_document",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("license", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        # The digest of the fetched bytes: what tells a re-run of an unchanged
        # file from a genuinely revised one, and half of the ingestion
        # idempotency key below.
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("published_on", sa.Date(), nullable=False),
        # `department` `null = nacional`: the seasonal-climate fact reads the
        # department's bulletin and falls back to the national one (docs/06 §9),
        # so null is a value, not missing evidence.
        sa.Column("department", sa.Text(), nullable=True),
        # `enso_state` is filled for bulletins only, hence nullable rather than
        # an enum over two of its three values (docs/03:354).
        sa.Column("enso_state", sa.Text(), nullable=True),
        # Ingestion is a repeatable job (ADR-0012): re-loading a document must
        # not duplicate it nor leave its chunks orphaned
        # (docs/03 §Unicidad de la ingesta).
        sa.UniqueConstraint("source_url", "sha256", name="uq_kb_document_sha256"),
        sa.CheckConstraint("kind in ('guide', 'agroclimatic_bulletin')", name="ck_kb_document_kind"),
        sa.CheckConstraint(
            "enso_state in ('neutral', 'el_nino', 'la_nina')", name="ck_kb_document_enso_state"
        ),
    )
    op.create_table(
        "kb_chunk",
        sa.Column("id", sa.Uuid(), primary_key=True),
        # A fragment of no document can never be cited — a citation carries
        # the source and its licence (ADR-0008:32) — so this is NOT NULL.
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("kb_document.id"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        # Full precision, not `halfvec`: D-T12.2 rejects half precision for a
        # corpus of ~50 MB over ~10 000 fragments (docs/02:90).
        sa.Column("embedding", Vector(_EMBEDDING_DIMENSION), nullable=False),
    )
    # The semantic search of the corpus (docs/03 §Índices principales;
    # ADR-0008:12). One build is fine at ~10 000 chunks.
    op.create_index(
        "ix_kb_chunk_embedding_cosine",
        "kb_chunk",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_table(
        "conversation",
        sa.Column("id", sa.Uuid(), primary_key=True),
        # `user_id` is NOT NULL: every conversation belongs to a user and the
        # daily limit reaches the user through it (D-T12.1/D-T12.5).
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("app_user.id"), nullable=False),
        # `plot_id` is null when the conversation is about no plot:
        # `POST /assistant/messages { conversation_id?, plot_id?, content }`
        # (docs/04-api.md:227) makes the first message's plot optional, so the
        # conversation it creates may name none. docs/03:363-367 annotates no
        # nullability, and its silence is not a NOT NULL assertion — the same
        # diagram writes `department "null = nacional"` (docs/03:351) where a
        # null is meant.
        sa.Column("plot_id", sa.Uuid(), sa.ForeignKey("plot.id"), nullable=True),
    )
    op.create_table(
        "message",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversation.id"), nullable=False),
        # No CHECK on `role`: docs/03:368-376 documents no vocabulary for it,
        # and a rule no document owns does not belong in the database.
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("helpful", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # `model IS NULL` is what distinguishes "the LLM was never called" from
        # "it was called and cost nothing" (D-T12.1).
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("cost_cop", sa.Numeric(12, 6), nullable=True),
        # Only an assistant answer can be rated: the feedback endpoint cannot
        # record a rating of the user's own question (D-T12.1).
        sa.CheckConstraint(
            "helpful is null or role = 'assistant'", name="ck_message_helpful_only_assistant"
        ),
    )
    # The two queries of the limit and the budget (D-T12.1; docs/03 §Índices
    # principales): the day's count of a user, and the month's spend against the
    # cap (docs/11:132).
    op.create_index(
        "ix_message_conversation_created_at",
        "message",
        ["conversation_id", "created_at"],
    )
    op.create_index("ix_message_created_at", "message", ["created_at"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_message_created_at", table_name="message")
    op.drop_index("ix_message_conversation_created_at", table_name="message")
    op.drop_table("message")
    op.drop_table("conversation")
    op.drop_index("ix_kb_chunk_embedding_cosine", table_name="kb_chunk")
    op.drop_table("kb_chunk")
    op.drop_table("kb_document")
    # `vector` is left installed, like `postgis` and `timescaledb` (ADR-0003).
