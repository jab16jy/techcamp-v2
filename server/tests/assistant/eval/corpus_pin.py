"""The corpus the evaluation replays, pinned by digest (E12 T8; ADR-0008:12;
ADR-0008:32).

A replay a human agronomist is asked to grade is only worth grading if it says
**which bytes** it ran over. This module records that — one entry per document,
digest of the file on disk, fragment count from the same `chunk_text` ingestion
uses — and turns the record into the in-memory repository the replay reads.

Two decisions, both made once here:

* **The chunking is not repeated.** `pin_corpus` and `seed_knowledge_repository`
  both go through `kb_ingest.load_corpus` and `kb_ingest.chunk_text`, so the
  pin's `chunk_count` and the repository's fragments are what ingestion would
  have stored rather than a second splitter that agrees today.
* **The corpus directory is not resolved twice.** `kb_ingest.corpus_dir()` owns
  the `TECHCAMP_KB_CORPUS_DIR` switch — the owner turns it once the licence
  review is signed (ADR-0008:32) — and `pin_corpus` reuses it verbatim. A second
  copy of that resolution here would be a second place to change it, and the two
  would drift.

**Document ids are derived, never random.** `uuid5` over the document's digest
in a fixed namespace gives the same document the same id on every workstation
and every run, which is what makes two artifacts comparable. A `uuid4()` would
make every replay a corpus nobody has seen before — the same reason
`kb_ingest.CorpusDocument` refuses to carry one.

**Nothing here grades.** The pin holds no expectation about a fragment: what the
assistant would answer is the provider's (out of scope, U2) and what the answer
was worth is the agronomist's. This module hands over evidence.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from assistant.doubles import FakeKnowledgeRepository
from techcamp.assistant.adapters.kb_ingest import (
    CorpusSource,
    chunk_text,
    corpus_dir,
    load_corpus,
)
from techcamp.assistant.application.ports import Embedder, RetrievedFragment

DOCUMENT_ID_NAMESPACE: UUID = uuid5(NAMESPACE_URL, "techcamp/assistant/eval/corpus-document")
"""The fixed namespace `document_id` derives document ids in.

A namespace of its own rather than `NAMESPACE_URL` used directly, so the digest
of a document here can never collide with an id anything else derived from the
same URL namespace. Fixed: a namespace that changed between runs would change
every document id, and with it every comparison between two artifacts."""

FRAGMENT_ID_NAMESPACE: UUID = uuid5(NAMESPACE_URL, "techcamp/assistant/eval/corpus-fragment")
"""The fixed namespace `fragment_id` derives a fragment's row id in.

Separate from `DOCUMENT_ID_NAMESPACE` because a fragment id and a document id
answer different questions, and a shared namespace would let one document's
digest be another's chunk."""


def fragment_id(document_id: UUID, content: str) -> UUID:
    """The id of the fragment of `document_id` whose text is `content`.

    The stand-in for the id `kb_chunk` would hold. The in-memory double mints a
    fresh `uuid4()` on every search, which is right for a test that only cares
    that *a* fragment came back and wrong for a replay: a stored chunk has one
    row, and two artifacts of the same corpus have to be able to name the same
    fragment. Deriving it the same way the document id is derived keeps the whole
    replay reproducible — `replay_question` twice gives an equal
    `QuestionReplay`, `RetrievedFragment.id` included.

    Not `uuid4()` for the same reason the document id is not: a random id would
    make every artifact name a corpus nobody has seen before.
    """
    return uuid5(FRAGMENT_ID_NAMESPACE, f"{document_id}:{content}")


class CorpusPinError(RuntimeError):
    """A corpus directory that no longer holds the bytes the pin names.

    Raised by `seed_knowledge_repository` when a digest the pin records is not
    among the files in the pinned directory: the corpus moved between pinning
    and seeding, and a replay that quietly replayed the new bytes would name a
    digest it did not use. A `RuntimeError` for the same reason
    `kb_ingest.CorpusError` is one — nothing in `assistant.domain` decides what
    a corpus directory may hold."""


@dataclass(frozen=True, slots=True)
class CorpusPinEntry:
    """What one replayed document was: the metadata a citation is built from, its
    digest, and how many fragments the chunker made of it.

    The first six fields are `kb_ingest.CorpusDocument`'s, minus `enso_state`:
    the evaluation replays retrieval, and retrieval neither reads nor cites the
    ENSO state. The two that are not — `sha256` and `chunk_count` — are what make
    the entry a *pin* rather than a description: the first says which bytes, the
    second says how many fragments those bytes became.
    """

    title: str
    source_url: str
    license: str
    kind: str
    published_on: date
    department: str | None
    sha256: str
    chunk_count: int


@dataclass(frozen=True, slots=True)
class CorpusPin:
    """Which corpus directory a replay ran over, and one entry per document of it,
    in the manifest's order.

    `directory` is the directory the digests were read from, so a reader can
    re-read the same bytes; `documents` is in `load_corpus`'s order, which is the
    order the seeding below follows and the order the report renders.
    """

    directory: Path
    documents: tuple[CorpusPinEntry, ...]


def document_id(sha256: str) -> UUID:
    """The id of the document whose bytes hash to `sha256`.

    Deterministic and digest-derived: the same document is the same id in every
    run, on every workstation, which is what lets two artifacts name the same
    document. `uuid5` rather than `uuid4` because a random id would make each
    replay a corpus nobody has seen before.
    """
    return uuid5(DOCUMENT_ID_NAMESPACE, sha256)


def pin_corpus(directory: Path | None = None) -> CorpusPin:
    """Pin the corpus in `directory`, or of `kb_ingest.corpus_dir()`.

    `None` is the packaged fixture corpus unless `TECHCAMP_KB_CORPUS_DIR` names
    another — `corpus_dir()`'s own resolution, reused and not copied. An explicit
    `directory` is honoured as given, the way `load_corpus` honours it, so a test
    can pin a corpus of its own.

    The digest is the file's, and `chunk_count` comes from `chunk_text`, so the
    pin and the store cannot disagree about how many fragments a document holds.
    """
    root = corpus_dir() if directory is None else Path(directory)
    return CorpusPin(
        directory=root,
        documents=tuple(_entry(source) for source in load_corpus(root)),
    )


def _entry(source: CorpusSource) -> CorpusPinEntry:
    return CorpusPinEntry(
        title=source.document.title,
        source_url=source.document.source_url,
        license=source.document.license,
        kind=source.document.kind,
        published_on=source.document.published_on,
        department=source.document.department,
        sha256=source.sha256,
        chunk_count=len(chunk_text(source.text)),
    )


async def seed_knowledge_repository(
    pin: CorpusPin, *, embedder: Embedder
) -> SeededKnowledgeRepository:
    """The pinned corpus as a `FakeKnowledgeRepository`, in the pin's order.

    Every document is added with `add_document` under `document_id(sha256)`, and
    every fragment of it with `add_fragment`, carrying the embedder's own vector
    — the same vector ingestion would have stored, from the same embedder the
    question is embedded with. The order is the pin's, which is the manifest's,
    so the same corpus always seeds the same way.

    The repository is a `SeededKnowledgeRepository` so a fragment's row id is
    derived rather than random: see `SeededKnowledgeRepository` for why a replay
    needs that. It is still a `FakeKnowledgeRepository` — nothing about the
    ranking, the floor or `top_k` is overridden — and every rule the double holds
    (the cosine is the real one, `top_k` cuts, the floor filters) is kept.

    The directory is re-read from the pin rather than taken from the pin's
    metadata, and a digest the directory no longer holds is `CorpusPinError`:
    the pin says which bytes were replayed, and a directory whose bytes moved
    would be a different corpus under the same name.
    """
    sources = {source.sha256: source for source in load_corpus(pin.directory)}
    knowledge = SeededKnowledgeRepository()

    for entry in pin.documents:
        source = sources.get(entry.sha256)
        if source is None:
            raise CorpusPinError(
                f"eval corpus pin: {pin.directory} no longer holds the document pinned as "
                f"{entry.title!r} (sha256 {entry.sha256}); the corpus moved between pinning "
                "and seeding, so this pin cannot be replayed"
            )
        await _seed_one(knowledge, source=source, entry=entry, embedder=embedder)

    return knowledge


async def _seed_one(
    knowledge: SeededKnowledgeRepository,
    *,
    source: CorpusSource,
    entry: CorpusPinEntry,
    embedder: Embedder,
) -> None:
    """One document and its fragments, added in a deterministic order.

    The fragment ids are `fragment_id`'s: the store's own, derived from the
    document and the fragment's text, so the same corpus always names the same
    chunk. What the double mints instead is a fresh `uuid4()` per search, which
    would make every artifact different from the one before it.
    """
    identifier = document_id(entry.sha256)
    knowledge.add_document(
        document_id=identifier,
        title=entry.title,
        kind=entry.kind,
        source_url=entry.source_url,
        license=entry.license,
        published_on=entry.published_on,
        department=entry.department,
        # `CorpusDocument.enso_state` is parsed from a bulletin's prose and stored
        # with it; it is replayed as stored rather than as a pin entry, because
        # retrieval never reads it.
        enso_state=source.document.enso_state,
    )
    chunks = chunk_text(source.text)
    vectors = await embedder.embed([chunk.text for chunk in chunks])
    for chunk, vector in zip(chunks, vectors, strict=True):
        knowledge.add_fragment(document_id=identifier, content=chunk.text, embedding=vector)


def fragment_count(pin: CorpusPin) -> int:
    """How many fragments the pinned corpus holds in total.

    A reader of the artifact, not the harness: it says how much evidence the
    corpus has before a question is asked, and it is a count of the pin rather
    than of what any question retrieved.
    """
    return sum(entry.chunk_count for entry in pin.documents)


class SeededKnowledgeRepository(FakeKnowledgeRepository):
    """The in-memory double, with fragment ids that come from the seed.

    `FakeKnowledgeRepository.search_by_embedding` mints a fresh `uuid4()` for
    every `RetrievedFragment` it returns. That is right for a test that only
    cares that *a* fragment came back, and wrong for a replay: a `kb_chunk` row
    has one id, and an artifact that names a different one on every run cannot be
    compared with the artifact before it. The real `SqlAlchemyKnowledgeRepository`
    returns `kb_chunk.id` — stable, because the row is.

    So this overrides **the id and nothing else**. The ranking, the cosine, the
    floor and `top_k` are the double's, which is the port the replay runs
    against: overriding any of those would make the replay grade a repository
    that is not the one the product uses.

    A subclass and not a copy, so every rule the double holds stays the double's
    rule and a change to `doubles.py` is a change here too.
    """

    async def search_by_embedding(
        self, embedding: Sequence[float], *, top_k: int, similarity_floor: float
    ) -> list[RetrievedFragment]:
        found = await super().search_by_embedding(
            embedding, top_k=top_k, similarity_floor=similarity_floor
        )
        return [
            replace(fragment, id=fragment_id(fragment.document_id, fragment.content))
            for fragment in found
        ]
