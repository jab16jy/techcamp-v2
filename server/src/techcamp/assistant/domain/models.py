"""One turn of an assistant conversation and the evidence behind it (E12 T1;
docs/03-modelo-datos.md §`kb_document`, `kb_chunk`, `conversation` y `message`;
docs/06-diseno-detallado.md §9; D-T12.1).

Pure data, no I/O and no clock read: `created_at` arrives as an argument because
the daily limit counts by it and a rule that read the clock itself could not be
decided by the values under test.

`MessageEvidence` is the D-T12.1 rule made into a type. The four columns of one
answer (`model`, the two token counts and `cost_cop`) are individually optional,
and four independent optionals let a row say "the LLM answered, with no tokens and
no cost" — a claim that is false, and one that a `SUM` would then pay zero for
instead of ignoring. So the fields move together: all `None` or all reported, and
`model is None` is what distinguishes the degraded answer from a call that came
free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from techcamp.assistant.domain.errors import (
    HelpfulFlagOnUserMessageError,
    InvalidMessageEvidenceError,
    SystemRoleOnStoredMessageError,
)


class MessageRole(StrEnum):
    """Who wrote a turn (docs/03-modelo-datos.md:368-376, `message.role`).

    Two roles are **stored**, because the endpoint writes both halves of the
    exchange and only an assistant answer may be marked helpful (D-T12.1).

    `SYSTEM` exists for the **outbound** payload and is refused by `Message`
    (T5, D-T12.6): docs/06-diseno-detallado.md §9 sends "sistema + hechos de la
    parcela + fragmentos + pregunta (stream)", and the fragments and the
    producer's own text must travel as *data* (docs/09-cuellos-de-botella.md
    §Seguridad), which a shared `user` role cannot express. So the rules need a
    role of their own on the wire — and none at all in the database: the system
    prompt is a `PromptSection` assembled per request and never stored, which is
    what `SystemRoleOnStoredMessageError` enforces.
    """

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class Conversation:
    """A transcript of one user's questions and answers (docs/03-modelo-datos.md:363-366).

    Three columns, no `org_id`: the conversation belongs to a **user**, and the
    plot it speaks about is already behind `resolve_plot_access` (D-T12.5, row
    `org_id` en `conversation`, the precedent `municipality` / `model_version` /
    `risk_prediction` set at docs/03 §`municipality`, `model_version` y
    `risk_prediction`). Adding `org_id` would be a second source of truth that can
    disagree with the plot.

    `plot_id` is optional because the endpoint's signature makes it so —
    `POST /assistant/messages { conversation_id?, plot_id?, content }`
    (docs/04-api.md §Riesgo, métricas y asistente): a general question belongs to
    a conversation that names no plot.
    """

    id: UUID
    user_id: UUID
    plot_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One message of the outbound conversation: a role and its text.

    The whole payload of a turn is `sequence[ChatMessage]`, which is why this type
    has no id, no timestamp and no cost: none of those exist on the provider's
    side, and inventing them here would put data in the request that the send-list
    (D-T12.6) does not allow.
    """

    role: MessageRole
    content: str


@dataclass(frozen=True, slots=True)
class MessageEvidence:
    """Which model produced an answer, with the tokens and the pesos it cost
    (D-T12.1, docs/06 §9 "Qué se guarda de cada turno").

    All four fields are `None` for the degraded no-LLM answer, and all four are
    reported for a call — never `0` in place of a `None`, and never a `None` for
    a model that did answer. The database mirrors the two invariants of the shape
    in `message.model` / `message.cost_cop` being nullable and in the
    `SUM(cost_cop)` that ignores NULL (docs/03 §`kb_document`, `kb_chunk`,
    `conversation` y `message`, rows `Costo y tiempo` and `Ausencia de evidencia`);
    this is where the invariant is decided.
    """

    model: str | None = None
    """Null **means the LLM was never called**, which is the one fact a null here
    carries (D-T12.1)."""
    prompt_tokens: int | None = None
    """`message.input_tokens`."""
    completion_tokens: int | None = None
    """`message.output_tokens`."""
    cost_cop: Decimal | None = None
    """`message.cost_cop`, the origin of the budget metric
    (docs/11-metricas.md §gasto del mes / tope)."""

    @classmethod
    def no_llm(cls) -> MessageEvidence:
        """The evidence of a degraded answer: the LLM was not called, so nothing
        is reported and nothing is spent (docs/06 §9: "guardar el mensaje con
        model, tokens y costo nulos")."""
        return cls()

    @classmethod
    def from_call(
        cls,
        *,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_cop: Decimal,
    ) -> MessageEvidence:
        """The evidence of an answer a provider produced.

        `cost_cop = 0` is a real call that happened to come free and is accepted:
        only a `None` model means "no se llamó al LLM".
        """
        return cls(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_cop=cost_cop,
        )

    def __post_init__(self) -> None:
        reported = (self.model, self.prompt_tokens, self.completion_tokens, self.cost_cop)
        if all(value is None for value in reported):
            return

        model = self.model
        prompt_tokens = self.prompt_tokens
        completion_tokens = self.completion_tokens
        cost_cop = self.cost_cop
        if not model or prompt_tokens is None or completion_tokens is None or cost_cop is None:
            raise InvalidMessageEvidenceError(
                "message evidence is either all null (the LLM was never called) or all "
                f"reported, got model={model!r}, prompt_tokens={prompt_tokens!r}, "
                f"completion_tokens={completion_tokens!r}, cost_cop={cost_cop!r}"
            )
        if prompt_tokens < 0 or completion_tokens < 0:
            raise InvalidMessageEvidenceError(
                f"token counts cannot be negative, got {prompt_tokens}/{completion_tokens}"
            )
        if cost_cop < 0:
            raise InvalidMessageEvidenceError(f"cost_cop cannot be negative, got {cost_cop}")

    @property
    def called(self) -> bool:
        """Whether a provider produced this answer at all: `model is None` is the
        whole test (D-T12.1)."""
        return self.model is not None

    @property
    def costs_nothing(self) -> bool:
        """A call that happened and cost zero pesos — which is not the same thing
        as an answer the LLM never produced, and is reported as such."""
        return self.model is not None and self.cost_cop == 0


@dataclass(frozen=True, slots=True)
class Message:
    """One stored turn of a conversation (docs/03-modelo-datos.md:368-376).

    A row of `message`, with the four D-T12.1 evidence columns folded into
    `evidence` so the invariant travels with the value instead of living in the
    four places that write it. The `helpful` rule is checked here as well: the
    database has `CHECK (helpful IS NULL OR role = 'assistant')`, and a value that
    violates it never reaches the column.
    """

    id: UUID
    conversation_id: UUID
    role: MessageRole
    content: str
    created_at: datetime
    evidence: MessageEvidence = field(default_factory=MessageEvidence.no_llm)
    helpful: bool | None = None

    def __post_init__(self) -> None:
        if self.role is MessageRole.SYSTEM:
            raise SystemRoleOnStoredMessageError(self.id)
        if self.helpful is not None and self.role is not MessageRole.ASSISTANT:
            raise HelpfulFlagOnUserMessageError(self.id)
