"""Corpus ingestion for the assistant: the chunker, the local corpus loader and the
`kb_document`/`kb_chunk` write (E12 T3; ADR-0008; ADR-0012; docs/03-modelo-datos.md
§`kb_document`, `kb_chunk`, `conversation` y `message`; docs/06-diseno-detallado.md §9;
D-T12.2, D-T12.5).

**The fragments: ~500 tokens with overlap** (ADR-0008:12). `CHUNK_TARGET_TOKENS` is the
upper bound a single fragment may reach and `CHUNK_OVERLAP_TOKENS` the tail every
fragment after the first repeats, so a sentence that answers the question is not cut in
half between two fragments and retrieved as neither. The cut is placed at the last
boundary available — a paragraph boundary before a sentence boundary — and a sentence
longer than the target is cut at words, because a fragment of more than the target is an
embedding a model was never meant to read.

**The token count is an estimate, and it says so.** There is no tokenizer here, and no
tokenizer dependency could be added (`server/pyproject.toml` is outside this task's
surface and `ml/` is forbidden to it): `estimate_tokens` converts characters to tokens
with the single named ratio `CHARS_PER_TOKEN`. This *approximates* the real count of the
model that will embed these fragments — the exact tokenizer is a property of
`intfloat/multilingual-e5-small` (ADR-0026) and therefore belongs to **the embedding
adapter**, not to the splitter. What the estimate has to guarantee, and does, is the size
bound: no fragment is much over `CHUNK_TARGET_TOKENS`, which is the property both
retrieval and the embedding model care about. When the model adapter lands, its tokenizer
replaces this ratio and nothing else here changes.

Every document carries its `source_url` and its `license` (ADR-0008:12 "Cada documento
registra su fuente y licencia para poder citarlo"), its `kind`, its `published_on` and
its `department` — `department = null` **is** the national bulletin (docs/03:353), the
value docs/06 §9's seasonal-climate fact falls back to when a department has no bulletin
of its own. The ENSO state of a bulletin is *parsed from the bulletin's own text* and
stored on `kb_document.enso_state` (docs/06:374 "el estado ENSO se registra al cargar el
boletín"), in the vocabulary of docs/03:354: `neutral | el_nino | la_nina`.

**Idempotent by `(source_url, sha256)`** (docs/03 §`kb_document`, `kb_chunk`,
`conversation` y `message`, row `Unicidad de la ingesta`), which is ADR-0012's
*(tipo, entidad, fecha)* rule read as *(source, document, revision)*: ingestion is a
repeatable job, so a re-run over unchanged bytes must neither duplicate the document nor
rewrite its fragments, and a **changed** digest for the same `source_url` supersedes the
stored revision — its fragments and its document row are deleted before the new revision
is inserted, so the corpus keeps **one document per source**.

**Embeddings come from the `Embedder` port** (ADR-0008; ADR-0026 pins the model and its
384 dimensions). This module never embeds anything itself: it is handed an `Embedder`,
checks that what came back is one vector per fragment and of the pinned width — the width
is the column's width, so a mismatch has to be an error rather than a database failure
after the model already ran — and writes the vectors.

**D-T12.2 — index before load, decided here.** The cosine HNSW index on `kb_chunk` is
already in place before this job ever runs: T2's migration created it on an empty table
(docs/03 §Índices principales: `kb_chunk` → `HNSW(embedding vector_cosine_ops)`), so
ingestion inserts straight into it and **the job never runs DDL**. pgvector's own README
advises creating an HNSW index *after* a bulk load, and that advice is right at
bulk-build scale: `maintenance_work_mem` has to be raised before the build, and the graph
stops fitting in it — the `hnsw graph no longer fits into maintenance_work_mem` notice
appears around 100 000 tuples. This corpus is ~10 000 fragments over ~50 MB
(docs/02-estimaciones.md:90), an order of magnitude below that, and ingestion is a
**repeatable job that re-runs** (ADR-0012). A load-then-index job would drop and rebuild
the index on every revision of every bulletin — the very cost a rebuild is meant to avoid
— over an index that was already correct. So the index is built by the migration and left
alone; ADR-0008:12 and D-T12.2 own the column and its index, and this module owns
neither.

**The corpus is a directory, and the directory that ships is fixtures only**
(ADR-0008:32, "El corpus de la v1 debe revisarse por licencia antes de migrarse";
the feature doc's Open question 2). `FIXTURE_CORPUS_DIR` is the packaged corpus of
teaching documents, every one of them marked as a fixture in its own title and
carrying a `.invalid` source host (RFC 2606). `corpus_dir()` reads
`TECHCAMP_KB_CORPUS_DIR` and falls back to those fixtures, which is the switch the
owner turns once the licence review is signed — **here, in the adapter that uses
it**, and not in `shared/config.py`: it is one corpus, not a setting of the
product, and a documented environment variable of every process is a setting of
the product. `load_corpus()` therefore reads files and refuses the four shapes it
will not store (an unknown `kind`, a manifest that declares `enso_state`, a missing
file, and a path that escapes the directory), so a corpus that cannot be loaded
fails at the loader with a message that names the entry, instead of one row at a
time inside a job.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import ColumnElement, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from techcamp.assistant.adapters.orm import KbChunkRow, KbDocumentRow
from techcamp.assistant.application.ports import EMBEDDING_DIMENSION, Embedder
from techcamp.shared.ids import uuid7

CHARS_PER_TOKEN = 4
"""Characters per token of the *estimate*, not of any real tokenizer (see the module
docstring). 4 is the order of magnitude of the subword vocabularies this corpus will be
embedded with (`intfloat/multilingual-e5-small`, ADR-0026), and it is one named constant
so the figure is changed in one place if a model of a different density is pinned. The
estimate's error is a few percent, which moves a fragment boundary, never its size
bound."""

CHUNK_TARGET_TOKENS = 500
"""The fragment size ADR-0008:12 fixes ("~500 tokens con solapamiento"), read as an upper
bound: no fragment exceeds it, and a document that fits in less is stored whole."""

CHUNK_OVERLAP_TOKENS = 50
"""The tail every fragment after the first repeats, so a fact cut between two fragments
is retrievable from at least one of them (ADR-0008:12 "con solapamiento")."""

_PARAGRAPH_SPLIT = re.compile(r"\n[ \t]*\n+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])[ \t]+|\n+")


@dataclass(frozen=True, slots=True)
class Chunk:
    """One fragment of one document, ready to be embedded and stored
    (docs/03-modelo-datos.md:357-362).

    `position` is the fragment's 0-based index inside its document. The column does not
    exist — it is what makes a failure readable ("fragment 7 of the national bulletin")
    and what lets a test assert the stored fragments are the ones the chunker produced.
    """

    text: str
    position: int


@dataclass(frozen=True, slots=True)
class _Unit:
    """One indivisible piece of a document: a sentence, or the piece of an oversized
    sentence cut at a word boundary.

    `paragraph` is the index of the paragraph it came from, so the fill loop can cut at a
    paragraph boundary before a sentence boundary (ADR-0008:12's fragments are read in
    context) and a fragment spanning two paragraphs still reads as two paragraphs instead
    of one run-on block.
    """

    text: str
    paragraph: int


def estimate_tokens(text: str) -> int:
    """How many tokens `text` is *estimated* to be: `ceil(len / CHARS_PER_TOKEN)`.

    An approximation, deliberately (see the module docstring): the exact tokenizer belongs
    to the embedding adapter of ADR-0026 and this module only needs the size bound to
    hold. `0` for an empty string, so the whitespace that separated two units costs
    nothing.
    """
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def chunk_text(
    text: str,
    *,
    target_tokens: int = CHUNK_TARGET_TOKENS,
    overlap_tokens: int = CHUNK_OVERLAP_TOKENS,
) -> list[Chunk]:
    """Split one document into fragments of at most `target_tokens`, each repeating the
    last `overlap_tokens` of the fragment before it (ADR-0008:12).

    Paragraph boundaries first, sentence boundaries second: a paragraph that does not fit
    in the fragment being filled ends it, and only a paragraph longer than the target on
    its own is cut inside itself, at a sentence boundary. A sentence longer still is cut
    at words, the only place a word is ever separated from its neighbours.

    Deterministic: the same text always yields the same fragments, which is what lets
    ingestion key its idempotency on the document's bytes (docs/03 §`kb_document`,
    `kb_chunk`, `conversation` y `message`, row `Unicidad de la ingesta`) rather than on
    whatever the splitter happened to do that day.

    `[]` for a document with no text: an empty or whitespace-only fragment has nothing to
    retrieve, and a corpus row of one would be counted against the ~10 000 fragments
    docs/02-estimaciones.md:90 budgets.
    """
    chunks: list[Chunk] = []
    buffer: list[_Unit] = []
    buffer_tokens = 0

    for paragraph_index, paragraph in _paragraphs(text):
        units = _units(paragraph, paragraph_index=paragraph_index, target_tokens=target_tokens)
        # The paragraph boundary: a paragraph that does not fit ends the fragment being
        # filled, even while that fragment still has room.
        if buffer and buffer_tokens + _tokens_of(units) > target_tokens:
            buffer, buffer_tokens = _flush(
                buffer,
                chunks,
                overlap_tokens=overlap_tokens,
                room=target_tokens - _tokens_of(units),
            )
        for unit in units:
            unit_tokens = estimate_tokens(unit.text)
            if buffer and buffer_tokens + unit_tokens > target_tokens:
                buffer, buffer_tokens = _flush(
                    buffer, chunks, overlap_tokens=overlap_tokens, room=target_tokens - unit_tokens
                )
            buffer.append(unit)
            buffer_tokens += unit_tokens
    if buffer:
        chunks.append(_chunk(buffer, position=len(chunks)))

    return chunks


def _paragraphs(text: str) -> list[tuple[int, str]]:
    """The document's paragraphs, in order and numbered.

    A blank line is the boundary — Markdown's own rule, and what a bulletin and a guide
    are both written with. Whitespace-only pieces are dropped here, the first of the two
    places a whitespace-only fragment cannot come from.
    """
    return [
        (index, paragraph.strip())
        for index, paragraph in enumerate(_PARAGRAPH_SPLIT.split(text))
        if paragraph.strip()
    ]


def _units(paragraph: str, *, paragraph_index: int, target_tokens: int) -> list[_Unit]:
    """The paragraph's sentences as units, an oversized one cut at words."""
    units: list[_Unit] = []
    for sentence in _SENTENCE_SPLIT.split(paragraph):
        stripped = sentence.strip()
        if not stripped:
            continue
        units.extend(
            _Unit(piece, paragraph_index)
            for piece in _word_groups(stripped, target_tokens=target_tokens)
        )
    return units


def _word_groups(text: str, *, target_tokens: int) -> list[str]:
    """`[text]`, or the pieces a sentence longer than one fragment is cut into.

    Cut at spaces, never inside a word: an oversized sentence is a table row or an OCR
    run rather than prose, and half a word in a fragment is a token the model reads as
    noise. A *single* word over the target — a URL, a base64 blob, an OCR run with no
    spaces at all — is the one exception, cut at characters, because a fragment over the
    target is an embedding no model reads well and the size bound outranks the word.
    """
    if estimate_tokens(text) <= target_tokens:
        return [text]
    width = target_tokens * CHARS_PER_TOKEN
    groups: list[str] = []
    current: list[str] = []
    tokens = 0
    for word in text.split():
        if len(word) > width:
            if current:
                groups.append(" ".join(current))
                current = []
                tokens = 0
            groups.extend(word[start : start + width] for start in range(0, len(word), width))
            continue
        word_tokens = estimate_tokens(word)
        if current and tokens + word_tokens > target_tokens:
            groups.append(" ".join(current))
            current = []
            tokens = 0
        current.append(word)
        tokens += word_tokens
    if current:
        groups.append(" ".join(current))
    return groups


def _flush(
    buffer: list[_Unit],
    chunks: list[Chunk],
    *,
    overlap_tokens: int,
    room: int,
) -> tuple[list[_Unit], int]:
    """Store the buffer as one fragment; return the tail the next one repeats.

    `room` is how many tokens are left in the target once the unit that triggered this
    flush is accounted for, and the overlap never exceeds it: a repeated tail that would
    push the next fragment over `target_tokens` is shortened, and dropped when there is no
    room at all. Without that the buffer would hold nothing but a repeated tail, and the
    fragment written next would be a near-copy of the previous one.
    """
    chunks.append(_chunk(buffer, position=len(chunks)))
    tail = _overlap_tail(buffer, overlap_tokens=min(overlap_tokens, max(0, room)))
    return tail, _tokens_of(tail)


def _overlap_tail(buffer: list[_Unit], *, overlap_tokens: int) -> list[_Unit]:
    """The suffix of `buffer` the next fragment starts with (ADR-0008:12).

    The longest run of whole units that fits the budget; a last unit bigger than the whole
    budget contributes its trailing words, because repeating nothing would defeat the
    overlap and repeating a whole oversized unit would duplicate more than the budget
    asks for.
    """
    if overlap_tokens <= 0 or not buffer:
        return []
    tail: list[_Unit] = []
    tokens = 0
    for unit in reversed(buffer):
        unit_tokens = estimate_tokens(unit.text)
        if tokens + unit_tokens > overlap_tokens:
            break
        tail.insert(0, unit)
        tokens += unit_tokens
    if tail:
        return tail
    words = buffer[-1].text.split()
    kept: list[str] = []
    tokens = 0
    for word in reversed(words):
        word_tokens = estimate_tokens(word)
        if tokens + word_tokens > overlap_tokens:
            break
        kept.insert(0, word)
        tokens += word_tokens
    return [_Unit(" ".join(kept), buffer[-1].paragraph)] if kept else []


def _tokens_of(units: list[_Unit]) -> int:
    return sum(estimate_tokens(unit.text) for unit in units)


def _chunk(buffer: list[_Unit], *, position: int) -> Chunk:
    """The fragment's text: whole units, a line break between paragraphs, and nothing
    kept but the whitespace that separated them stripped away."""
    parts: list[str] = []
    current: list[str] = []
    paragraph = buffer[0].paragraph
    for unit in buffer:
        if unit.paragraph != paragraph:
            parts.append(" ".join(current))
            current = []
            paragraph = unit.paragraph
        current.append(unit.text)
    parts.append(" ".join(current))
    return Chunk(text="\n\n".join(part for part in parts if part), position=position)


FIXTURE_CORPUS_DIR = (Path(__file__).parent.parent / "fixtures" / "corpus").resolve()
"""The corpus that ships in the package: teaching documents, every one of them a
fixture (ADR-0008:32). Resolved from `__file__` rather than named as a path because a
package is imported from wherever it is installed, and an absolute path in source
would only ever work on the workstation that wrote it."""

CORPUS_DIR_ENV_VAR = "TECHCAMP_KB_CORPUS_DIR"
"""The switch the owner turns once the licence review of the real corpus is signed
(ADR-0008:32, Open question 2 of the feature doc). Read by `corpus_dir()` here, not
by `shared/config.py`: it is one corpus, not a setting of the product."""

MANIFEST_NAME = "manifest.json"
"""The corpus's index: which documents exist, and everything ADR-0008:12 asks each
one to record. Not a second copy of the text — the file is the text, and the manifest
is where the metadata lives."""

CORPUS_KINDS = ("guide", "agroclimatic_bulletin")
"""docs/03-modelo-datos.md:352's vocabulary, checked here so an entry outside it is
refused by the loader that read it rather than by the `ck_kb_document_kind` CHECK one
row at a time inside a job."""

_DOCUMENT_REVISIONS_UNIQUE = "uq_kb_document_sha256"
"""The constraint that decides which of two concurrent runs of the same revision
stored it (docs/03 §`kb_document`, `kb_chunk`, `conversation` y `message`, row
`Unicidad de la ingesta`). Its name is read from the driver error, exactly like
`telemetry`'s `_violated_constraint`."""

type CorpusKind = Literal["guide", "agroclimatic_bulletin"]
"""docs/03:352's two kinds, as the type the manifest is validated into."""

type EnsoState = Literal["neutral", "el_nino", "la_nina"]
"""docs/03:354's vocabulary. Not an alias of the column's CHECK and not a new one:
the parser produces it and nothing else writes it."""

_ARTICLE = r"(?:el\s+|la\s+)?"
_ENSO_PHASE = rf"{_ARTICLE}[nñ]i[nñ]o|{_ARTICLE}[nñ]i[nñ]a|neutr(?:o|al)"
_ENSO_LABELLED = re.compile(
    # A label and then, immediately, the phase it labels: `estado enso`,
    # `estado del enso`, then the phase after an optional `:`/`-`/`=`. The accented
    # letters are spelled as classes rather than as `ñ` so the match is case- and
    # accent-insensitive without normalising the text, which keeps the *original*
    # spelling ("El Niño") for the error message that names two disagreeing lines.
    rf"estado\s+(?:del\s+)?enso\s*[:=\-‐‑–—‒―]?\s*({_ENSO_PHASE})\b",
    re.IGNORECASE,
)


class CorpusError(RuntimeError):
    """A corpus directory that cannot be loaded, named by the entry that refused it.

    One error for the four refusals the loader makes (unknown `kind`, a declared
    `enso_state`, a missing file, a path out of the directory) and for the two of
    `parse_enso_state`, because they are the same failure from the corpus's side: a
    manifest or a bulletin this ingestion cannot store without guessing. A `RuntimeError`
    and not a domain error on purpose — nothing in `domain` decides what a corpus file
    may say, and the loader is a filesystem adapter (AGENTS.md §Architecture rules).
    """


class EmbeddingContractError(RuntimeError):
    """What the `Embedder` port returned is not what the column can store.

    One vector per fragment, each exactly `EMBEDDING_DIMENSION` floats wide
    (ports.py, ADR-0026). The width is the column's width, so a mismatch is raised
    **before** the write: as this error, not as a database failure after the model
    has already run and been paid for. Raised, never padded or truncated — a vector
    silently resized is a vector the retrieval compares against the wrong geometry.
    """


@dataclass(frozen=True, slots=True)
class CorpusDocument:
    """What one manifest entry declares about a document, read and checked
    (docs/03-modelo-datos.md:347-355; ADR-0008:12).

    **Not `assistant.application.ports.KnowledgeDocument`**, although the fields are
    the same ones: that is a *stored* row and it carries the `id` the database gave
    it, while this is a file on disk that has no id yet. Reusing it would mean
    carrying a placeholder `uuid4()` that reads like a real one — a document that
    looks stored and is not. One owner per shape: this is the loader's, that is the
    repository's, and `ingest_document` is the single seam that turns the first into
    the second.

    `published_on` is a `date`, parsed here rather than left as the manifest's
    string, because the column is `Date` and a job argument cannot carry a date
    anyway (the same reason `weather`'s consolidation takes an ISO string).
    """

    title: str
    source_url: str
    license: str
    kind: CorpusKind
    published_on: date
    department: str | None = None
    enso_state: str | None = None
    """Parsed from the document, and **only for a bulletin** (docs/03:354 "solo
    boletines"): the state is a property of the bulletin, so a guide is stored with
    a null one whatever its prose mentions."""


@dataclass(frozen=True, slots=True)
class CorpusSource:
    """One document of a corpus directory: where it is, what it declares, and its
    bytes.

    `sha256` is the digest of the bytes **as they are on disk** — no normalisation,
    no newline translation — because the digest is about *the file*, and it is the
    second half of the ingestion idempotency key `(source_url, sha256)` (docs/03 §
    `kb_document`, `kb_chunk`, `conversation` y `message`, row `Unicidad de la
    ingesta`). `text` is the same bytes decoded, so a caller never reads the file a
    second time and gets a different answer.
    """

    path: Path
    document: CorpusDocument
    sha256: str
    text: str


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    """What ingesting one source did, which is what the job logs.

    `stored` is `False` for the three legitimate no-writes — `unchanged` (the
    revision is already there), `no fragments to store`, `stored by another run` —
    so the caller can log "why nothing happened" instead of a silent success, and
    can tell them apart from a run that stored something.
    """

    document_id: UUID | None
    chunk_count: int
    stored: bool
    reason: str


UNCHANGED = "unchanged"
"""The revision is already stored: re-running over unchanged bytes is a no-op
(docs/03 row `Unicidad de la ingesta`)."""

SUPERSEDED = "stored"
"""A new revision replaced the stored one."""

NO_FRAGMENTS = "no fragments to store"
"""A document with no text. Its row would be a citation with nothing behind it
(ADR-0008:12), so nothing is written and the job says so."""

CONCURRENTLY_STORED = "stored by another run first"
"""Another worker stored this exact revision while this one was writing it: the
`(source_url, sha256)` constraint decided, and this run keeps its delete so it does
not touch the row the winner wrote."""


def corpus_dir() -> Path:
    """The corpus directory: `TECHCAMP_KB_CORPUS_DIR` when it is set, else the
    packaged fixtures.

    Read at call time, not at import time, so a test — and the owner pointing the
    seminar at a licence-reviewed corpus — changes the corpus without reimporting the
    worker. An empty value is treated as unset: a variable exported empty in a
    compose file is a missing setting, not a corpus of no documents.
    """
    configured = os.environ.get(CORPUS_DIR_ENV_VAR, "").strip()
    return Path(configured) if configured else FIXTURE_CORPUS_DIR


def load_corpus(directory: Path | None = None) -> list[CorpusSource]:
    """Every document of the corpus in `directory`, or of `corpus_dir()`.

    `manifest.json` is read, then each entry's file is read **once**: the digest is
    over those bytes and `text` is their decode, so the two can never disagree.
    Entries are returned in manifest order, so a run over an unchanged corpus
    touches rows in the same order every time — which is what makes the concurrency
    story below converge.

    **Nothing here opens `source_url`.** It is recorded (ADR-0008:12) and never
    fetched: the seminar room has no internet (ADR-0021) and the licence review of
    the real corpus is a decision an owner signs (ADR-0008:32).
    """
    root = corpus_dir() if directory is None else Path(directory)
    manifest = root / MANIFEST_NAME
    try:
        raw = manifest.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise CorpusError(
            f"kb corpus: {manifest} does not exist, so the corpus has no manifest"
        ) from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CorpusError(f"kb corpus: {manifest} is not readable JSON: {exc}") from exc
    entries = parsed.get("documents") if isinstance(parsed, dict) else None
    if not isinstance(entries, list):
        raise CorpusError(
            f"kb corpus: {manifest} has no 'documents' list, so there is nothing to load"
        )
    return [_source(root, entry) for entry in entries]


def _source(root: Path, entry: object) -> CorpusSource:
    """One manifest entry as a `CorpusSource`, or the refusal that keeps it out."""
    if not isinstance(entry, dict):
        raise CorpusError(f"kb corpus: a manifest entry is not an object: {entry!r}")
    name = _text_of(entry, "file", name="the manifest")
    _refuse_declared_enso(entry, name=name)
    path = _inside(root, name)
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise CorpusError(
            f"kb corpus: {name} is listed in the manifest and is not in the corpus"
        ) from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CorpusError(f"kb corpus: {name} is not UTF-8 text: {exc}") from exc
    kind = _kind_of(entry, name=name)
    return CorpusSource(
        path=path,
        document=CorpusDocument(
            title=_text_of(entry, "title", name=name),
            source_url=_text_of(entry, "source_url", name=name),
            license=_text_of(entry, "license", name=name),
            kind=kind,
            published_on=_published_on(entry, name=name),
            department=_department_of(entry, name=name),
            # Parsed, never declared: docs/06-diseno-detallado.md:375 says the state
            # is registered "al cargar el boletín", which is this line and not one
            # beside it, so a bulletin whose text changes changes its stored state.
            enso_state=parse_enso_state(text) if kind == "agroclimatic_bulletin" else None,
        ),
        sha256=hashlib.sha256(raw).hexdigest(),
        text=text,
    )


def _text_of(entry: dict[str, object], key: str, *, name: str) -> str:
    """The entry's `key` as a non-empty string, or the refusal naming it.

    The columns ADR-0008:12 asks a document to record are `NOT NULL`
    (docs/03-modelo-datos.md:348-354), so an entry missing one is refused here with
    the file it belongs to, instead of failing a `NOT NULL` inside a job where the
    error would name a constraint and not a manifest.
    """
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CorpusError(f"kb corpus: {name} has no {key!r} to store")
    return value


def _kind_of(entry: dict[str, object], *, name: str) -> CorpusKind:
    value = _text_of(entry, "kind", name=name)
    if value not in CORPUS_KINDS:
        raise CorpusError(
            f"kb corpus: {name} has kind {value!r}, which is not one of "
            f"{', '.join(CORPUS_KINDS)} (docs/03-modelo-datos.md:352)"
        )
    return cast("CorpusKind", value)


def _published_on(entry: dict[str, object], *, name: str) -> date:
    value = _text_of(entry, "published_on", name=name)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CorpusError(
            f"kb corpus: {name} has published_on {value!r}, which is not an ISO date"
        ) from exc


def _department_of(entry: dict[str, object], *, name: str) -> str | None:
    """The entry's department, or `None`.

    `department = null` **is** the national bulletin (docs/03-modelo-datos.md:351),
    the value docs/06 §9's seasonal-climate fact falls back to when a department has
    no bulletin of its own, so an absent key is a value and never missing evidence.
    An empty string is not one, though: a department named "" would be a department
    no fact could read.
    """
    value = entry.get("department")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CorpusError(f"kb corpus: {name} has an empty 'department', which names no region")
    return value


def _refuse_declared_enso(entry: dict[str, object], *, name: str) -> None:
    """Refuse a manifest that states the ENSO phase beside the document.

    One owner per fact: the state is parsed out of the text (docs/06:375), so a
    manifest carrying one is a second source of truth that can disagree with the
    bulletin it describes. Refused, not ignored — silently dropping it would leave
    the next reader believing the manifest's word, which is the very thing this
    check exists to prevent.
    """
    if "enso_state" in entry:
        raise CorpusError(
            f"kb corpus: {name} declares enso_state, which is parsed from the document "
            "itself (docs/06-diseno-detallado.md:375); a manifest may not state it"
        )


def _inside(root: Path, name: str) -> Path:
    """`root / name`, refused when it resolves outside `root`.

    A corpus directory is trusted input once the owner points at it (ADR-0008:32),
    and `../` in an entry is not a document of that corpus: it is a path out of the
    directory the licence review covered. Both sides are resolved before the
    comparison, so a symlink inside the corpus cannot escape it either.
    """
    resolved = (root / name).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise CorpusError(f"kb corpus: {name} is outside the corpus directory {root}")
    return resolved


def parse_enso_state(text: str) -> str | None:
    """The ENSO phase a bulletin **states**, in docs/03:354's vocabulary, or `None`.

    Read from the bulletin's own words because that is where docs/06:375 says the
    state comes from, and matched with the accents and the case folded, which are the
    OCR and formatting variants of the same fact ("El Niño", "EL NIÑO", "el nino" are
    one state).

    **A labelled line wins over the body**, and prose is never a match: a bulletin
    that reviews the seasons it lived through names all three phases in its body, and
    the one it *states* is the labelled line. Two labelled lines that disagree are
    `CorpusError` rather than a coin toss — the stored state is what docs/06 §9's
    seasonal-climate fact answers with, and an arbitrary phase is a wrong season.
    """
    stated: dict[EnsoState, str] = {}
    for line in text.splitlines():
        for match in _ENSO_LABELLED.finditer(line):
            stated.setdefault(_enso_of(match.group(1)), match.group(1))
    if len(stated) > 1:
        rendered = " and ".join(f"{stated[state]!r}" for state in sorted(stated))
        raise CorpusError(
            f"kb corpus: a document states more than one ENSO phase ({rendered}); a "
            "bulletin that disagrees with itself is not ingested"
        )
    return next(iter(stated), None)


def _enso_of(phase: str) -> EnsoState:
    """The docs/03:354 state one written phase names.

    Folds the accents off the matched text rather than reading a normalised copy of
    it, so the phase the caller reports and the phase the bulletin wrote are the same
    string. `neutro` and `neutral` are one state: both are the Spanish bulletin and
    the translated one, and the column admits only `neutral`.
    """
    folded = _fold(phase)
    if "nino" in folded:
        return "el_nino"
    if "nina" in folded:
        return "la_nina"
    return "neutral"


def _fold(text: str) -> str:
    """`text` with the accents removed and the case folded: "El Niño" -> "el nino"."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


async def ingest_document(
    session: AsyncSession, source: CorpusSource, *, embedder: Embedder
) -> IngestOutcome:
    """Store one source and its fragments, idempotently by `(source_url, sha256)`.

    **The unchanged case never touches the rows and never calls the model.** It is
    checked first, before the document is chunked and before `embedder` is awaited,
    because a re-run over unchanged bytes must not rewrite its fragments (docs/03 row
    `Unicidad de la ingesta`) and because embedding is the only expensive step here.
    The stored row's id is returned, so a caller can still cite the document it found.

    **A changed digest for the same `source_url` supersedes** (docs/03 row
    `Unicidad de la ingesta`, ADR-0012's *(tipo, entidad, fecha)* read as *(source,
    document, revision)*): the stored chunks and the stored row of that source are
    deleted inside the same savepoint that inserts the new revision, so the corpus
    keeps **one document per source** and no fragment of the old revision is left
    behind. Deleting before inserting is what makes that hold without a second
    round trip; the `(source_url, sha256)` constraint is what makes it converge when
    two runs overlap — the loser of the race sees the unique violation, rolls back to
    the savepoint (which restores the delete, so the winner's row survives untouched)
    and reports `stored by another run first` instead of overwriting a revision it
    did not write. Any other integrity error is re-raised: a sweep must not swallow a
    broken statement because it happened to fail here.

    The embedding seam is checked before the write and before the transaction: see
    `_embedded` and `EmbeddingContractError`.
    """
    stored = await _revision(session, source_url=source.document.source_url, sha256=source.sha256)
    if stored is not None:
        return IngestOutcome(document_id=stored.id, chunk_count=0, stored=False, reason=UNCHANGED)
    chunks = chunk_text(source.text)
    if not chunks:
        return IngestOutcome(document_id=None, chunk_count=0, stored=False, reason=NO_FRAGMENTS)
    vectors = await _embedded(embedder, [chunk.text for chunk in chunks], source=source)
    try:
        async with session.begin_nested():
            document_id = await _store(session, source=source, chunks=chunks, vectors=vectors)
    except Exception as exc:
        if not _is_same_revision(exc):
            raise
        return IngestOutcome(
            document_id=None, chunk_count=0, stored=False, reason=CONCURRENTLY_STORED
        )
    return IngestOutcome(
        document_id=document_id, chunk_count=len(chunks), stored=True, reason=SUPERSEDED
    )


async def _embedded(
    embedder: Embedder, texts: list[str], *, source: CorpusSource
) -> list[list[float]]:
    """The vectors for `texts`, or `EmbeddingContractError` before anything is written.

    The check is on the way back from the port, not on the way to the database: the
    width is `vector(384)`'s width (ADR-0026), so a model that returned 128 or 512
    floats would otherwise cost a full embedding run and then fail as a database
    error — and the failure would name pgvector instead of the model that is wrong.
    """
    vectors = list(await embedder.embed(texts))
    if len(vectors) != len(texts):
        raise EmbeddingContractError(
            f"kb ingest: the embedder returned {len(vectors)} vectors for "
            f"{len(texts)} fragments of {source.path.name}; the port promises one each"
        )
    for position, vector in enumerate(vectors):
        if len(vector) != EMBEDDING_DIMENSION:
            raise EmbeddingContractError(
                f"kb ingest: vector {position} of {source.path.name} is "
                f"{len(vector)} wide, and kb_chunk.embedding is "
                f"vector({EMBEDDING_DIMENSION}) (ADR-0026)"
            )
    return vectors


async def _revision(session: AsyncSession, *, source_url: str, sha256: str) -> KbDocumentRow | None:
    """The stored row of this exact revision, or `None`.

    Scoped to the whole revision — both halves of the key — because that is what the
    unique constraint protects: a row with this `source_url` and another digest is a
    *revision* to supersede, not the same document to skip.
    """
    result = await session.execute(
        select(KbDocumentRow).where(
            KbDocumentRow.source_url == source_url, KbDocumentRow.sha256 == sha256
        )
    )
    return result.scalars().first()


async def _store(
    session: AsyncSession, *, source: CorpusSource, chunks: list[Chunk], vectors: list[list[float]]
) -> UUID:
    """Delete whatever this source had and insert this revision, then return its id.

    Both statements and the inserts share the caller's savepoint, so a unique
    violation on `(source_url, sha256)` rolls the delete back with them — which is
    what lets `ingest_document` keep the row the winning run wrote.
    """
    document_id = uuid7()
    document = source.document
    await session.execute(delete(KbChunkRow).where(_chunks_of(document.source_url)))
    await session.execute(
        delete(KbDocumentRow).where(KbDocumentRow.source_url == document.source_url)
    )
    session.add(
        KbDocumentRow(
            id=document_id,
            title=document.title,
            source_url=document.source_url,
            license=document.license,
            kind=document.kind,
            sha256=source.sha256,
            published_on=document.published_on,
            department=document.department,
            enso_state=document.enso_state,
        )
    )
    # Flushed before its fragments, not after them. `kb_chunk.document_id` is a
    # `ForeignKey` and the ORM knows nothing else about the order of two independent
    # mappers (`orm.py` declares no relationship between them), so a single flush would
    # emit the `kb_chunk` inserts first and the database would refuse them as orphans.
    await session.flush()
    session.add_all(
        KbChunkRow(id=uuid7(), document_id=document_id, content=chunk.text, embedding=vector)
        for chunk, vector in zip(chunks, vectors, strict=True)
    )
    await session.flush()
    return document_id


def _chunks_of(source_url: str) -> ColumnElement[bool]:
    """Every `kb_chunk` of the documents this `source_url` stored.

    A subquery rather than a list of ids: the delete has to reach the chunks of a
    revision that was inserted by another process, whose id this one never read, and
    it has to be a single statement so it takes the same row locks as the delete of
    the documents themselves.
    """
    return KbChunkRow.document_id.in_(
        select(KbDocumentRow.id).where(KbDocumentRow.source_url == source_url)
    )


def _is_same_revision(exc: Exception) -> bool:
    """Whether `exc` is the duplicate of this exact revision and nothing else.

    asyncpg's driver error is chained as `__cause__` on the error SQLAlchemy's
    dialect re-wraps, and it carries `constraint_name`
    (`telemetry/adapters/repositories.py::_violated_constraint` reads it the same
    way). Any other `IntegrityError` is re-raised: a job that swallowed every one of
    them would report a clean run over a corpus it never stored.
    """
    return isinstance(exc, IntegrityError) and _violated_constraint(exc) == (
        _DOCUMENT_REVISIONS_UNIQUE
    )


def _violated_constraint(exc: IntegrityError) -> str | None:
    driver_error = getattr(exc.orig, "__cause__", None)
    name: str | None = getattr(driver_error, "constraint_name", None)
    return name
