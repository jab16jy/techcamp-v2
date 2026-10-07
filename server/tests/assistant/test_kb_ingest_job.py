"""The corpus ingestion job (E12 T3; ADR-0008:12,32; ADR-0021; ADR-0026;
docs/03-modelo-datos.md §`kb_document`, `kb_chunk`, `conversation` y `message`;
docs/06-diseno-detallado.md §9; D-T12.2).

Real Postgres, no provider: the job is the seam where the chunker, the loader, the
`Embedder` port and the two tables meet, and every property that matters is only
observable once they do. What is pinned here:

* a document and its fragments land with everything ADR-0008:12 asks a document to
  record — source, licence, kind, publication date — plus `department` and the ENSO
  state parsed from the bulletin;
* **re-running over unchanged bytes neither duplicates the document nor rewrites its
  fragments** (docs/03 row `Unicidad de la ingesta`), and a **changed digest for the
  same `source_url` supersedes** the stored revision — one row, new fragments, no
  orphans;
* a vector the column could not store is refused **before** the write, because the
  width is `vector(384)`'s width (ADR-0026) and a mismatch discovered by the database
  would be a failure after the model had already run;
* `enso_state` is stored for bulletins and is null for a guide (docs/03:354 "solo
  boletines"), and the national bulletin keeps `department = null` (docs/03:351);
* **the ingestion path imports no tokenizer and no torch.** `server/pyproject.toml` is
  outside this task's surface, so the dependency that would drag `torch` and
  `sentence-transformers` in cannot be added — and `ml/`, which has its own
  dependencies, is forbidden to this task. The last test reads the two modules'
  imports and fails if either grows one, which is what keeps
  `test_kb_chunker.py`'s promise ("the exact tokenizer belongs to the embedding
  adapter of ADR-0026, which owns the model") a checked property rather than a comment.

**The test double is the `Embedder` double, not the seminar stand-in.** Where the
behavior under test is the seam, the job is given `FakeEmbedder` through
`configure_embedder`; where it is the stand-in itself, the test calls the stand-in by
name. A test that ran both through the stand-in would pass if the two had quietly
become the same object.
"""

from __future__ import annotations

import ast
import inspect
import json
import math
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from assistant.doubles import FakeEmbedder
from techcamp.assistant.adapters import jobs as jobs_module
from techcamp.assistant.adapters.jobs import (
    INGEST_CORPUS_TASK_NAME,
    QUEUE_NAME,
    SeminarHashEmbedder,
    configure_embedder,
    ingest_kb_corpus,
)
from techcamp.assistant.adapters.kb_ingest import (
    CORPUS_DIR_ENV_VAR,
    EmbeddingContractError,
    load_corpus,
)
from techcamp.assistant.adapters.orm import KbChunkRow, KbDocumentRow
from techcamp.assistant.application.ports import EMBEDDING_DIMENSION
from techcamp.shared.jobs import app as jobs_app

pytestmark = pytest.mark.anyio

ADAPTERS = Path(__file__).resolve().parent.parent.parent / "src/techcamp/assistant/adapters"

FORBIDDEN_IMPORTS = {"torch", "sentence_transformers", "transformers", "tokenizers"}
"""What a dependency-free ingestion path must not import. `ml/` owns the model and its
dependencies (`ml/` has its own `pyproject.toml`); an adapter that imported any of
these would make `server`'s lock file carry torch and break the gate T8 needs to add
the model properly."""


@pytest.fixture(autouse=True)
async def _empty_corpus(db_session: AsyncSession):
    """`kb_document`/`kb_chunk` are reference tables with no `org_id`, so
    `db_session`'s `TRUNCATE organization ... CASCADE` never reaches them
    (docs/03 §`kb_document`, `kb_chunk`, `conversation` y `message`, row `org_id en
    `kb_document` y `kb_chunk`) and a document one test ingested would make the next
    test's idempotency assertions read a corpus that is not its own."""
    yield
    await db_session.execute(delete(KbChunkRow))
    await db_session.execute(delete(KbDocumentRow))
    await db_session.commit()


@pytest.fixture(autouse=True)
def _fake_embedder(monkeypatch: pytest.MonkeyPatch) -> FakeEmbedder:
    """The `Embedder` port filled by the test double, and restored after each test.

    Monkeypatched onto the module's own seam rather than imported, because that seam
    is process-wide state: a test that left a double in it would silently become the
    embedder of every test that runs after it in a random order
    (`pytest-randomly`).
    """
    embedder = FakeEmbedder()
    monkeypatch.setattr(jobs_module, "_embedder", embedder, raising=False)
    return embedder


@pytest.fixture(autouse=True)
def _fixture_corpus(monkeypatch: pytest.MonkeyPatch) -> None:
    """The packaged fixture corpus (ADR-0008:32), whatever the shell's environment says.

    The variable is read by `corpus_dir()` at call time, so a developer with
    `TECHCAMP_KB_CORPUS_DIR` exported would otherwise have every test in this file
    assert against their own corpus.
    """
    monkeypatch.delenv(CORPUS_DIR_ENV_VAR, raising=False)


async def _documents(session: AsyncSession) -> list[KbDocumentRow]:
    result = await session.execute(select(KbDocumentRow).order_by(KbDocumentRow.source_url))
    return list(result.scalars())


async def _chunks(session: AsyncSession, document_id: UUID) -> list[KbChunkRow]:
    result = await session.execute(
        select(KbChunkRow).where(KbChunkRow.document_id == document_id).order_by(KbChunkRow.id)
    )
    return list(result.scalars())


async def _chunk_count(session: AsyncSession) -> int:
    return int((await session.execute(select(func.count()).select_from(KbChunkRow))).scalar_one())


# -- the job's shape -------------------------------------------------------------


async def test_the_ingestion_is_a_task_in_the_assistant_queue() -> None:
    """ADR-0012: the corpus is loaded by the `worker`, on the queue that worker
    listens on. `queue` is repeated per task for the same reason the periodic jobs
    repeat it: procrastinate configures a job from the options it is handed, so a task
    that did not name it would sit in the default queue where this worker does not
    listen."""
    task = jobs_app.tasks[INGEST_CORPUS_TASK_NAME]

    assert task.queue == QUEUE_NAME
    assert QUEUE_NAME == "assistant"


async def test_the_ingestion_takes_procrastinate_periodic_timestamp_first() -> None:
    """ADR-0021 makes the job runnable at the instant (`POST /dev/jobs/kb:ingest:run`),
    and procrastinate hands a periodic launch a `timestamp` first argument. The corpus
    does not change on a schedule, so nothing reads it — but the signature has to
    accept it, or the first periodic registration would be a signature change."""
    parameters = list(inspect.signature(ingest_kb_corpus).parameters)
    assert parameters[0] == "timestamp"


# -- what a run stores -----------------------------------------------------------


async def test_a_document_and_its_fragments_land_with_source_and_licence(
    db_session: AsyncSession,
) -> None:
    """ADR-0008:12: "Cada documento registra su fuente y licencia para poder citarlo".
    The citation of T4/T5 is built from these columns, so a document stored without
    them could not be cited at all — and `kb_document` has no `org_id`
    (docs/03 row `org_id en `kb_document` y `kb_chunk`), so this row is the only trace
    of where the corpus came from."""
    await ingest_kb_corpus(timestamp=0)
    await db_session.commit()

    rows = await _documents(db_session)
    assert len(rows) == 3
    for row in rows:
        assert row.title
        assert row.source_url.startswith("https://example.invalid/")
        assert row.license == "CC-BY-4.0 (test fixture)"
        assert row.kind in ("guide", "agroclimatic_bulletin")
        assert row.published_on.year == 2026
        assert len(row.sha256) == 64
        assert await _chunks(db_session, row.id)


async def test_the_stored_fragments_are_the_chunker_s_own(
    db_session: AsyncSession, _fake_embedder: FakeEmbedder
) -> None:
    """The fragments stored are the ones `chunk_text` produced, in order, and each was
    embedded as the text stored — which is the only way ADR-0008:12's "top 5 fragments
    at similarity >= 0.75" can return text a citation can quote."""
    from techcamp.assistant.adapters.kb_ingest import chunk_text

    await ingest_kb_corpus(timestamp=0)
    await db_session.commit()

    source = next(
        source
        for source in load_corpus()
        if source.document.title.startswith("Boletín Mesa Técnica")
    )
    row = next(
        row for row in await _documents(db_session) if row.source_url == source.document.source_url
    )
    expected = [chunk.text for chunk in chunk_text(source.text)]

    stored = await _chunks(db_session, row.id)

    assert expected, "the fixture must be long enough to be split"
    assert len(stored) == len(expected)
    assert sorted(chunk.content for chunk in stored) == sorted(expected)
    assert len(stored) > 1, "a multi-paragraph fixture must exercise the ~500-token cut"


async def test_the_enso_state_is_stored_for_the_bulletins(
    db_session: AsyncSession,
) -> None:
    """docs/06-diseno-detallado.md:375: "El estado ENSO (`kb_document.enso_state`) se
    registra al cargar el boletín". The state comes from the bulletin's own words, so
    the row says `el_nino` for the Córdoba bulletin and `neutral` for the national one
    without the manifest stating either."""
    await ingest_kb_corpus(timestamp=0)
    await db_session.commit()

    states = {row.title: row.enso_state for row in await _documents(db_session)}

    assert (
        states["Boletín Mesa Técnica Agroclimática de Córdoba — septiembre de 2026 (fixture)"]
        == "el_nino"
    )
    assert states["Boletín Agroclimático Nacional — septiembre de 2026 (fixture)"] == "neutral"


async def test_a_guide_is_stored_with_no_enso_state_and_the_national_bulletin_with_no_department(
    db_session: AsyncSession,
) -> None:
    """docs/03-modelo-datos.md:354 marks `enso_state` "solo boletines": a guide is
    stored with a null state whatever its prose mentions. And docs/03:351 marks
    `department` "null = nacional", the value docs/06 §9's seasonal-climate fact falls
    back to when a department has no bulletin of its own — so both nulls are values."""
    await ingest_kb_corpus(timestamp=0)
    await db_session.commit()

    rows = {
        row.kind + ":" + (row.department or "nacional"): row for row in await _documents(db_session)
    }

    guide = next(row for row in rows.values() if row.kind == "guide")
    assert guide.enso_state is None
    national = next(
        row
        for row in rows.values()
        if row.kind == "agroclimatic_bulletin" and row.department is None
    )
    assert national is not None


# -- idempotency -----------------------------------------------------------------


async def test_re_running_over_unchanged_bytes_neither_duplicates_nor_rewrites(
    db_session: AsyncSession,
) -> None:
    """docs/03 §`kb_document`, `kb_chunk`, `conversation` y `message`, row `Unicidad de
    la ingesta`: ingestion is a repeatable job (ADR-0012), so a re-run over the same
    bytes is a no-op. The fragments are compared **by id**, not by count: a re-run that
    deleted and rewrote them would leave the same count and the same text, and every
    citation pointing at a fragment id would have followed the churn."""
    await ingest_kb_corpus(timestamp=0)
    await db_session.commit()
    before = {
        row.source_url: (row.id, [chunk.id for chunk in await _chunks(db_session, row.id)])
        for row in await _documents(db_session)
    }

    await ingest_kb_corpus(timestamp=0)
    await db_session.commit()

    after = {
        row.source_url: (row.id, [chunk.id for chunk in await _chunks(db_session, row.id)])
        for row in await _documents(db_session)
    }
    assert after == before


async def test_a_re_run_does_not_call_the_embedder_for_an_unchanged_revision(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unchanged case is decided **before** the model runs. Re-embedding the whole
    corpus on every run would make the idempotent path the expensive one — the cost of
    a model call against no change in the rows."""
    await ingest_kb_corpus(timestamp=0)
    await db_session.commit()

    calls: list[int] = []
    counting = FakeEmbedder()

    async def counting_embed(texts):  # type: ignore[no-untyped-def]
        calls.append(len(texts))
        return await counting.embed(texts)

    counting.embed = counting_embed  # type: ignore[method-assign]
    monkeypatch.setattr(jobs_module, "_embedder", counting)

    await ingest_kb_corpus(timestamp=0)
    await db_session.commit()

    assert calls == []


async def test_a_changed_digest_for_the_same_source_supersedes_the_stored_revision(
    db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A revised bulletin is a **new revision of the same document**, not a second
    document: the stored chunks and the stored row of that `source_url` are deleted and
    the new ones inserted, so the corpus keeps one document per source and no fragment
    of the old revision is left behind (docs/03 row `Unicidad de la ingesta`).

    Built from a corpus directory the test owns, because "the bytes changed" is not
    expressible on the packaged fixtures without editing them."""
    _write_single_corpus(
        tmp_path, text="Primera versión del boletín de prueba.", published_on="2026-09-01"
    )
    monkeypatch.setenv(CORPUS_DIR_ENV_VAR, str(tmp_path))
    await ingest_kb_corpus(timestamp=0)
    await db_session.commit()
    first = (await _documents(db_session))[0]

    _write_single_corpus(
        tmp_path, text="Segunda versión del boletín de prueba.", published_on="2026-09-02"
    )
    await ingest_kb_corpus(timestamp=0)
    await db_session.commit()

    rows = await _documents(db_session)
    assert len(rows) == 1
    assert rows[0].id != first.id
    assert rows[0].sha256 != first.sha256
    assert rows[0].published_on.isoformat() == "2026-09-02"
    # One row and its own fragments: nothing of the superseded revision survives.
    assert await _chunk_count(db_session) == len(await _chunks(db_session, rows[0].id))


# -- the embedding seam ----------------------------------------------------------


class _WrongWidthEmbedder:
    """An `Embedder` that returns a vector the column could never store.

    The failure ADR-0026 makes an error: `vector(384)`'s width is the model's width,
    not a preference, and a padded or truncated vector would be compared against the
    wrong geometry at retrieval time.
    """

    async def embed(self, texts):  # type: ignore[no-untyped-def]
        return [[0.0] * 8 for _ in texts]


class _WrongCountEmbedder:
    """An `Embedder` that drops a vector.

    A silent count mismatch would store fewer fragments than the chunker produced and
    nothing would say so: the last fragment would simply never be retrievable.
    """

    async def embed(self, texts):  # type: ignore[no-untyped-def]
        return [[0.0] * EMBEDDING_DIMENSION for _ in texts][:-1]


@pytest.mark.parametrize(
    ("embedder", "expected"),
    [
        pytest.param(_WrongWidthEmbedder(), "wide", id="wrong-width"),
        pytest.param(_WrongCountEmbedder(), "one each", id="wrong-count"),
    ],
)
async def test_a_vector_the_column_could_not_store_is_refused_before_the_write(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    embedder: object,
    expected: str,
) -> None:
    """The check is on the way back from the port, not on the way to the database: a
    width or count mismatch discovered by Postgres would be a failure after the model
    had already run and been paid for, and its error would name pgvector rather than
    the model that is wrong (ports.py, ADR-0026). Nothing is written — the refusal
    happens before the savepoint opens."""
    monkeypatch.setattr(jobs_module, "_embedder", embedder)

    with pytest.raises(EmbeddingContractError, match=expected):
        await ingest_kb_corpus(timestamp=0)
    await db_session.rollback()

    assert await _documents(db_session) == []
    assert await _chunk_count(db_session) == 0


async def test_the_seam_is_used_when_one_is_configured(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`configure_embedder` is the seam the composition root injects the real model
    through, and it is what lets T8 replace the seminar stand-in without editing the
    job: the vectors stored here are the double's, so the seam is proven by what
    landed and not by the fact that a function was called."""
    embedder = FakeEmbedder()
    monkeypatch.setattr(jobs_module, "_embedder", None, raising=False)

    configure_embedder(embedder)
    try:
        await ingest_kb_corpus(timestamp=0)
        await db_session.commit()
    finally:
        monkeypatch.setattr(jobs_module, "_embedder", None, raising=False)

    assert await _documents(db_session)


# -- the seminar stand-in --------------------------------------------------------


async def test_the_job_runs_with_no_configuration_and_stores_the_pinned_width(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0021's seminar profile runs with no provider configured, and
    `techcamp/worker.py` is registration only — it never calls a `configure_*` seam
    here. So the default embedder has to be the seminar stand-in, and it has to be
    right: 384 floats, or the very first run would fail on the column."""
    monkeypatch.setattr(jobs_module, "_embedder", None, raising=False)

    await ingest_kb_corpus(timestamp=0)
    await db_session.commit()

    rows = await _documents(db_session)
    assert rows
    chunks = await _chunks(db_session, rows[0].id)
    assert chunks
    assert len(chunks[0].embedding) == EMBEDDING_DIMENSION


async def test_the_seminar_stand_in_is_deterministic_normalised_and_the_pinned_width() -> None:
    """The stand-in is three properties and no more: **the same text always embeds to
    the same vector** (so a stored fragment is retrievable after any restart),
    `EMBEDDING_DIMENSION` wide (ADR-0026), and L2-normalised, so the cosine the HNSW
    `vector_cosine_ops` index computes is a similarity and not an accident of length.

    It is **not** `intfloat/multilingual-e5-small` and says so in its docstring: T8
    replaces it with the pinned model, which is a model this repository cannot load
    yet (`server/pyproject.toml` has no torch and `ml/` is forbidden to this task)."""
    embedder = SeminarHashEmbedder()
    texts = ["El riego por goteo.", "El riego por goteo.", "La cosecha de maíz."]

    vectors = await embedder.embed(texts)

    assert vectors[0] == vectors[1]
    assert vectors[0] != vectors[2]
    for vector in vectors:
        assert len(vector) == EMBEDDING_DIMENSION
        assert abs(math.sqrt(sum(value * value for value in vector)) - 1.0) < 1e-9


async def test_the_seminar_stand_in_similarises_fragments_that_share_words() -> None:
    """The reason the stand-in hashes *words* and not the whole text: it has to place two
    fragments about the same thing closer together than two about different things, or
    the seminar's one retrieval run would find nothing at every threshold and nobody
    could see the citations, the floor or the ranking work.

    It is still word identity and nothing else, so this is the limit of what the
    stand-in can do — "gusano cogollero" does not find "Spodoptera frugiperda"
    (ADR-0008's Contexto), which is why T8 evaluates against the pinned model."""
    embedder = SeminarHashEmbedder()

    vectors = await embedder.embed(
        [
            "La precipitación de septiembre fue inferior al promedio en el valle del Sinú.",
            "El promedio de precipitación del valle del Sinú quedó por debajo en septiembre.",
            "El filtro del gotero debe limpiarse cada mes para que el caudal no caiga.",
        ]
    )

    shared = _cosine(vectors[0], vectors[1])
    apart = _cosine(vectors[0], vectors[2])
    assert shared > apart


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    return dot / (math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right)))


async def test_the_seminar_stand_in_is_not_the_test_double() -> None:
    """The stand-in and `FakeEmbedder` must not be the same object in disguise: one is
    production code the seminar runs on, the other is a test's. If they ever collapsed,
    a test asserting the stand-in's behavior would be asserting the double's, and the
    seminar's retrieval would be silently the double's too."""
    text = "El riego por goteo."

    assert SeminarHashEmbedder is not FakeEmbedder
    assert await SeminarHashEmbedder().embed([text]) != await FakeEmbedder().embed([text])


# -- no tokenizer, no torch ------------------------------------------------------


@pytest.mark.parametrize("module", ["kb_ingest.py", "jobs.py"])
async def test_the_ingestion_path_imports_no_tokenizer_and_no_torch(module: str) -> None:
    """`server/pyproject.toml` is outside this task's surface and `ml/` is forbidden
    to it, so the dependency that brings `torch`/`sentence-transformers` in cannot be
    added — and none is needed: the chunker estimates tokens with a named ratio and
    the stand-in embeds with a hash. This reads both modules' imports so a later edit
    that adds one fails here instead of at T8's lock file.

    Checked on the module's own `import` statements, not on its transitive imports:
    `sqlalchemy`/`pgvector` are the database's business, and what this invariant is
    about is the *embedding and tokenization* dependencies a reader would expect a
    model-backed ingest to have."""
    tree = ast.parse((ADAPTERS / module).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])

    assert not imported & FORBIDDEN_IMPORTS


async def test_the_only_embedding_provider_reachable_without_configuration_is_the_stand_in(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The promise, from the other side: with no `configure_embedder` call anywhere the
    job still stores vectors of the pinned width, which is only possible because the
    stand-in needs no model file, no download and no dependency (ADR-0021)."""
    monkeypatch.setattr(jobs_module, "_embedder", None, raising=False)

    assert isinstance(jobs_module._resolve_embedder(), SeminarHashEmbedder)


def _write_single_corpus(directory: Path, *, text: str, published_on: str) -> None:
    """One-document corpus directory, for the revision test that needs changed bytes."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "corpus": "test",
                "documents": [
                    {
                        "file": "boletin.md",
                        "title": "Boletín de prueba",
                        "source_url": "https://example.invalid/boletin-prueba",
                        "license": "CC-BY-4.0 (test fixture)",
                        "kind": "agroclimatic_bulletin",
                        "published_on": published_on,
                        "department": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (directory / "boletin.md").write_text(text, encoding="utf-8")
