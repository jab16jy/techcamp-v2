"""The question's fragments, and the citations they become (E12 T4; ADR-0008:12;
docs/06-diseno-detallado.md §9; docs/04-api.md §Riesgo, métricas y asistente).

docs/06 §9's flow, third step: "embedding de la pregunta → top 5 fragmentos
(similitud ≥ 0,75)", and then the fragments travel as **data** into the one call
(docs/09-cuellos-de-botella.md §Seguridad: "Los fragmentos RAG y el texto del
usuario van como datos, no como instrucciones"). This use case produces them and
nothing else: no prompt, no facts, no provider.

Four rules, each one a way a retrieval that "works" still fails a producer:

* **The two numbers are named constants**, not literals in a query
  (`domain.limits.RETRIEVAL_TOP_K`, `SIMILARITY_FLOOR`), so ADR-0008:12's pair
  is changed in one place and never drifts into a query string.
* **The floor is re-applied after the call.** The port documents that it filters,
  and a use case that trusted that document would let a repository bug decide what
  the producer reads. Re-filtering costs a comparison and is the only thing that
  makes "0.74 never reaches the answer" a property of this module rather than of
  the adapter.
* **Fewer than `top_k` is an answer.** A corpus holding three qualifying fragments
  has three, and padding the list with fragments below the floor would be inventing
  relevance.
* **A fragment whose document is gone is dropped.** `Citation` requires a
  `source` and a document is what a source is (ADR-0008:12 records source **and**
  licence per document); a fragment with no document cannot be cited, and text
  with no source behind it is not evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from techcamp.assistant.application.ports import (
    Embedder,
    KnowledgeDocument,
    KnowledgeRepository,
    RetrievedFragment,
)
from techcamp.assistant.domain.citations import Citation, CitationKind, validate_citation
from techcamp.assistant.domain.limits import RETRIEVAL_TOP_K, SIMILARITY_FLOOR

#: The two `kb_document.kind` values `CitationKind` names, mapped to it. A kind
#: outside this map is **not** an error: `kb_document.kind` is a column another
#: migration could widen, and one document with a kind this version has never seen
#: must not fail a producer's whole question. It is cited without a `kind`, which
#: is missing evidence rather than a claim.
_KINDS: dict[str, CitationKind] = {
    "guide": CitationKind.GUIDE,
    "agroclimatic_bulletin": CitationKind.AGROCLIMATIC_BULLETIN,
}


@dataclass(frozen=True, slots=True)
class RetrievedEvidence:
    """The fragments a question retrieved, and the citations they became.

    Both travel together because they answer one question from two sides: the
    **fragments** are the text the prompt carries as data (T5), and the
    **citations** are the sources the answer names (docs/04-api.md §Riesgo, métricas
    y asistente). The degraded answer (docs/06 §9: "estos mismos hechos junto con
    los documentos relevantes") reads only the citations, so a retrieval that
    returned fragments without resolving their documents would hand it documents it
    does not have.
    """

    fragments: tuple[RetrievedFragment, ...]
    citations: tuple[Citation, ...]


async def retrieve_fragments(
    *,
    question: str,
    embedder: Embedder,
    knowledge: KnowledgeRepository,
    top_k: int = RETRIEVAL_TOP_K,
    similarity_floor: float = SIMILARITY_FLOOR,
) -> RetrievedEvidence:
    """The `top_k` closest fragments of the corpus to `question`, and their
    citations (ADR-0008:12; docs/06-diseno-detallado.md §9).

    Takes **no LLM port**, the same argument `application.usage` makes for the
    budget branch: this function has no way to reach a provider, so retrieval can
    neither call one nor be the step that degrades an answer.

    The order is the repository's: the port promises best-first, and this use case
    does not re-sort it. A second ordering would either contradict the port or need
    a tie-break of its own, and an order a caller cannot reproduce is an order it
    cannot test.

    Fewer than `top_k` fragments, or none at all, is a legitimate answer: the
    corpus may hold nothing close enough, and that is evidence to report as
    missing rather than a failure to retry.
    """
    embedding = (await embedder.embed([question]))[0]
    found = await knowledge.search_by_embedding(
        embedding, top_k=top_k, similarity_floor=similarity_floor
    )

    fragments: list[RetrievedFragment] = []
    citations: list[Citation] = []
    cited: set[UUID] = set()
    for fragment in found:
        # Re-applied here and not trusted to the port: a 0.74 fragment is not
        # evidence, and a repository that ignored the floor must not decide what a
        # producer reads.
        if fragment.similarity < similarity_floor:
            continue
        document = await knowledge.get_document(fragment.document_id)
        if document is None:
            # Uncitable: a fragment whose document is gone is dropped rather than
            # sent as text with no source behind it.
            continue
        fragments.append(fragment)
        if fragment.document_id in cited:
            # One document is one source even when two of its chunks matched; the
            # score shown is the best one, which is the fragment just read.
            continue
        cited.add(fragment.document_id)
        citations.append(_citation(document, similarity=fragment.similarity))

    return RetrievedEvidence(fragments=tuple(fragments), citations=tuple(citations))


def _citation(document: KnowledgeDocument, *, similarity: float) -> Citation:
    """One citation of a corpus document, validated before it is returned.

    `source` is the document's `source_url` when the corpus recorded one — the link
    a reader can open is the provenance `ADR-0008:12` asks for — and the title when
    it did not. `kb_document` has **no institution column**
    (docs/03-modelo-datos.md §`kb_document`, `kb_chunk`, `conversation` y `message`),
    so the URL is the only provenance the table stores and the title is the honest
    fallback; `Citation.source` is required and a citation without one is not a
    citation.

    `similarity` is the fragment's own score, not the document's best: this function
    is called once per *new* document, with the first (best) fragment that reached
    it, and that is the score the answer leaned on.
    """
    citation = Citation(
        source=document.source_url if document.source_url else document.title,
        title=document.title,
        kind=_KINDS.get(document.kind),
        source_url=document.source_url,
        published_on=document.published_on,
        similarity=similarity,
    )
    validate_citation(citation)
    return citation
