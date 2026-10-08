"""The replay: the deterministic half of the assistant flow, stopped (E12 T8;
docs/06-diseno-detallado.md §9; ADR-0007:15).

The replay is the whole harness in one function: it takes a question, the pinned
corpus and the embedder, and produces exactly what a human agronomist must be
given to grade an answer — the question, the fragments retrieval resolved, the
citations those fragments become, and the **exact prompt the provider would
receive**. It calls the real `application.retrieval.retrieve_fragments` and the
real `adapters.api.prompt.build_prompt`, and it stops there.

Five behaviours are pinned here, and each one is a way a replay that "works"
still misleads the person grading it:

* **It runs the real use case and the real prompt builder.** A replay built over
  a second implementation would grade something the product does not do.
* **The prompt carries the four sections in docs/06 §9's order.** System first,
  so the rules are read before any data; the question last, so it is the last
  thing the model reads (D-T12.6).
* **A question the corpus cannot answer replays as an explicit absence.** Empty
  fragments, empty citations, and a fragment block that *says* there are none —
  which is what lets the model answer "si no hay información suficiente,
  decirlo" instead of inventing one.
* **The same question replays to the same value.** Two replays a human cannot
  compare are two evaluations, not one run twice.
* **No provider is reachable.** Not through a parameter, not through a module
  global, not even through a test double: the structural guard iterates the
  package's own namespaces.

The corpus under test is a small hand-built one, not the fixture corpus. The
fixture corpus plus the seminar stand-in retrieves nothing above ADR-0008:12's
0.75 floor — measured and recorded in
`tests/assistant/eval/test_corpus_pin.py::test_the_stand_in_reaches_no_fragment_above_the_floor`
— so a replay of it would show an empty prompt block for every question, and the
fragment path, the citations and `top_k` would go untested. The stand-in scores
by shared words, so a short fragment and a question that shares them retrieve.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from types import ModuleType
from uuid import UUID

import pytest

from assistant.eval import corpus_pin, eval_set, replay
from assistant.eval.corpus_pin import (
    DOCUMENT_ID_NAMESPACE,
    SeededKnowledgeRepository,
    document_id,
    pin_corpus,
)
from assistant.eval.eval_set import EVAL_SET_NAME, EvalQuestion, load_eval_set
from assistant.eval.replay import QuestionReplay, ReplayReport, replay_all, replay_question
from techcamp.assistant.adapters.api.prompt import OUTBOUND_SECTIONS, build_prompt
from techcamp.assistant.adapters.jobs import SeminarHashEmbedder
from techcamp.assistant.application.ports import LLMClient
from techcamp.assistant.application.retrieval import RetrievedEvidence, retrieve_fragments
from techcamp.assistant.domain.limits import RETRIEVAL_TOP_K, SIMILARITY_FLOOR
from techcamp.assistant.domain.models import MessageRole
from techcamp.assistant.domain.prompt_policy import build_system_prompt

pytestmark = pytest.mark.anyio

REPLAY_QUESTION = "¿Con qué frecuencia se revisa el filtro de malla en el lote?"
"""A producer question about filter maintenance, in the corpus's own vocabulary.

It is also the question the stand-in embedder can retrieve: the fragments below
are short and share its words, so their cosine is above the floor. The fixture
corpus's fragments are 300 words and retrieve nothing — see this module's
docstring."""

UNRELATED_QUESTION = "¿Cuántas sucursales del banco hay en Bogotá?"

GUIDE_ID = UUID("aaaaaaaa-0000-4000-8000-000000000001")
BULLETIN_ID = UUID("aaaaaaaa-0000-4000-8000-000000000002")

FILTER_FRAGMENTS = (
    "El filtro de malla se revisa cada mes en el lote.",
    "El filtro de malla se revisa cada mes en el lote de goteo.",
    "El filtro de malla se revisa cada mes en el lote del sistema.",
)
"""Three fragments about filter maintenance, all above the floor for
`REPLAY_QUESTION`, all of the same document."""

OTHER_FRAGMENT = (
    "La evapotranspiración de referencia del trimestre más caluroso "
    "ronda los 4,5 mm diarios en el Caribe."
)
"""A fragment the question does not share a subject with: the corpus's way of
holding something that is real and still not the answer."""


def _embedder() -> SeminarHashEmbedder:
    """The seminar stand-in, the embedder the ingestion job uses by default.

    The same instance type is used to seed a corpus and to embed a question,
    because a vector the store and the query disagree about is not a similarity.
    """
    return SeminarHashEmbedder()


def _documents() -> SeededKnowledgeRepository:
    """The two documents the corpus's citations are built from: a licence-reviewed
    guide and the departmental bulletin (docs/06 §9's two kinds)."""
    knowledge = SeededKnowledgeRepository()
    knowledge.add_document(
        document_id=GUIDE_ID,
        title="Guía de riego por goteo para el Caribe colombiano (fixture)",
        kind="guide",
        source_url="https://example.invalid/guia-riego-goteo-caribe",
        license="CC-BY-4.0 (test fixture)",
    )
    knowledge.add_document(
        document_id=BULLETIN_ID,
        title="Boletín Mesa Técnica Agroclimática de Córdoba — septiembre de 2026 (fixture)",
        kind="agroclimatic_bulletin",
        source_url="https://example.invalid/boletin-mesa-tecnica-cordoba-2026-09",
        license="CC-BY-4.0 (test fixture)",
        department="Córdoba",
    )
    return knowledge


async def _corpus(*fragments: tuple[UUID, str]) -> SeededKnowledgeRepository:
    """A corpus of the two documents above plus `fragments`, each embedded with the
    embedder the question is embedded with — the same pair the real store has."""
    knowledge = _documents()
    embedder = SeminarHashEmbedder()
    vectors = await embedder.embed([text for _, text in fragments])
    for (identifier, text), vector in zip(fragments, vectors, strict=True):
        knowledge.add_fragment(document_id=identifier, content=text, embedding=vector)
    return knowledge


def _mini_corpus() -> Awaitable[SeededKnowledgeRepository]:
    return _corpus(
        *((GUIDE_ID, text) for text in FILTER_FRAGMENTS),
        (BULLETIN_ID, OTHER_FRAGMENT),
    )


async def _corpus_with_many_fragments() -> SeededKnowledgeRepository:
    """Eight qualifying fragments of one document, so ADR-0008:12's five have
    something to cut. The eight are the same maintenance instruction phrased over
    eight lots: the stand-in embedder scores by shared words, so that is what
    puts them all above the floor."""
    return await _corpus(
        *(
            (GUIDE_ID, f"El filtro de malla se revisa cada mes en el lote {tail}.")
            for tail in (
                "",
                "de goteo",
                "del sistema",
                "de riego",
                "de maiz",
                "de yuca",
                "de la finca",
                "de la parcela",
            )
        )
    )


# --- the real use case and the real prompt -------------------------------


async def test_a_replay_is_the_real_retrieval_and_the_real_prompt_assembled() -> None:
    """The replay is a composition of the two real steps, not a reimplementation
    of either: its fragments and citations are what `retrieve_fragments` returns
    and its prompt is what `build_prompt` builds from that same evidence.

    Asserted by computing both independently and comparing, and by the module's
    own names resolving to the real functions — so a replay that quietly swapped
    in a stand-in would be caught here.
    """
    knowledge = await _mini_corpus()
    embedder = _embedder()
    question = EvalQuestion(id="q99", question=REPLAY_QUESTION)

    found = await replay_question(question, knowledge, embedder=embedder)

    evidence = await retrieve_fragments(
        question=REPLAY_QUESTION, embedder=embedder, knowledge=knowledge
    )
    assert found.fragments == evidence.fragments
    assert found.citations == evidence.citations
    assert found.prompt == build_prompt(facts=None, evidence=evidence, question=REPLAY_QUESTION)
    assert found.question is question

    # And the module the replay calls through is bound to the real ones: a
    # stand-in swapped into `replay`'s namespace would be caught here.
    bound = vars(replay)
    assert bound["retrieve_fragments"] is retrieve_fragments
    assert bound["build_prompt"] is build_prompt
    assert replay.__all__ == ["QuestionReplay", "ReplayReport", "replay_all", "replay_question"]


def test_a_replay_carries_the_question_and_its_three_outputs() -> None:
    """The shape the agronomist reads: the question, what was retrieved, what
    would be cited, and the exact outbound prompt. Four fields and no fifth —
    no score, no grade, no threshold, no verdict."""
    fields = {field.name for field in QuestionReplay.__dataclass_fields__.values()}

    assert fields == {"question", "fragments", "citations", "prompt"}


# --- the send-list -------------------------------------------------------


async def test_the_prompt_carries_the_four_sections_in_outbound_sections_order() -> None:
    """docs/06 §9's send-list (D-T12.6), in the order `OUTBOUND_SECTIONS` declares:
    system, plot facts, fragments, question.

    The order is the contract — system first so the rules are read before any
    data, question last so it is the last thing the model reads — and the roles
    are what makes it hold: the rules are a **system** message, and the fragments
    and the producer's own words are user messages, so corpus text cannot reach
    the model in the same voice as the rules (docs/09:75).
    """
    knowledge = await _mini_corpus()
    embedder = _embedder()
    question = EvalQuestion(id="q99", question=REPLAY_QUESTION)

    found = await replay_question(question, knowledge, embedder=embedder)

    messages = found.prompt
    assert len(messages) == len(OUTBOUND_SECTIONS) == 4
    assert [message.role for message in messages] == [
        MessageRole.SYSTEM,
        MessageRole.USER,
        MessageRole.USER,
        MessageRole.USER,
    ]
    # `build_prompt`'s own wording, read off its output: the system block is the
    # system prompt, the fact block says so when no plot is named, the fragment
    # block is the retrieved evidence, and the question block ends with the
    # producer's own words.
    assert messages[0].content == build_system_prompt()
    assert "sin datos de parcela" in messages[1].content
    assert "Fragmentos recuperados" in messages[2].content
    assert messages[3].content.endswith(question.question)


async def test_a_question_that_names_no_plot_replays_without_facts() -> None:
    """`facts` defaults to `None` (docs/04-api.md:227 makes `plot_id` optional),
    and the fact block is still emitted saying so rather than silently dropped:
    docs/06 §9's "si no hay información suficiente, decirlo" needs the absence to
    be visible to the model and to the grader."""
    knowledge = await _mini_corpus()
    embedder = _embedder()
    question = EvalQuestion(id="q99", question=REPLAY_QUESTION)

    found = await replay_question(question, knowledge, embedder=embedder)

    assert "sin datos de parcela" in found.prompt[1].content
    assert found.prompt == build_prompt(
        facts=None, evidence=_evidence_of(found), question=question.question
    )


def _evidence_of(found: QuestionReplay) -> RetrievedEvidence:
    return RetrievedEvidence(fragments=found.fragments, citations=found.citations)


# --- the corpus cannot answer everything ---------------------------------


async def test_a_question_with_no_corpus_overlap_replays_as_an_explicit_absence() -> None:
    """ADR-0008:12's floor makes an unmatched question an **answer**, not a
    failure: no fragments, no citations, and a prompt whose fragment block says
    there are none. That is what lets the model say so instead of inventing a
    source.

    The wording is asserted through `build_prompt`'s own output — the block the
    real builder emits for empty evidence — and not through anything written
    here.
    """
    knowledge = await _mini_corpus()
    embedder = _embedder()
    question = EvalQuestion(id="q98", question=UNRELATED_QUESTION)

    found = await replay_question(question, knowledge, embedder=embedder)

    assert found.fragments == ()
    assert found.citations == ()
    assert (
        found.prompt[2].content
        == build_prompt(
            facts=None,
            evidence=RetrievedEvidence(fragments=(), citations=()),
            question=UNRELATED_QUESTION,
        )[2].content
    )
    # And that block really does name the absence: the phrase is
    # `adapters.api.prompt`'s, read off what it rendered above.
    assert "ninguno" in found.prompt[2].content


# --- determinism ---------------------------------------------------------


async def test_replaying_the_same_question_twice_gives_an_equal_replay() -> None:
    """A replay a human cannot compare with the one before it is not a replay.

    Every field is derived — the question from the set, the fragments from
    `retrieve_fragments` over the seeded corpus, the citations from those
    fragments, the prompt from `build_prompt` — so the whole `QuestionReplay` is
    equal, `RetrievedFragment.id` included. The id is the seed's own
    (`corpus_pin.SeededKnowledgeRepository`) rather than a fresh `uuid4()` per
    search, which is what a stored `kb_chunk` row does: one row, one id.
    """
    knowledge = await _mini_corpus()
    embedder = _embedder()
    question = EvalQuestion(id="q99", question=REPLAY_QUESTION)

    first = await replay_question(question, knowledge, embedder=embedder)
    second = await replay_question(question, knowledge, embedder=embedder)

    assert first == second
    assert first.fragments


async def test_a_replay_carries_at_most_retrieval_top_k_fragments() -> None:
    """ADR-0008:12 fixes five fragments. The count is the use case's own default
    (`domain.limits.RETRIEVAL_TOP_K`) and the harness passes nothing, so a corpus
    holding eight qualifying fragments replays five."""
    knowledge = await _corpus_with_many_fragments()
    embedder = _embedder()
    question = EvalQuestion(id="q99", question=REPLAY_QUESTION)

    found = await replay_question(question, knowledge, embedder=embedder)

    assert len(found.fragments) == RETRIEVAL_TOP_K
    assert all(fragment.similarity >= SIMILARITY_FLOOR for fragment in found.fragments)


async def test_the_fragments_arrive_best_first_and_the_citations_follow() -> None:
    """The port promises best-first and the replay does not re-sort: an order the
    harness cannot reproduce is an order it cannot grade. One document that
    matched twice is one source, with the best score."""
    knowledge = await _corpus_with_many_fragments()
    embedder = _embedder()
    question = EvalQuestion(id="q99", question=REPLAY_QUESTION)

    found = await replay_question(question, knowledge, embedder=embedder)

    similarities = [fragment.similarity for fragment in found.fragments]
    assert similarities == sorted(similarities, reverse=True)
    assert found.citations
    # Every citation retrieval built carries its similarity, because the score is
    # what selected it; a citation without one would cite on nothing.
    for citation in found.citations:
        assert citation.similarity is not None
        assert citation.similarity >= SIMILARITY_FLOOR


# --- the whole set -------------------------------------------------------


async def test_replay_all_replays_the_whole_set_in_order_and_records_the_corpus() -> None:
    """`replay_all` walks the set in the set's order and records which corpus it
    walked, so the artifact names the bytes it replayed. 30 questions, 30
    replays, same ids in the same order."""
    questions = load_eval_set()
    corpus = pin_corpus()
    knowledge = await corpus_pin.seed_knowledge_repository(corpus, embedder=_embedder())

    report = await replay_all(
        questions=questions,
        knowledge=knowledge,
        embedder=_embedder(),
        corpus=corpus,
    )

    assert isinstance(report, ReplayReport)
    assert len(report.replays) == len(questions) == 30
    assert [replay_.question.id for replay_ in report.replays] == [
        question.id for question in questions
    ]
    assert report.corpus == corpus
    assert report.eval_set == EVAL_SET_NAME
    assert report.replays == tuple(
        [await replay_question(question, knowledge, embedder=_embedder()) for question in questions]
    )


async def test_replay_all_can_name_a_set_of_its_own() -> None:
    """The report records the set it replayed. A producer-collected set that
    replaced the fixture would carry its own name, and the artifact must not
    claim `e12-t8-caribe-30` for it."""
    questions = (EvalQuestion(id="q01", question="¿Qué es el riego por goteo?"),)
    corpus = pin_corpus()
    knowledge = await corpus_pin.seed_knowledge_repository(corpus, embedder=_embedder())

    report = await replay_all(
        questions=questions,
        knowledge=knowledge,
        embedder=_embedder(),
        corpus=corpus,
        eval_set="otro-conjunto",
    )

    assert report.eval_set == "otro-conjunto"


def test_the_report_shape_holds_a_set_a_corpus_and_the_replays() -> None:
    """Three fields, and the corpus is the pin that says which bytes were
    replayed."""
    fields = {field.name for field in ReplayReport.__dataclass_fields__.values()}

    assert fields == {"eval_set", "corpus", "replays"}


# --- no provider is reachable --------------------------------------------


REPLAY_PATH_MODULES = (corpus_pin, eval_set, replay)
"""The modules the replay path is made of, exactly the three the brief names."""


def _public_functions(module: ModuleType) -> list[tuple[str, Callable[..., object]]]:
    """Every function `module` itself declares publicly, by its own name."""
    return [
        (name, value)
        for name, value in vars(module).items()
        if not name.startswith("_")
        and inspect.isfunction(value)
        and value.__module__ == module.__name__
    ]


def test_no_public_function_on_the_replay_path_takes_a_provider() -> None:
    """The same structural argument `application.usage` and
    `application.retrieval` make: the harness has no way to name a provider, so
    it cannot call one. A parameter named for one, or annotated with
    `LLMClient`, would be the whole hole."""
    for module in REPLAY_PATH_MODULES:
        for _name, function in _public_functions(module):
            for parameter in inspect.signature(function).parameters.values():
                assert parameter.name not in {"llm", "provider", "llm_client", "client"}
                assert "LLM" not in str(parameter.annotation)


def test_the_eval_package_holds_no_llm_client_in_its_module_globals() -> None:
    """Even a test double must not be reachable. A `FakeLLMClient` sitting in a
    module global would be one `stream()` away from a call, and the harness's
    whole promise is that it stops before the provider."""
    for module in REPLAY_PATH_MODULES:
        for name, value in vars(module).items():
            assert not isinstance(value, LLMClient), f"{module.__name__}.{name}"


def test_no_replay_path_module_imports_a_provider_double() -> None:
    """`assistant.doubles.FakeLLMClient` is the only provider double in the
    assistant's test doubles, and none of these modules may reach it — not for a
    call, and not for a name."""
    for module in REPLAY_PATH_MODULES:
        assert "FakeLLMClient" not in vars(module)


def test_the_document_ids_are_never_random() -> None:
    """A `uuid4()` would make each replay a corpus nobody has seen before; the
    derivation is the digest's."""
    assert document_id("a" * 64) == document_id("a" * 64)
    assert document_id("a" * 64) != document_id("b" * 64)
    assert DOCUMENT_ID_NAMESPACE.version == 5
