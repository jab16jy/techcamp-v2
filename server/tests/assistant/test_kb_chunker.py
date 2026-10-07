"""The corpus chunker (E12 T3; ADR-0008:12 "Fragmentar los documentos (~500 tokens con
solapamiento)", docs/03-modelo-datos.md §`kb_document`, `kb_chunk`, `conversation` y
`message`; D-T12.2).

Pure functions, no database and no provider: what is pinned here is the *shape* of the
fragments the corpus is stored as, and the two properties retrieval depends on later
(ADR-0008:12 — top 5 fragments at similarity >= 0.75 over `kb_chunk`):

* fragments are about `CHUNK_TARGET_TOKENS` and never larger, so an embedding is of a
  fragment a model can read rather than of a whole document;
* consecutive fragments **overlap**, so a sentence that answers the question is not cut
  in half between two fragments and retrieved as neither;
* the boundary is the last one available — a paragraph boundary before a sentence
  boundary — and a fragment is never empty or whitespace only, because a fragment with
  no text has no retrieval value and an empty `content` is evidence the loader silently
  failed.

**The token count is an estimate, not a tokenizer.** `server/pyproject.toml` is outside
this task's surface, so no tokenizer dependency may be added and none is needed: the
chunker converts characters to tokens with one named ratio. It *approximates* the real
count of the model that will embed these fragments, and the exact tokenizer belongs to
the embedding adapter of ADR-0026, which owns the model; what this module has to
guarantee is the size bound. `test_kb_ingest_job.py` keeps the "no tokenizer, no torch"
half of that promise, over both modules of the ingestion path.
"""

from __future__ import annotations

import pytest

from techcamp.assistant.adapters.kb_ingest import (
    CHARS_PER_TOKEN,
    CHUNK_OVERLAP_TOKENS,
    CHUNK_TARGET_TOKENS,
    chunk_text,
    estimate_tokens,
)


def _unit(tokens: int) -> str:
    """One sentence of exactly `tokens` estimated tokens.

    A single repeated word, so the character count is the only thing the estimate can
    read and a test's arithmetic stays visible: `estimate_tokens(_unit(200)) == 200`.
    """
    return "a" * (tokens * CHARS_PER_TOKEN - 1) + "."


def _paragraph(*sentences: int) -> str:
    """One paragraph made of sentences of the given token counts."""
    return " ".join(_unit(tokens) for tokens in sentences)


def _prose(*, sentences: int, words_per_sentence: int = 64) -> str:
    """One paragraph of ordinary short sentences, `words_per_sentence` words each.

    The chunker's fixtures above are one enormous "sentence" per paragraph, which makes
    a *sentence* boundary impossible to cut at. Real prose has many short sentences, and
    the fragment boundary lands on one of them, so the overlap test needs this shape.
    """
    return " ".join(
        " ".join(["ma"] * words_per_sentence) + "." for _ in range(sentences)
    )


def _document(*paragraphs: str) -> str:
    return "\n\n".join(paragraphs)


def _tokens_of(chunk: str) -> int:
    return estimate_tokens(chunk)


def test_the_fragment_size_and_its_overlap_are_named_constants() -> None:
    """ADR-0008:12 says "~500 tokens con solapamiento" and nothing more precise, so the
    two figures are named constants rather than literals in the fill loop: a corpus
    re-chunked against another target is one edit here, and a test reads the number it
    asserted."""
    assert CHUNK_TARGET_TOKENS == 500
    assert CHUNK_OVERLAP_TOKENS == 50
    assert 0 < CHUNK_OVERLAP_TOKENS < CHUNK_TARGET_TOKENS


def test_the_token_estimate_is_a_documented_ratio_not_a_tokenizer() -> None:
    """`estimate_tokens` is `ceil(chars / CHARS_PER_TOKEN)`, and the module says plainly
    that it approximates. The ratio is deterministic and width-independent, which is
    what makes a test above able to state a fragment size in tokens at all."""
    assert CHARS_PER_TOKEN == 4
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
    assert estimate_tokens("a" * (CHARS_PER_TOKEN * 200)) == 200


def test_a_document_shorter_than_the_target_is_one_fragment() -> None:
    """A guide of two short paragraphs is not cut: a fragment smaller than the target is
    not a defect, and splitting it would only give the retrieval two fragments of the
    same document with less context each."""
    text = _document(_paragraph(50, 60), _paragraph(40))

    chunks = chunk_text(text)

    assert len(chunks) == 1
    assert chunks[0].position == 0
    assert chunks[0].text == text


def test_a_long_document_is_split_into_fragments_of_at_most_the_target() -> None:
    """ADR-0008:12's ~500 tokens is an upper bound a model can be asked about. Eight
    paragraphs of 200 tokens each is 1 600 tokens, so the document cannot be one
    fragment, and none of the fragments may exceed the target."""
    text = _document(*[_paragraph(200) for _ in range(8)])

    chunks = chunk_text(text)

    assert len(chunks) > 1
    assert all(_tokens_of(chunk.text) <= CHUNK_TARGET_TOKENS for chunk in chunks)
    assert [chunk.position for chunk in chunks] == list(range(len(chunks)))


def test_the_boundary_between_two_fragments_is_a_paragraph_boundary() -> None:
    """Paragraphs first, sentences second. Two 300-token paragraphs do not fit in one
    500-token fragment, and the cut belongs at the end of the first paragraph rather
    than inside the second one: a fragment that ends mid-paragraph loses the context
    that says which section it came from."""
    first = _paragraph(300)
    second = _paragraph(300)

    chunks = chunk_text(_document(first, second))

    assert [chunk.text for chunk in chunks] == [first, second]


def test_a_paragraph_longer_than_the_target_is_split_at_sentence_boundaries() -> None:
    """One paragraph of four 200-token sentences is 800 tokens, so it cannot be one
    fragment, and it may not be split mid-sentence: every fragment must be a whole
    number of sentences, each one ending in its own full stop."""
    sentences = [200, 200, 200, 200]
    text = _paragraph(*sentences)

    chunks = chunk_text(text)

    assert len(chunks) > 1
    assert all(_tokens_of(chunk.text) <= CHUNK_TARGET_TOKENS for chunk in chunks)
    units = [_unit(tokens) for tokens in sentences]
    assert all(chunk.text in _sentence_sequences(units) for chunk in chunks)


def _sentence_sequences(units: list[str]) -> list[str]:
    """Every contiguous run of `units`, so a test can assert a fragment is whole
    sentences rather than comparing text it rebuilt itself."""
    runs = [
        " ".join(units[start:stop])
        for start in range(len(units))
        for stop in range(start + 1, len(units) + 1)
    ]
    return runs


def test_consecutive_fragments_share_their_overlap() -> None:
    """ADR-0008:12 fragments "con solapamiento". Without it a sentence that answers the
    question can be cut between two fragments, and neither of them retrieves as a whole
    answer; so the fragment after the first starts with the tail of the one before it, up
    to `CHUNK_OVERLAP_TOKENS`, and it is the tail of *whole sentences* because a
    sentence is the unit the cut respects."""
    text = _prose(sentences=40)

    chunks = chunk_text(text)

    assert len(chunks) > 1
    first, second = chunks[0], chunks[1]
    repeated = first.text.rsplit(". ", 1)[-1]
    assert second.text.startswith(repeated)
    assert estimate_tokens(repeated) <= CHUNK_OVERLAP_TOKENS


def test_a_sentence_longer_than_the_target_is_split_at_word_boundaries() -> None:
    """A single sentence of 900 tokens with no full stop in it (a table row, an OCR run)
    cannot be stored as one fragment, so it is cut at words: every fragment holds whole
    words, taken from the document in order. Words repeated across a boundary are the
    overlap doing its job, not a lost word."""
    words = ["riega"] * 600
    text = " ".join(words)

    chunks = chunk_text(text)

    assert len(chunks) > 1
    assert all(_tokens_of(chunk.text) <= CHUNK_TARGET_TOKENS for chunk in chunks)
    assert all(set(chunk.text.split()) == {"riega"} for chunk in chunks)
    starts = [words.index(chunk.text.split()[0]) for chunk in chunks]
    assert starts == sorted(starts), "fragments must follow the document's order"


def test_no_fragment_is_empty_or_whitespace_only() -> None:
    """A fragment with no text is a row with no retrieval value, and one this module can
    only produce by mis-splitting: blank lines between paragraphs, leading and trailing
    whitespace, and a document that is nothing but whitespace."""
    text = _document(_paragraph(200), "", "   ", _paragraph(200), _paragraph(200))

    chunks = chunk_text(text)

    assert chunks
    assert all(chunk.text.strip() == chunk.text and chunk.text for chunk in chunks)


def test_a_whitespace_only_document_produces_no_fragment_at_all() -> None:
    """The other half of the same rule: no text is no fragment. An empty `kb_chunk` row
    would embed to a vector nobody can retrieve and would be counted in the corpus's
    ~10 000 fragments (docs/02-estimaciones.md:90)."""
    assert chunk_text("") == []
    assert chunk_text("\n\n   \n\t\n") == []


def test_the_chunking_is_deterministic() -> None:
    """The ingestion job is repeatable and its idempotency key is the digest of the
    document's bytes (ADR-0012's *(tipo, entidad, fecha)* rule applied to the corpus,
    docs/03 §`kb_document`, `kb_chunk`, `conversation` y `message`, row `Unicidad de la
    ingesta`). Two runs over the same bytes must therefore produce the same fragments,
    or the corpus would be re-embedded on every run for no reason."""
    text = _document(*[_paragraph(150, 90) for _ in range(5)])

    assert [chunk.text for chunk in chunk_text(text)] == [
        chunk.text for chunk in chunk_text(text)
    ]


@pytest.mark.parametrize("tokens", [1, CHUNK_TARGET_TOKENS, CHUNK_TARGET_TOKENS + 1, 5000])
def test_a_fragment_never_exceeds_the_target_for_any_single_paragraph(tokens: int) -> None:
    """The size bound has to hold for every input, not only for the round numbers above:
    a fragment of exactly the target, one token over it, and a paragraph five times the
    target all come back within the bound."""
    chunks = chunk_text(_document(_paragraph(tokens)))

    assert all(_tokens_of(chunk.text) <= CHUNK_TARGET_TOKENS for chunk in chunks)
