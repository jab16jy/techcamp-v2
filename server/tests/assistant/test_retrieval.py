"""The retrieval use case (E12 T4; ADR-0008:12; docs/06-diseno-detallado.md §9;
docs/04-api.md §Riesgo, métricas y asistente).

docs/06 §9: "embedding de la pregunta → top 5 fragmentos (similitud ≥ 0,75)". The
threshold and the count are the two named constants of `domain.limits`, so a test
can prove they are the constants and not literals written into a query.

Four behaviours are pinned here, and each is a way a retrieval that "works" still
fails the producer:

* **0.74 is excluded and 0.75 is included** — the floor is inclusive (`>=`), and
  one fragment below it must not reach the answer.
* **Fewer than five fragments is a legitimate answer**, not an error and not a
  padded one: a corpus that holds nothing close enough is evidence the assistant
  has to report as missing rather than fill in.
* **The floor is re-applied after the call.** A repository that ignored it must
  still not contribute a 0.74 fragment; this use case does not trust the port to
  have done what its docstring says.
* **A fragment whose document is gone is dropped.** An uncitable fragment is not
  evidence, and citing it would be a source nobody can open.
"""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from datetime import date
from uuid import UUID, uuid4

import pytest

from assistant.doubles import FakeKnowledgeRepository, fragment_vector_at
from techcamp.assistant.application.ports import RetrievedFragment
from techcamp.assistant.application.retrieval import RetrievedEvidence, retrieve_fragments
from techcamp.assistant.domain.citations import CitationKind, validate_citation
from techcamp.assistant.domain.limits import RETRIEVAL_TOP_K, SIMILARITY_FLOOR

pytestmark = pytest.mark.anyio

QUESTION = "¿por qué no regué ayer?"

GUIDE_ID = UUID("11111111-1111-1111-1111-111111111111")
BULLETIN_ID = UUID("22222222-2222-2222-2222-222222222222")


class RecordingEmbedder:
    """An `Embedder` whose vector for any text is `(1.0, 0.0)`, and which records
    what it was asked to embed.

    Local to this module on purpose — T1's doubles are left exactly as they are —
    and two-dimensional on purpose: T1's `fragment_vector_at` helper builds a chunk
    vector whose cosine against a **two-dimensional** query is exactly the
    similarity a test needs, so the query has to be two-dimensional too. Padding
    both to `EMBEDDING_DIMENSION` would make these tests assert on the padding
    rather than on the floor. The 384-dimension contract of the real embedder is
    `FakeEmbedder`'s own, pinned in `test_doubles`.
    """

    def __init__(self) -> None:
        self.calls: list[Sequence[str]] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts]


def _documents(knowledge: FakeKnowledgeRepository) -> FakeKnowledgeRepository:
    """A corpus with the two kinds docs/06 §9 names: a licence-reviewed guide and
    the national bulletin."""
    knowledge.add_document(
        document_id=GUIDE_ID,
        title="Guía de manejo del ají",
        kind="guide",
        source_url="https://example.org/aji",
        license="CC-BY-4.0",
    )
    knowledge.add_document(
        document_id=BULLETIN_ID,
        title="Boletín Agroclimático Nacional, octubre 2026",
        kind="agroclimatic_bulletin",
        source_url="https://example.org/ban-oct-2026",
        published_on=date(2026, 10, 1),
        department=None,
    )
    return knowledge


def _corpus(
    *chunks: tuple[UUID, float, str], ignore_similarity_floor: bool = False
) -> FakeKnowledgeRepository:
    """A corpus with both documents and one chunk per tuple, each built with a
    vector whose cosine against the question is exactly the given similarity."""
    knowledge = _documents(FakeKnowledgeRepository(ignore_similarity_floor=ignore_similarity_floor))
    for document_id, similarity, content in chunks:
        knowledge.add_fragment(
            document_id=document_id, content=content, embedding=fragment_vector_at(similarity)
        )
    return knowledge


# --- the two constants ----------------------------------------------------


async def test_the_named_constants_are_the_query_not_literals() -> None:
    """ADR-0008:12 fixes five fragments at ≥ 0.75, and the acceptance criterion
    says the threshold is a named constant rather than a literal in a query: the
    call the use case makes is recorded by the double and asserted here."""
    knowledge = _corpus((GUIDE_ID, 0.90, "Riego del ají."))

    await retrieve_fragments(question=QUESTION, embedder=RecordingEmbedder(), knowledge=knowledge)

    assert (RETRIEVAL_TOP_K, SIMILARITY_FLOOR) == (5, 0.75)
    _embedding, top_k, floor = knowledge.calls[0]
    assert (top_k, floor) == (RETRIEVAL_TOP_K, SIMILARITY_FLOOR)


async def test_the_similarity_floor_and_top_k_can_be_narrowed_by_the_caller() -> None:
    """Both are arguments so a different corpus or T8's evaluation can ask for
    something else, and the default stays ADR-0008's pair."""
    knowledge = _corpus((GUIDE_ID, 0.80, "Casi."), (BULLETIN_ID, 0.93, "Muy."))

    evidence = await retrieve_fragments(
        question=QUESTION,
        embedder=RecordingEmbedder(),
        knowledge=knowledge,
        top_k=1,
        similarity_floor=0.90,
    )

    assert [fragment.content for fragment in evidence.fragments] == ["Muy."]
    assert knowledge.calls[0][1:] == (1, 0.90)


# --- the floor ------------------------------------------------------------


async def test_a_fragment_at_the_floor_is_included_and_one_below_it_is_not() -> None:
    """`>=`, not `>`: 0.75 qualifies and 0.74 does not. A producer whose question
    matches a guide almost exactly must not lose it to a rounding decision, and the
    fragment one notch below the floor must not be padded in."""
    knowledge = _corpus(
        (GUIDE_ID, 0.75, "El riego del ají se hace en la mañana."),
        (BULLETIN_ID, 0.74, "Octubre con lluvias por debajo del promedio."),
    )

    evidence = await retrieve_fragments(
        question=QUESTION, embedder=RecordingEmbedder(), knowledge=knowledge
    )

    assert [fragment.content for fragment in evidence.fragments] == [
        "El riego del ají se hace en la mañana."
    ]


async def test_the_floor_is_re_applied_after_a_repository_that_ignored_it() -> None:
    """Defence in depth, and the reason the use case re-filters instead of
    trusting the port: an adapter that forgot the `WHERE` would contribute a 0.74
    fragment, and a 0.74 fragment is not evidence. The port's docstring is a
    promise; this is the check."""
    knowledge = _corpus((GUIDE_ID, 0.74, "Casi pertinente."), ignore_similarity_floor=True)

    evidence = await retrieve_fragments(
        question=QUESTION, embedder=RecordingEmbedder(), knowledge=knowledge
    )

    # The double really did hand the fragment over: the floor was ignored below it.
    assert knowledge.calls[0][2] == SIMILARITY_FLOOR
    assert evidence.fragments == ()
    assert evidence.citations == ()


# --- how many -------------------------------------------------------------


async def test_fewer_than_five_fragments_is_a_legitimate_answer() -> None:
    """ADR-0008:12 asks for the five closest; a young corpus may hold three that
    qualify. That is evidence the assistant reports, never an error and never a
    padded list of fragments below the floor."""
    knowledge = _corpus(
        (GUIDE_ID, 0.95, "Uno."), (BULLETIN_ID, 0.83, "Dos."), (GUIDE_ID, 0.77, "Tres.")
    )

    evidence = await retrieve_fragments(
        question=QUESTION, embedder=RecordingEmbedder(), knowledge=knowledge
    )

    assert len(evidence.fragments) == 3
    assert len(evidence.citations) == 2


async def test_at_most_five_fragments_are_returned_best_first() -> None:
    """docs/06 §9's "top 5". The port promises best-first and this use case does
    not re-sort: an order the caller cannot reproduce is an order it cannot test."""
    knowledge = _corpus(
        (GUIDE_ID, 0.76, "Cinco"),
        (GUIDE_ID, 0.99, "Uno"),
        (BULLETIN_ID, 0.88, "Tres"),
        (GUIDE_ID, 0.92, "Dos"),
        (BULLETIN_ID, 0.81, "Cuatro"),
        (GUIDE_ID, 0.70, "Seis"),
    )

    evidence = await retrieve_fragments(
        question=QUESTION, embedder=RecordingEmbedder(), knowledge=knowledge
    )

    assert [fragment.content for fragment in evidence.fragments] == [
        "Uno",
        "Dos",
        "Tres",
        "Cuatro",
        "Cinco",
    ]


# --- the documents behind the fragments -----------------------------------


async def test_a_fragment_whose_document_is_gone_is_dropped_rather_than_cited() -> None:
    """An uncitable fragment is not evidence. The corpus holds a chunk whose
    `document_id` no document answers — a document removed after ingestion, or a
    chunk whose document was never committed — and the fragment is dropped rather
    than sent as text with no source behind it. ADR-0008:12 records source **and**
    licence per document, so a source-less chunk cannot be cited."""
    knowledge = _corpus((GUIDE_ID, 0.95, "Con documento."))
    knowledge.add_fragment(
        document_id=uuid4(), content="Sin documento.", embedding=fragment_vector_at(0.99)
    )

    evidence = await retrieve_fragments(
        question=QUESTION, embedder=RecordingEmbedder(), knowledge=knowledge
    )

    assert [fragment.content for fragment in evidence.fragments] == ["Con documento."]
    assert len(evidence.citations) == 1
    assert evidence.citations[0].title == "Guía de manejo del ají"
    # `kb_document` stores no institution column, so the link is the provenance a
    # citation carries (`ADR-0008:12` records source and licence per document).
    assert evidence.citations[0].source == "https://example.org/aji"


async def test_two_fragments_of_one_document_are_cited_once_with_the_best_score() -> None:
    """The producer reads a list of sources, and a document that matched twice is
    one source. The score shown is the best one, because that is the fragment the
    answer leaned on."""
    knowledge = _corpus((GUIDE_ID, 0.81, "Primera parte."), (GUIDE_ID, 0.94, "Segunda parte."))

    evidence = await retrieve_fragments(
        question=QUESTION, embedder=RecordingEmbedder(), knowledge=knowledge
    )

    assert len(evidence.fragments) == 2
    assert len(evidence.citations) == 1
    assert evidence.citations[0].similarity == pytest.approx(0.94)


async def test_every_citation_carries_its_document_and_passes_validation() -> None:
    """docs/04-api.md §Riesgo: an answer cites the source. `validate_citation` runs
    on every citation this use case builds, so a shape it refuses is refused here
    and not on the way to the producer."""
    knowledge = _corpus((GUIDE_ID, 0.95, "Riego."), (BULLETIN_ID, 0.88, "Lluvias de octubre."))

    evidence = await retrieve_fragments(
        question=QUESTION, embedder=RecordingEmbedder(), knowledge=knowledge
    )

    kinds = {citation.title: citation.kind for citation in evidence.citations}
    assert kinds == {
        "Guía de manejo del ají": CitationKind.GUIDE,
        "Boletín Agroclimático Nacional, octubre 2026": CitationKind.AGROCLIMATIC_BULLETIN,
    }
    for citation in evidence.citations:
        validate_citation(citation)
    bulletin = next(c for c in evidence.citations if c.kind is CitationKind.AGROCLIMATIC_BULLETIN)
    assert bulletin.source_url == "https://example.org/ban-oct-2026"
    assert bulletin.published_on is not None
    assert bulletin.similarity == pytest.approx(0.88)


# --- the embedding boundary -----------------------------------------------


async def test_the_question_is_embedded_once_and_the_fragments_are_the_stored_ones() -> None:
    """The embedding call carries the question alone: nothing in ADR-0008
    retrieves on the plot's facts, and embedding them would put a plot's data
    through a second path the send-list of D-T12.6 does not describe.

    The fragments returned are the stored chunks with their own `id` and
    `similarity`, because T5's prompt builder quotes `content` and the evidence
    behind the answer is the fragment's id rather than a re-embedding of it."""
    embedder = RecordingEmbedder()
    knowledge = _corpus((GUIDE_ID, 0.9, "Contenido."))

    evidence = await retrieve_fragments(question=QUESTION, embedder=embedder, knowledge=knowledge)

    assert embedder.calls == [[QUESTION]]
    fragment = evidence.fragments[0]
    assert isinstance(fragment, RetrievedFragment)
    assert fragment.content == "Contenido."
    assert fragment.document_id == GUIDE_ID
    assert fragment.similarity == pytest.approx(0.9)


async def test_an_empty_corpus_is_an_empty_answer_and_not_an_error() -> None:
    """A seminar corpus with nothing close enough — docs/06 §9's fixed fixtures, or
    a licence review that has not happened yet (ADR-0008:32) — leaves the assistant
    with the plot's facts alone. That is the shape the degraded answer handles, so
    an empty retrieval is a normal answer and not a failure."""
    evidence = await retrieve_fragments(
        question=QUESTION, embedder=RecordingEmbedder(), knowledge=FakeKnowledgeRepository()
    )

    assert evidence == RetrievedEvidence(fragments=(), citations=())
    assert evidence.fragments == ()
    assert evidence.citations == ()


# --- the shape of the use case --------------------------------------------


def test_the_use_case_takes_no_llm_port() -> None:
    """The same structural argument `application.usage` makes for "budget exhausted
    → zero provider calls": retrieval cannot call a provider because its signature
    has no way to name one."""
    parameters = inspect.signature(retrieve_fragments).parameters

    assert "llm" not in parameters
    assert all("LLM" not in str(parameter.annotation) for parameter in parameters.values())
