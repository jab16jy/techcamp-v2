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
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

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
