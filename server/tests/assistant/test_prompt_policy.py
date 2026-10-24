"""The prompt policy as data: the sections an outbound prompt may carry and the
five rules docs/06-diseno-detallado.md §9 mandates (E12 T1).

The send-list is mechanical and therefore testable (D-T12.6): the prompt carries
the system prompt, the plot facts, the retrieved fragments and the question, and
nothing else. The vocabulary itself is the allow-list, so a section that does not
exist is a domain error rather than a silently accepted extra block.
"""

from __future__ import annotations

import pytest

from techcamp.assistant.domain.errors import PromptSectionNotAllowedError
from techcamp.assistant.domain.prompt_policy import (
    PROMPT_RULES,
    PromptSection,
    build_system_prompt,
    validate_prompt_sections,
)


def test_the_prompt_carries_exactly_four_sections() -> None:
    """D-T12.6: the system prompt, the fact block, the retrieved fragments and
    the question — and nothing else. There is no section for a phone number, a
    technician's contact data or the plot's geometry, because a block that is not
    in the vocabulary cannot be built."""
    assert {section.value for section in PromptSection} == {
        "system",
        "plot_facts",
        "fragments",
        "question",
    }


def test_the_full_send_list_is_accepted_in_order() -> None:
    sections = [
        PromptSection.SYSTEM,
        PromptSection.PLOT_FACTS,
        PromptSection.FRAGMENTS,
        PromptSection.QUESTION,
    ]

    assert validate_prompt_sections(sections) == tuple(sections)


def test_validate_coerces_the_section_names_into_the_enum() -> None:
    """T5 assembles the prompt from the section names it decided on, and the
    vocabulary is what decides whether a block is allowed."""
    assert validate_prompt_sections(["system", "question"]) == (
        PromptSection.SYSTEM,
        PromptSection.QUESTION,
    )


def test_a_section_outside_the_allow_list_is_a_domain_error() -> None:
    with pytest.raises(PromptSectionNotAllowedError) as raised:
        validate_prompt_sections(["system", "technician_contact"])

    assert "technician_contact" in str(raised.value)


def test_a_value_that_is_not_a_section_at_all_is_a_domain_error() -> None:
    """The vocabulary is checked at runtime, because the block list reaches the
    prompt from a fact builder this module does not own."""
    rejected = object()

    with pytest.raises(PromptSectionNotAllowedError):
        validate_prompt_sections([rejected])


def test_there_are_five_rules_and_they_are_spelled_out_in_spanish() -> None:
    # docs/06 §9 "Reglas del prompt" lists exactly five bullets.
    assert len(PROMPT_RULES) == 5
    assert all(rule.strip() for rule in PROMPT_RULES)


def test_the_rules_pin_plain_spanish_the_source_and_insufficient_evidence() -> None:
    rules = " ".join(PROMPT_RULES)

    assert "español sencillo" in rules
    assert "citar la fuente" in rules
    assert "información suficiente" in rules


def test_the_rules_forbid_inventing_a_dose_and_point_to_the_ica_label() -> None:
    """H7 (docs/06:386): never invent agrochemical doses — refer to the product
    label registered with ICA and to the technician."""
    rules = " ".join(PROMPT_RULES)

    assert "Nunca inventar dosis de agroquímicos" in rules
    assert "ICA" in rules
    assert "técnico" in rules


def test_the_rules_keep_the_llm_from_computing_anything() -> None:
    """H1 (ADR-0007:16): the LLM explains the system's recommendations and alerts;
    it does not compute irrigation, risk or doses."""
    rules = " ".join(PROMPT_RULES)

    assert "no calcula riego ni riesgo" in rules


def test_the_system_prompt_carries_every_rule() -> None:
    prompt = build_system_prompt()

    assert all(rule in prompt for rule in PROMPT_RULES)


def test_the_system_prompt_is_plain_spanish_and_reads_as_an_instruction() -> None:
    prompt = build_system_prompt()

    assert prompt.startswith("Eres el asistente agronómico")
    assert "Nunca inventar dosis" in prompt
