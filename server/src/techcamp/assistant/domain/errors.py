"""Domain-level assistant failures. Pure, no I/O (mirrors `farms.domain.errors`).

Each error is the domain saying "this shape is not one the assistant may build",
so the caller never has to invent a value for a field the docs leave empty.

The `...NotFound` pair is the documented shape of isolation for this module:
`conversation` and `message` carry no `org_id` (docs/03-modelo-datos.md §
`kb_document`, `kb_chunk`, `conversation` y `message`, row `org_id` en
`conversation`, D-T12.5) and their repositories filter by `user_id`, so another
user's conversation is simply **absent**. Callers map absence to `404`, never to
`403` and never to an empty `200` (docs/04-api.md §Convenciones HTTP,
docs/09-cuellos-de-botella.md §Seguridad).
"""

from __future__ import annotations

from uuid import UUID


class PromptSectionNotAllowedError(Exception):
    """Raised when an outbound prompt is assembled with a block outside the
    send-list (D-T12.6).

    The prompt carries the system prompt, the plot facts, the retrieved fragments
    and the question, and nothing else (ADR-0007:39 — minimum data to the
    provider, no personal data). The section vocabulary is the allow-list, so a
    block this module does not know about is rejected instead of being sent.
    """

    def __init__(self, section: object) -> None:
        self.section = section
        super().__init__(f"Prompt section {section!r} is not part of the send-list")


class CitationMissingModelVersionError(Exception):
    """Raised when a citation about a prediction does not name the version that
    produced it (docs/06-diseno-detallado.md §9, docs/04-api.md §Riesgo,
    métricas y asistente).

    A prediction without its `model_version` is a number nobody can trace back to
    the model that produced it, so the citation is not one the answer may carry.
    """

    def __init__(self, source: str) -> None:
        self.source = source
        super().__init__(f"Citation to {source!r} does not name the model version that produced it")


class CitationMissingBaselineMarkError(Exception):
    """Raised when a citation about a prediction does not say whether the served
    version is a baseline (docs/06 §9: "Si la predicción viene de la heurística de
    línea base, se indica").

    `is_baseline = None` is missing evidence, and reading it as `False` would
    present a baseline's `low` as a calibrated verdict (D-T9.2: the served
    `climatology_month` baseline carries `thresholds={}`).
    """

    def __init__(self, source: str) -> None:
        self.source = source
        super().__init__(f"Citation to {source!r} does not mark whether its version is a baseline")


class InvalidMessageEvidenceError(Exception):
    """Raised when the evidence of one answer (`message.model`, the two token
    counts and `message.cost_cop`) does not describe one and the same thing
    (D-T12.1, docs/06 §9).

    The four fields move together: either the LLM was never called and all four
    are `None`, or it was called and all four are reported. A shape that mixes
    them is a `0` or a `None` standing in for missing evidence, which is exactly
    what the design forbids.
    """

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class HelpfulFlagOnUserMessageError(Exception):
    """Raised when a question of the user is marked helpful (D-T12.1: only an
    assistant answer can be marked useful).

    The database mirrors this with
    `CHECK (helpful IS NULL OR role = 'assistant')` (docs/03-modelo-datos.md §
    `kb_document`, `kb_chunk`, `conversation` y `message`, row `Costo y tiempo`);
    this is the same rule one layer earlier, so the column never has to reject it.
    """

    def __init__(self, message_id: UUID) -> None:
        self.message_id = message_id
        super().__init__(
            f"Only an assistant answer can be marked helpful, message {message_id} is not"
        )


class ConversationNotFoundError(Exception):
    """Raised when a conversation doesn't exist or belongs to another user.

    docs/04-api.md §Convenciones HTTP: a resource of another organization answers
    `404`, never `403`, so the caller cannot tell the two apart — and here the two
    are the same row, because the repository filters by `user_id` (D-T12.5).
    """

    def __init__(self, conversation_id: UUID) -> None:
        self.conversation_id = conversation_id
        super().__init__(f"Conversation {conversation_id} not found")


class MessageNotFoundError(Exception):
    """Raised when a message doesn't exist or belongs to another user
    (`POST /assistant/messages/{message_id}:feedback` answers `404` for a
    message of another user, docs/04-api.md §Riesgo, métricas y asistente)."""

    def __init__(self, message_id: UUID) -> None:
        self.message_id = message_id
        super().__init__(f"Message {message_id} not found")
