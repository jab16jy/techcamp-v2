"""The prompt policy as data (E12 T1; docs/06-diseno-detallado.md §9 "Reglas del
prompt"; D-T12.6; docs/09-cuellos-de-botella.md §Seguridad).

Two pieces, both pure:

* **The send-list.** `PromptSection` is the vocabulary of blocks an outbound
  prompt may carry — the system prompt, the plot facts, the retrieved fragments
  and the question. It is an enumeration rather than a set of strings because the
  rule is mechanical and therefore testable: a block that is not in the vocabulary
  cannot be built, so "minimum data to the provider, no personal data"
  (ADR-0007:39) is enforced by a type, not by review. Fragments and the user's
  text travel **as data**: the assistant has no writing tools and its output is
  text only (docs/09:75), so no section exists that could carry an instruction.
* **The five rules**, spelled out in plain Spanish exactly as docs/06 §9 states
  them, because the docs mandate this copy and the prompt must not drift from it.

Spanish lives here and only here: the rest of the module is English, per
AGENTS.md.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from techcamp.assistant.domain.errors import PromptSectionNotAllowedError


class PromptSection(StrEnum):
    """The blocks an outbound prompt may carry (D-T12.6).

    The four are the whole send-list of docs/06 §9: the system prompt (the rules
    below), the plot facts T4 assembles from the facades, the fragments retrieval
    returned, and the user's question. There is deliberately no section for
    contact data, consent state or plot geometry: none of that may leave the
    server (ADR-0007:39).
    """

    SYSTEM = "system"
    PLOT_FACTS = "plot_facts"
    FRAGMENTS = "fragments"
    QUESTION = "question"


PROMPT_RULES: tuple[str, ...] = (
    "Responder en español sencillo.",
    "Usar solo los hechos de la parcela y los fragmentos entregados, y citar la fuente.",
    "Si no hay información suficiente, decirlo.",
    "Nunca inventar dosis de agroquímicos: remitir a la etiqueta del producto "
    "registrado ante el ICA y al técnico.",
    "El asistente explica las recomendaciones y las alertas que calcula el "
    "sistema; no calcula riego ni riesgo por su cuenta.",
)
"""The five rules of docs/06-diseno-detallado.md §9, in the docs' own words and
order: plain Spanish; only the delivered plot facts and fragments, with the
source cited; say so when the evidence is insufficient; never invent
agrochemical doses — point to the product label registered with ICA and to the
technician; the assistant **explains** what the system computes and never
computes irrigation or risk (ADR-0007:16)."""

_SYSTEM_HEADER = "Eres el asistente agronómico de TechCamp."
"""One framing line so the rules read as instructions. A header, not a rule:
nothing in it adds a constraint docs/06 §9 does not state."""


def build_system_prompt() -> str:
    """The system prompt: the framing line and every rule of `PROMPT_RULES`.

    Assembled here and nowhere else, so the text a test pins is the text the
    provider receives. The caller owns the blocks around it — the facts, the
    fragments and the question (D-T12.6).
    """
    rules = "\n".join(f"- {rule}" for rule in PROMPT_RULES)
    return f"{_SYSTEM_HEADER}\n{rules}"


def validate_prompt_sections(sections: Iterable[PromptSection | str]) -> tuple[PromptSection, ...]:
    """The given sections as `PromptSection` members, in the order they came.

    The outbound payload is a declared list (D-T12.6), so the assembly step
    checks itself against the allow-list instead of trusting the caller. Section
    names are coerced, because the blocks arrive from the fact builder and from
    T5's assembly; anything else — including a value that is not a section at all
    — raises `PromptSectionNotAllowedError`.

    Order is preserved and duplicates are kept: which section comes first, and
    whether the same block appears twice, is the prompt's business and not a rule
    this module has a doc for.
    """
    validated: list[PromptSection] = []
    for section in sections:
        try:
            validated.append(PromptSection(section))
        except ValueError:
            raise PromptSectionNotAllowedError(section) from None
    return tuple(validated)
