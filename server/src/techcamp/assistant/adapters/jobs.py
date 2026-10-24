"""The corpus ingestion job (E12 T3; ADR-0008:12,32; ADR-0012; ADR-0021; ADR-0026;
docs/03-modelo-datos.md §`kb_document`, `kb_chunk`, `conversation` y `message`, row
`Unicidad de la ingesta`; docs/06-diseno-detallado.md §9; D-T12.2).

One task, not a fan-out: the corpus is a **directory of files**, so there is nothing
to enumerate beyond what `load_corpus` already read, and the run walks the documents
itself. `POST /dev/jobs/kb:ingest:run` answers one `job_id` for the same reason the
other seminar jobs do (ADR-0021:32 — "Programados **y** ejecutables al instante").
No `@app.periodic` here, deliberately: a bulletin does not appear on a schedule, so a
cron would re-read the same files every day to find nothing new. Idempotency
(docs/03 row `Unicidad de la ingesta`) is what makes an occasional re-run cheap — see
`kb_ingest.ingest_document`.

**The corpus is fixtures until the licence review is signed** (ADR-0008:32, "El
corpus de la v1 debe revisarse por licencia antes de migrarse"; the feature doc's
Open question 2). `TECHCAMP_KB_CORPUS_DIR` is what the owner turns, and nothing here
downloads anything: `source_url` is recorded, never opened.

**The embedder is a seam, and its default is the seminar stand-in.** ADR-0026 pins
`intfloat/multilingual-e5-small` (MIT, 384 dimensions, local), and this repository
cannot load it yet: `server/pyproject.toml` carries no torch and is outside this task's
surface, and `ml/` — which owns the model dependencies — is forbidden to it. So
`SeminarHashEmbedder` stands in, deterministically, dependency-free and offline, and
`configure_embedder` is the one-line seam T8 uses to put the real model behind the
same `Embedder` port without editing this job. **It is not e5-small and does not
pretend to be**: it is a token-hashing projection, so it knows nothing about
agronomic semantics and its similarity between two fragments is lexical overlap
rather than meaning. That is enough for the seminar to exercise retrieval end to end
(ADR-0021) and is **not** enough to validate the corpus: the retrieval evaluation is
T8's work, against the pinned model.

**Every failure here bubbles up to procrastinate, which retries** (`RetryStrategy`).
That is the honest shape for a run this small: a corpus of three files either stores
or it does not, and the two failures that can happen — a loader refusal about the
directory and a broken embedder contract — are configuration faults, not per-document
data. Rejected: a per-document `try`/`except` that logs and moves on, because the one
failure that a real corpus produces at scale (a model whose width is not 384's) would
then commit zero rows and log one warning per document, which is a successful run that
stored nothing — the failure that reads later as "retrieval finds nothing in the
corpus". Per-document *outcomes* are still logged one by one, and the one legitimate
per-document race (another worker stored this revision first) is handled inside
`kb_ingest`, where the constraint is read.
"""

from __future__ import annotations

import hashlib
import logging
import math
import unicodedata
from collections.abc import Sequence
from pathlib import Path

from procrastinate import RetryStrategy
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.assistant.adapters.kb_ingest import (
    CorpusSource,
    corpus_dir,
    ingest_document,
    load_corpus,
)
from techcamp.assistant.application.ports import EMBEDDING_DIMENSION, Embedder
from techcamp.shared.db import async_session_factory
from techcamp.shared.jobs import app

logger = logging.getLogger(__name__)

QUEUE_NAME = "assistant"
INGEST_CORPUS_TASK_NAME = "assistant.ingest_kb_corpus"

PROFILED_WORDS = 48
"""How many words of a fragment `SeminarHashEmbedder` reads. A stand-in's constant, so
it is a named number and not a literal in the loop: the seminar's retrieval is a
demonstration, not an evaluation (T8 evaluates against the pinned model), and reading a
500-token fragment in full would cost time without making the demonstration any more
honest."""

_PUNCTUATION = ".,;:!?()[]«»\"'“”‘’—–-…·"
"""Trimmed off a word before it is hashed. A fragment's words come with the punctuation
of the prose they were written in, and `"riego,"` hashing to its own dimension from
`"riego"` would make the overlap a measure of the author's commas."""

_EMPTY_VECTOR_COMPONENT = 1.0 / (EMBEDDING_DIMENSION**0.5)
"""The one component value of the no-direction vector `_normalised` returns: 384 copies
of `1/sqrt(384)` have length exactly 1, so even the degenerate vector is stored as a
point on the unit sphere and the cosine between two of them is 1."""

_embedder: Embedder | None = None
"""The injected `Embedder`, or `None` for the seminar stand-in.

A module seam and not a parameter because procrastinate 3.10 offers a task no
startup injection point (`App.task` takes no dependency callback), the same reason
`risk`'s jobs carry `configure_weather_cells`. **`None` is a valid state and not an
error**: unlike `risk`'s cell reader, this job has a default that works, because
ADR-0021's seminar profile must ingest the corpus with nothing configured at all, and
`techcamp/worker.py` registers tasks without configuring them.
"""


class SeminarHashEmbedder:
    """The seminar stand-in for ADR-0026's pinned model, and **not** the model.

    `intfloat/multilingual-e5-small` is what the corpus will be embedded with (MIT,
    384 dimensions, run locally so the corpus never leaves the server — ADR-0026,
    ADR-0007:39). It is not loadable here: `server/pyproject.toml` has no torch and is
    outside this task's edit surface, and `ml/`, which owns the model's dependencies, is
    forbidden to this task. Rejected: adding torch to `server/` for one ingestion step,
    which would make every `api`, `worker` and `ingestor` process carry a ~2 GB
    dependency for something only the worker uses. Rejected: computing a bag-of-words
    vector *inside* `kb_ingest`, which would put the embedding behind no port and make
    T8's real adapter a rewrite instead of an injection.

    So this is what ADR-0021 prescribes for a profile with no provider: **recorded
    behaviour instead of a live call**, here a deterministic projection. Same text,
    same vector, forever; different text, a different vector; `EMBEDDING_DIMENSION`
    floats; L2-normalised, so the cosine `vector_cosine_ops` computes is a real
    similarity rather than an artefact of length.

    **What it is not:** a semantic embedding. It is a hashed bag of words, so two
    fragments score by shared words — a bulletin that repeats "precipitación" looks
    close to another that does, and "gusano cogollero" will not find "Spodoptera
    frugiperda", which is the exact failure ADR-0008's move away from TF-IDF was made
    to fix. The seminar can exercise retrieval, the floor and the citations against it;
    only T8's evaluation against the pinned model says anything about whether the
    corpus retrieves well.
    """

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [_embed_one(text) for text in texts]


def _embed_one(text: str) -> list[float]:
    """One fragment as `EMBEDDING_DIMENSION` L2-normalised floats.

    A **hashed bag of words** — the hashing trick, as `HashingVectorizer` does it:
    every word is hashed into a dimension and a sign and the words are summed. That
    is what makes the stand-in worth having at all, because fragments that share words
    land closer together and the seminar's retrieval exercises the ranking, the
    similarity floor and the citations instead of returning noise. Rejected: a digest
    of the whole text, the other deterministic option — it is uncorrelated, so every
    pair of fragments would score near zero and every query would miss the 0.75 floor
    of ADR-0008:12, and the seminar would learn nothing from its one retrieval run.
    """
    buckets = [0.0] * EMBEDDING_DIMENSION
    for word in _words(text):
        digest = hashlib.blake2b(word.encode("utf-8"), digest_size=8).digest()
        position = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSION
        sign = 1.0 if digest[4] & 1 else -1.0
        buckets[position] += sign
    return _normalised(buckets)


def _words(text: str) -> list[str]:
    """The fragment's words, case-folded, without accents, cut at `PROFILED_WORDS`.

    Folding is what makes the overlap worth anything: a fragment and a question about
    it are written with different capital letters and different accents, and "Riego",
    "riego" and "riego" have to be one word.

    Truncated on purpose. A stand-in's cost is linear in the words it reads, a fragment
    is ~500 tokens (ADR-0008:12) and its first few dozen words carry the subject, so
    reading all of it would cost time without making the seminar's retrieval any more
    representative of what the pinned model does.
    """
    decomposed = unicodedata.normalize("NFD", text.casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    words = [
        word.strip(_PUNCTUATION) for word in plain.split() if any(char.isalnum() for char in word)
    ]
    return words[:PROFILED_WORDS]


def _normalised(vector: list[float]) -> list[float]:
    """`vector` scaled to length 1, so cosine is a similarity (ADR-0008:12).

    A vector with no direction — a fragment with no readable word — cannot be scaled,
    and gets the all-ones projection instead of a division by zero: the column still
    stores 384 floats, and such a fragment really is indistinguishable from every
    other one.
    """
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return [_EMPTY_VECTOR_COMPONENT] * EMBEDDING_DIMENSION
    return [value / norm for value in vector]


def configure_embedder(embedder: Embedder) -> None:
    """Put `embedder` behind the `Embedder` port for every run of this process.

    **Optional**, unlike `risk`'s `configure_weather_cells`: the seminar stand-in is a
    working default (ADR-0021), so a worker that never calls this still ingests a
    corpus. T8 calls it with the ADR-0026 model — or, better, the composition root
    does, once, exactly as `risk`'s factories are injected from `techcamp/worker.py`
    (docs/05-arquitectura.md §Solo la fachada pública)."""
    global _embedder
    _embedder = embedder


def _resolve_embedder() -> Embedder:
    """The injected embedder, or the seminar stand-in. Never `None`."""
    return _embedder if _embedder is not None else SeminarHashEmbedder()


@app.task(
    name=INGEST_CORPUS_TASK_NAME,
    queue=QUEUE_NAME,
    # A rerun is free on the rows (docs/03 row `Unicidad de la ingesta`), so the retry
    # only covers a database hiccup mid-run, which would otherwise leave the corpus
    # half-ingested until the next manual trigger. Two attempts, like every other job.
    retry=RetryStrategy(max_attempts=2, linear_wait=30),
)
async def ingest_kb_corpus(timestamp: int, directory: str | None = None) -> None:
    """Load the corpus directory and store every document of it with its fragments.

    `timestamp` is procrastinate's periodic argument and is not read: the corpus is
    files on disk, not a date. `directory` is the seminar's way to point the run at a
    specific corpus (ADR-0021) and defaults to `corpus_dir()`, which is the packaged
    fixtures unless `TECHCAMP_KB_CORPUS_DIR` names another directory.

    **Each document commits on its own.** The chunking and the embedding are per
    document, so the transaction is too, and a document the embedder refuses is a
    warning this run continues past instead of an exception that takes the whole corpus
    down: the corpus is a directory of independent files, and one broken file is not
    evidence the rest is broken. What bubbles up to procrastinate is the loader's
    refusal — before any write, and about the directory as a whole.
    """
    root = Path(directory) if directory else corpus_dir()
    sources = load_corpus(root)
    logger.info("kb: ingesting %s document(s) from %s", len(sources), root)

    embedder = _resolve_embedder()
    async with async_session_factory() as session:
        for source in sources:
            await _ingest_one(session, source, embedder=embedder)


async def _ingest_one(session: AsyncSession, source: CorpusSource, *, embedder: Embedder) -> None:
    """One document, in its own transaction, with what happened written down.

    The per-document commit is what makes a partial run *describable*: a failure on
    document four leaves documents one to three stored, and the log says which. It is
    also the only place a document-level outcome is decided.

    **`EmbeddingContractError` is deliberately not caught here.** A wrong width is not
    one document's problem: ADR-0026 pins the width to the model, so every document of
    the corpus fails it identically, and a run that swallowed it would commit nothing
    and log ten thousand warnings while reporting success — the empty corpus that looks
    like a retrieval failure. It propagates to procrastinate, which retries, and the run
    fails loudly. The per-document recovery that *is* legitimate — another worker having
    stored this exact revision first — lives inside `ingest_document`, where the
    constraint is read and the rollback is scoped to the savepoint that made it.
    """
    async with session.begin_nested():
        outcome = await ingest_document(session, source, embedder=embedder)
    await session.commit()

    log = logger.info if outcome.stored else logger.debug
    log(
        "kb: %s %s (%s fragment(s))",
        outcome.reason,
        source.path.name,
        outcome.chunk_count,
    )
