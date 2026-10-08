"""The corpus pin, and the repository the replay runs against (E12 T8;
ADR-0008:12; ADR-0008:32).

A replay is only worth grading if a reader knows **which bytes** produced it. The
pin is that record: one entry per document, with the digest of the file on disk
and the fragment count `chunk_text` derived from it. `seed_knowledge_repository`
then turns the pin into a `FakeKnowledgeRepository` seeded through the same
`chunk_text` ingestion uses, so the replay runs against the corpus the ingestion
would have stored rather than against a copy somebody edited.

Four behaviours are pinned here, and each is a way a replay that "works" is
still worthless:

* **The digests are the files'.** A pin whose digest disagreed with the corpus
  would name bytes nobody replayed, and the artifact would not be reproducible.
* **The ids are derived, never random.** `uuid4()` would make every run a
  different corpus, so a document id in one artifact would never match the next.
* **The ENSO state is retrievable from the bulletin that declares it.** Córdoba
  declares `El Niño` for September 2026 and the national bulletin declares
  `Neutral`; a question about the September ENSO state must rank Córdoba first,
  or the corpus cannot tell the two apart.
* **The stand-in embedder has a measured boundary**, recorded rather than hidden:
  see `test_the_stand_in_reaches_no_fragment_above_the_floor`.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from uuid import uuid5

import pytest

from assistant.doubles import FakeKnowledgeRepository
from assistant.eval.corpus_pin import (
    DOCUMENT_ID_NAMESPACE,
    CorpusPin,
    CorpusPinEntry,
    CorpusPinError,
    document_id,
    pin_corpus,
    seed_knowledge_repository,
)
from techcamp.assistant.adapters.jobs import SeminarHashEmbedder
from techcamp.assistant.adapters.kb_ingest import (
    FIXTURE_CORPUS_DIR,
    chunk_text,
    load_corpus,
)
from techcamp.assistant.application.ports import KnowledgeDocument
from techcamp.assistant.domain.limits import RETRIEVAL_TOP_K, SIMILARITY_FLOOR

# `anyio`, not `asyncio_mode`: the repo runs its async tests through
# `tests/conftest.py`'s `anyio_backend`. The marker is module-wide, like
# `tests/assistant/test_retrieval.py` does, and the sync tests of this module are
# untouched by it.
pytestmark = pytest.mark.anyio

ENSO_QUESTION = (
    "¿En qué estado ENSO estaba la Mesa Técnica Agroclimática de Córdoba en septiembre de 2026?"
)
"""A question about the September ENSO state: exactly what the two bulletins
disagree about. Córdoba declares `El Niño`, the national bulletin declares
`Neutral`, the guide declares neither."""

UNRELATED_QUESTION = "¿Cuántas sucursales del banco hay en Bogotá?"
"""A question with no word in common with any fragment of the corpus: the
"insufficient evidence" case ADR-0008:12's floor exists for."""


def _sources() -> dict[str, object]:
    """Every corpus file by digest, so a pin can be checked against the bytes."""
    return {source.sha256: source for source in load_corpus(FIXTURE_CORPUS_DIR)}


def _cordoba_sha256(pin: CorpusPin) -> str:
    for entry in pin.documents:
        if entry.department == "Córdoba":
            return entry.sha256
    raise AssertionError("the fixture corpus holds no Córdoba bulletin")


# --- the pin records the bytes -------------------------------------------


def test_the_pinned_corpus_is_the_packaged_fixture_corpus() -> None:
    """The default is the packaged fixtures (ADR-0021's seminar profile,
    ADR-0008:32's fixture corpus), not a directory resolved from the process's
    working directory."""
    pin = pin_corpus()

    assert isinstance(pin.directory, Path)
    assert pin.directory == FIXTURE_CORPUS_DIR
    assert len(pin.documents) == 3


def test_the_pinned_digests_are_the_sha256_of_the_corpus_files() -> None:
    """The digest is the file's, byte for byte: `hashlib.sha256` over the bytes
    on disk, exactly what `CorpusSource.sha256` records. A pin that disagreed
    with the corpus would name bytes nobody replayed."""
    sources = _sources()

    for entry in pin_corpus().documents:
        source = sources[entry.sha256]
        assert entry.sha256 == hashlib.sha256(source.path.read_bytes()).hexdigest()  # type: ignore[attr-defined]


def test_every_entry_carries_what_a_citation_needs_from_it() -> None:
    """Source, licence and publication date are what `Citation` is built from
    (ADR-0008:12 records source **and** licence per document). A pin that
    dropped one would leave the agronomist unable to open the document."""
    pin = pin_corpus()

    assert all(isinstance(entry, CorpusPinEntry) for entry in pin.documents)
    for entry in pin.documents:
        assert entry.title
        assert entry.source_url.startswith("https://")
        assert entry.license
        assert entry.kind in {"guide", "agroclimatic_bulletin"}
        assert isinstance(entry.published_on, date)
        assert entry.sha256
        assert entry.chunk_count > 0


def test_the_chunk_count_is_the_chunker_s_own() -> None:
    """The count comes from `chunk_text`, the function ingestion uses, so the pin
    and the stored corpus cannot disagree about how many fragments a document
    holds."""
    sources = _sources()

    for entry in pin_corpus().documents:
        source = sources[entry.sha256]
        assert entry.chunk_count == len(chunk_text(source.text))  # type: ignore[attr-defined]


def test_the_department_tells_the_regional_bulletin_from_the_national_one() -> None:
    """`department = null` is how docs/06 §9 tells the national bulletin from a
    regional one, and the pin keeps the distinction."""
    departments = {entry.title: entry.department for entry in pin_corpus().documents}

    assert departments["Boletín Agroclimático Nacional — septiembre de 2026 (fixture)"] is None
    assert (
        departments["Boletín Mesa Técnica Agroclimática de Córdoba — septiembre de 2026 (fixture)"]
        == "Córdoba"
    )


def test_pinning_twice_gives_the_same_pin() -> None:
    """A replay is reproducible from the pin alone: the same directory replayed
    twice records the same entries in the same order."""
    assert pin_corpus() == pin_corpus()


# --- the ids are derived --------------------------------------------------


def test_the_document_ids_are_derived_from_the_digest_and_never_random() -> None:
    """`uuid5` over the digest in a fixed namespace: the same document is the
    same id in every run, so a document id in one artifact matches the next. A
    `uuid4()` would make each replay a corpus nobody has seen before."""
    for entry in pin_corpus().documents:
        assert document_id(entry.sha256) == uuid5(DOCUMENT_ID_NAMESPACE, entry.sha256)


def test_the_document_ids_are_stable_across_two_pinning_runs() -> None:
    """Two pins, one set of ids. This is the property that makes a replay
    comparable with the one before it."""
    first = pin_corpus()
    second = pin_corpus()

    assert [document_id(entry.sha256) for entry in first.documents] == [
        document_id(entry.sha256) for entry in second.documents
    ]


# --- the repository the replay runs against ------------------------------


async def test_the_seeded_repository_is_the_in_memory_double() -> None:
    """A `FakeKnowledgeRepository`, so the replay runs in memory with no database
    and no ingestion job: the harness replays the corpus, it does not store it."""
    knowledge = await seed_knowledge_repository(pin_corpus(), embedder=SeminarHashEmbedder())

    assert isinstance(knowledge, FakeKnowledgeRepository)


async def test_the_seeded_repository_holds_every_document_with_its_metadata() -> None:
    """One stored document per pinned entry, with the fields a citation is built
    from: the pin is what was replayed, so the repository must hold what it
    says."""
    pin = pin_corpus()

    knowledge = await seed_knowledge_repository(pin, embedder=SeminarHashEmbedder())

    stored: list[KnowledgeDocument] = []
    for entry in pin.documents:
        document = await knowledge.get_document(document_id(entry.sha256))
        assert document is not None
        stored.append(document)
        assert document.title == entry.title
        assert document.source_url == entry.source_url
        assert document.license == entry.license
        assert document.kind == entry.kind
        assert document.published_on == entry.published_on
        assert document.department == entry.department
    assert len(stored) == len(pin.documents)


async def test_seeding_twice_answers_a_question_the_same_way() -> None:
    """The seeding order is the pin's and the vectors are the embedder's, so two
    seedings of the same pin rank the same fragments in the same order. A
    `uuid4()` document id or an unordered seeding would break this, and with it
    any comparison between two runs."""
    pin = pin_corpus()
    embedder = SeminarHashEmbedder()
    (vector,) = await embedder.embed([ENSO_QUESTION])

    first = await _ranked(await seed_knowledge_repository(pin, embedder=embedder), vector)
    second = await _ranked(await seed_knowledge_repository(pin, embedder=embedder), vector)

    assert first == second
    assert first, "the seeded corpus ranked nothing for a question about the ENSO state"


async def _ranked(
    knowledge: FakeKnowledgeRepository, vector: list[float]
) -> list[tuple[str, float]]:
    """The corpus's ranking for `vector`, floor lifted so the order is visible."""
    found = await knowledge.search_by_embedding(vector, top_k=RETRIEVAL_TOP_K, similarity_floor=0.0)
    return [(fragment.content, fragment.similarity) for fragment in found]


async def test_a_question_about_the_september_enso_state_ranks_the_cordoba_bulletin_first() -> None:
    """Córdoba declares `El Niño` for September 2026, the national bulletin
    declares `Neutral` and the guide declares neither, so a question about the
    September ENSO state has to rank Córdoba first: it is the one document that
    can answer it. `El Niño` has to be in the fragment that ranked first, because
    that is the sentence the answer would rest on.

    The floor is lifted **for the ranking alone**, and that is measured, not
    assumed: the stand-in embedder is a hashed bag of words over a fragment's
    first 48 words, and its scores for a short question against a 300-word
    fragment sit below ADR-0008:12's 0.75 floor — see
    `test_the_stand_in_reaches_no_fragment_above_the_floor`. What is being checked
    here is that the seeding and the embedding rank the right document first;
    what the floor does with that ranking is `retrieve_fragments`'s business and
    `tests/assistant/eval/test_replay.py` exercises it.
    """
    pin = pin_corpus()
    knowledge = await seed_knowledge_repository(pin, embedder=SeminarHashEmbedder())
    (vector,) = await SeminarHashEmbedder().embed([ENSO_QUESTION])

    ranked = await _ranked(knowledge, vector)

    assert ranked, "the seeded corpus ranked nothing for a question about the ENSO state"
    # The fragment that ranked first carries the sentence the answer would rest on.
    assert "El Niño" in ranked[0][0]
    assert await _document_of(pin, ranked[0][0]) == _cordoba_sha256(pin)


async def _document_of(pin: CorpusPin, content: str) -> str | None:
    """The digest of the pinned document holding `content`, or `None`."""
    for entry in pin.documents:
        if any(chunk.text == content for chunk in chunk_text(_text_of(pin, entry))):
            return entry.sha256
    return None


async def test_a_question_with_no_word_overlap_returns_nothing_above_the_floor() -> None:
    """The "insufficient evidence" branch: a question sharing no vocabulary with
    the corpus is not evidence, and the floor must exclude it rather than let a
    weak match through as if it were one."""
    pin = pin_corpus()
    knowledge = await seed_knowledge_repository(pin, embedder=SeminarHashEmbedder())
    (vector,) = await SeminarHashEmbedder().embed([UNRELATED_QUESTION])

    assert (
        await knowledge.search_by_embedding(
            vector, top_k=RETRIEVAL_TOP_K, similarity_floor=SIMILARITY_FLOOR
        )
        == []
    )


async def test_the_stand_in_reaches_no_fragment_above_the_floor() -> None:
    """The boundary of the stand-in embedder, measured rather than assumed.

    `SeminarHashEmbedder` is a hashed bag of words over a fragment's **first 48
    words**, and every fixture document opens with the same 40-word fixture
    boilerplate. A short producer question therefore shares the boilerplate, not
    the agronomy, and its best cosine against the whole corpus sits under
    ADR-0008:12's 0.75 floor. That is the honest answer for this corpus and this
    embedder, and it is the reason the graded run needs the ADR-0026 model rather
    than the seminar stand-in: a replay that retrieved nothing would hand the
    agronomist no fragments to grade.

    Recorded here so nobody "fixes" it by tuning a stand-in until the questions
    somebody wants retrieved retrieve. That would decide the evaluation's
    outcome, and the outcome is the owner's to decide, not the harness's.
    """
    pin = pin_corpus()
    knowledge = await seed_knowledge_repository(pin, embedder=SeminarHashEmbedder())
    embedder = SeminarHashEmbedder()

    best = 0.0
    for text in (ENSO_QUESTION, UNRELATED_QUESTION, "¿Qué lámina debo aplicar al maíz?"):
        (vector,) = await embedder.embed([text])
        for fragment in await knowledge.search_by_embedding(
            vector, top_k=RETRIEVAL_TOP_K, similarity_floor=-1.0
        ):
            best = max(best, fragment.similarity)

    assert best < SIMILARITY_FLOOR


async def test_a_pin_whose_bytes_are_gone_is_refused_rather_than_replayed() -> None:
    """Seeding over a directory whose bytes moved is a different corpus, and a
    replay that silently replayed it would name a digest it did not use."""
    moved = CorpusPin(
        directory=FIXTURE_CORPUS_DIR,
        documents=(
            CorpusPinEntry(
                title="Un documento que ya no está.",
                source_url="https://example.invalid/ausente",
                license="CC-BY-4.0 (test fixture)",
                kind="guide",
                published_on=date(2026, 9, 15),
                department=None,
                sha256="0" * 64,
                chunk_count=3,
            ),
        ),
    )

    with pytest.raises(CorpusPinError):
        await seed_knowledge_repository(moved, embedder=SeminarHashEmbedder())


def _text_of(pin: CorpusPin, entry: CorpusPinEntry) -> str:
    """The text of a pinned entry, re-read from the directory the pin names."""
    source = next(s for s in load_corpus(pin.directory) if s.sha256 == entry.sha256)
    return source.text
