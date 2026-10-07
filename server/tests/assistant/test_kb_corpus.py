"""The local corpus loader and the ENSO parser (E12 T3; ADR-0008:12,32;
docs/03-modelo-datos.md §`kb_document`, `kb_chunk`, `conversation` y `message`;
docs/06-diseno-detallado.md §9 "el estado ENSO se registra al cargar el boletín";
ADR-0012).

Pure file reading, no database and no provider, and that is the point of the module's
most important test: **the ingestion path has no way to reach the network.** ADR-0008:32
("El corpus de la v1 debe revisarse por licencia antes de migrarse") is a gate on *what*
may be loaded, so the only thing that ships in the package is the seminar fixture corpus,
every file of it marked as a fixture, and the real corpus is a directory the owner points
at once the licence check is signed (the feature doc's Open question 2). Nothing here
downloads a bulletin: `source_url` is **recorded**, never opened.

What these tests protect:

* one entry per source with everything ADR-0008:12 asks a document to record — source,
  licence, kind, publication date — plus `department`, whose `null` **is** the national
  bulletin (docs/03:353), the value docs/06 §9's seasonal-climate fact falls back to;
* the ENSO state parsed **from the bulletin's own text**, in docs/03:354's vocabulary
  (`neutral | el_nino | la_nina`), and only for a bulletin (docs/03:354 "solo boletines");
* `sha256` as the digest of the document's bytes, which is the second half of the
  ingestion idempotency key `(source_url, sha256)` (docs/03 §`kb_document`, `kb_chunk`,
  `conversation` y `message`, row `Unicidad de la ingesta`);
* a manifest entry that reaches **outside** the corpus directory being refused: a corpus
  directory the owner points at is trusted input, and a path that escapes it is neither a
  document nor a licence review.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from techcamp.assistant.adapters.kb_ingest import (
    CORPUS_DIR_ENV_VAR,
    FIXTURE_CORPUS_DIR,
    CorpusError,
    corpus_dir,
    load_corpus,
    parse_enso_state,
)

FIXTURE_LICENSE = "CC-BY-4.0 (test fixture)"
NATIONAL_BULLETIN = "boletin-agroclimatico-nacional-2026-09.md"
REGIONAL_BULLETIN = "boletin-mesa-tecnica-cordoba-2026-09.md"
GUIDE = "guia-riego-goteo-caribe.md"


def _by_file(directory: Path | None = None) -> dict[str, object]:
    return {source.path.name: source for source in load_corpus(directory)}


def _write_corpus(
    directory: Path,
    documents: list[dict[str, object]],
    *,
    files: dict[str, str] | None = None,
) -> Path:
    """A corpus directory of the manifest's own shape, for the tests that need one
    document and not the shipped three."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(
        json.dumps({"corpus": "test", "documents": documents}), encoding="utf-8"
    )
    for name, text in (files or {}).items():
        (directory / name).write_text(text, encoding="utf-8")
    return directory


def _entry(file: str, **overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "file": file,
        "title": "Documento de prueba",
        "source_url": f"https://example.invalid/{file}",
        "license": FIXTURE_LICENSE,
        "kind": "guide",
        "published_on": "2026-09-01",
        "department": None,
    }
    return {**entry, **overrides}


# -- the shipped corpus --------------------------------------------------------


def test_the_corpus_directory_is_the_packaged_fixtures_unless_another_is_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`TECHCAMP_KB_CORPUS_DIR` is the switch the owner turns once the licence review of
    the real corpus is signed (ADR-0008:32, Open question 2 of the feature doc). It is
    read **here**, in the adapter that uses it, and not in `shared/config.py`: it is one
    corpus, not a setting of the product, and it must not become a documented setting of
    every process."""
    monkeypatch.delenv(CORPUS_DIR_ENV_VAR, raising=False)
    assert corpus_dir() == FIXTURE_CORPUS_DIR

    monkeypatch.setenv(CORPUS_DIR_ENV_VAR, str(tmp_path))
    assert corpus_dir() == tmp_path


def test_the_corpus_directory_variable_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Same switch, seen from the loader: the job reads the directory it is pointed at,
    so the licence-reviewed corpus is loaded by configuration and not by a code change."""
    _write_corpus(tmp_path, [_entry("guia.md")], files={"guia.md": "El riego por goteo."})

    monkeypatch.setenv(CORPUS_DIR_ENV_VAR, str(tmp_path))
    sources = load_corpus()

    assert [source.path.name for source in sources] == ["guia.md"]


def test_the_fixture_corpus_ships_three_documents() -> None:
    """The seminar needs a national bulletin and a regional one for docs/06 §9's
    seasonal-climate fact ("el último boletín del departamento de la finca, o del
    nacional si no hay uno regional"), plus at least one guide, because a corpus of
    bulletins only would never exercise the `guide` kind of docs/03:352."""
    sources = load_corpus()

    assert sorted(source.path.name for source in sources) == sorted(
        [NATIONAL_BULLETIN, REGIONAL_BULLETIN, GUIDE]
    )
    assert {source.document.kind for source in sources} == {
        "agroclimatic_bulletin",
        "guide",
    }


def test_every_shipped_fixture_is_marked_as_a_fixture() -> None:
    """ADR-0008:32: the corpus must be licence-reviewed before it is migrated. Until that
    review happens only fixtures ship, and a fixture that could be mistaken for a real
    bulletin would be a licence question the repository cannot answer. So every entry
    names a test-fixture licence and a source host reserved for examples
    (RFC 2606's `.invalid`)."""
    sources = load_corpus()

    assert all(source.document.license == FIXTURE_LICENSE for source in sources)
    assert all(
        source.document.source_url.startswith("https://example.invalid/")
        for source in sources
    )


def test_a_document_records_its_source_licence_kind_and_publication_date() -> None:
    """ADR-0008:12: "Cada documento registra su fuente y licencia para poder citarlo" —
    a citation is built from these columns (T4's `KnowledgeRepository.get_document`), so
    a document that stored no licence could not be cited at all."""
    source = _by_file()[GUIDE]

    assert source.document.title
    assert source.document.source_url
    assert source.document.license == FIXTURE_LICENSE
    assert source.document.kind == "guide"
    assert source.document.published_on.year == 2026


def test_the_national_bulletin_has_no_department() -> None:
    """`department = null` **is** the national bulletin (docs/03:353), the value
    docs/06 §9's seasonal-climate fact falls back to when a department has no bulletin
    of its own. Null is a value here, not missing evidence."""
    national = _by_file()[NATIONAL_BULLETIN]

    assert national.document.kind == "agroclimatic_bulletin"
    assert national.document.department is None


def test_a_regional_bulletin_carries_its_department() -> None:
    """The other side of the same value: the mesas técnicas bulletin names the
    departamento it covers (docs/06 §9: Córdoba, Sucre and
    Magdalena–Cesar–La Guajira–Atlántico)."""
    regional = _by_file()[REGIONAL_BULLETIN]

    assert regional.document.department == "Córdoba"


def test_the_enso_state_is_parsed_from_the_bulletin_text() -> None:
    """docs/06:374: "El estado ENSO (`kb_document.enso_state`) se registra al cargar el
    boletín". It is parsed out of the bulletin's own words rather than declared beside
    them, so a bulletin that changes its text changes its stored state — and the corpus
    keeps one owner per fact. Both fixture bulletins carry a labelled state, and each
    parses into docs/03:354's vocabulary."""
    sources = _by_file()

    assert sources[NATIONAL_BULLETIN].document.enso_state == "neutral"
    assert sources[REGIONAL_BULLETIN].document.enso_state == "el_nino"


def test_a_guide_records_no_enso_state() -> None:
    """docs/03:354 marks `enso_state` "solo boletines": the state is a property of the
    *bulletin*, not of a guide that happens to mention the term, so a guide is stored
    with a null state whatever its text says."""
    guide = _by_file()[GUIDE]

    assert guide.document.kind == "guide"
    assert guide.document.enso_state is None


def test_the_digest_is_the_sha256_of_the_document_bytes() -> None:
    """The second half of the ingestion idempotency key (docs/03 §`kb_document`,
    `kb_chunk`, `conversation` y `message`, row `Unicidad de la ingesta`): the digest of
    the bytes, so an unchanged file is recognised on a re-run and a revised one is
    recognised as a revision. The bytes are read as they are on disk — no normalisation
    — because the digest is about *the file*, not about the text this loader happens to
    decode from it."""
    source = _by_file()[GUIDE]
    raw = source.path.read_bytes()

    assert source.sha256 == hashlib.sha256(raw).hexdigest()
    assert source.text == raw.decode("utf-8")


def test_a_manifest_never_declares_the_enso_state(tmp_path: Path) -> None:
    """One owner per fact: `enso_state` is parsed from the document (docs/06:374), so a
    manifest carrying one would be a second source of truth that can disagree with the
    text it describes. A manifest that declares it is refused rather than silently
    ignored."""
    directory = _write_corpus(
        tmp_path / "corpus",
        [_entry("boletin.md", kind="agroclimatic_bulletin", enso_state="el_nino")],
        files={"boletin.md": "Estado ENSO: Neutral."},
    )

    with pytest.raises(CorpusError, match="enso_state"):
        load_corpus(directory)


def test_a_manifest_with_an_unknown_kind_is_refused(tmp_path: Path) -> None:
    """`kind` ∈ {guide, agroclimatic_bulletin} (docs/03:352) is a documented vocabulary,
    so an entry outside it is a manifest that cannot be stored: the loader refuses it
    with the entry's file name rather than letting the CHECK refuse it later, one row at
    a time, inside a job."""
    directory = _write_corpus(
        tmp_path / "corpus",
        [_entry("guia.md", kind="manual")],
        files={"guia.md": "El riego por goteo."},
    )

    with pytest.raises(CorpusError, match="manual"):
        load_corpus(directory)


def test_a_document_file_outside_the_corpus_directory_is_refused(tmp_path: Path) -> None:
    """A corpus directory is trusted input once the owner points at it (ADR-0008:32), and
    `../` in a manifest entry is not a document of that corpus: it is a path out of the
    directory the licence review covered. The entry is refused, and the refusal names the
    offending path."""
    corpus = tmp_path / "corpus"
    secret = tmp_path / "outside.md"
    secret.write_text("no es parte del corpus", encoding="utf-8")
    directory = _write_corpus(
        corpus, [_entry("../outside.md")], files={"guia.md": "El riego por goteo."}
    )

    with pytest.raises(CorpusError, match="outside.md"):
        load_corpus(directory)


def test_a_manifest_without_a_document_file_is_refused(tmp_path: Path) -> None:
    """A missing file is not an empty document: it is a corpus that cannot be loaded, and
    ingesting the rest of it would leave the assistant citing a source it never read."""
    directory = _write_corpus(tmp_path / "corpus", [_entry("ausente.md")])

    with pytest.raises(CorpusError, match="ausente.md"):
        load_corpus(directory)


# -- the ENSO parser itself -----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Estado ENSO: El Niño.", "el_nino"),
        ("Estado ENSO: la Niña.", "la_nina"),
        ("Estado ENSO: Neutral.", "neutral"),
        ("estado del enso - neutro", "neutral"),
        ("ESTADO ENSO: EL NIÑO", "el_nino"),
    ],
)
def test_the_enso_state_is_read_from_a_labelled_line(text: str, expected: str) -> None:
    """Bulletins state the ENSO phase in a labelled line, and that line is what the
    parser reads. Upper case and missing accents are the OCR and formatting variants of
    the same fact, so they are the same state — the parser matches on text with the
    accents removed."""
    assert parse_enso_state(text) == expected


def test_a_labelled_line_wins_over_the_body_text() -> None:
    """A bulletin that reviews the last seasons in its body mentions all three phases; the
    one it *states* is the labelled line. Without this rule the stored state would depend
    on which phrase the parser happened to find first, and the seasonal-climate fact of
    docs/06 §9 would answer with a season the bulletin only recalls."""
    text = (
        "Durante los últimos años el Caribe pasó por El Niño y por La Niña.\n\n"
        "Estado ENSO: Neutral para el trimestre.\n"
    )

    assert parse_enso_state(text) == "neutral"


def test_two_labelled_lines_that_disagree_are_refused() -> None:
    """A bulletin that states two different phases is not a bulletin this parser can
    label. Picking one would store an arbitrary state on the row the seasonal-climate
    fact reads, so the corpus refuses the document and says why."""
    text = "Estado ENSO: El Niño.\n\nEstado ENSO: La Niña.\n"

    with pytest.raises(CorpusError, match="El Niño|La Niña"):
        parse_enso_state(text)


def test_a_document_that_never_states_its_enso_phase_has_none() -> None:
    """`enso_state` is nullable and a missing state is `null`, never a guess (docs/03:354
    admits the three values, and docs/06 §9 reports the state it has). A guide, a
    bulletin whose phase is not stated, and an empty text all return `None`."""
    assert parse_enso_state("El cultivo de maíz en el Caribe.") is None
    assert parse_enso_state("") is None
