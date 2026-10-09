"""The artifact: what the harness writes down for a human agronomist (E12 T8;
ADR-0007:15).

`render` turns a `ReplayReport` into text a person can read down with a pen, and
`write` puts it on disk as UTF-8. Both are pure functions of the report: same
report, same bytes, forever. A human comparing two runs has to be comparing the
same artifact, and a rendering that varied on iteration order, on a `set`, on a
`dict` or on float formatting could not be compared at all.

What the artifact carries, per question:

* the ask, in the producer's own words;
* the fragments retrieval resolved, each with the similarity that selected it
  (ADR-0008:12);
* the sources those fragments became — source, licence and publication date,
  because ADR-0008:12 records source **and** licence per document and a grader
  has to be able to open what the answer rested on;
* the **exact** prompt a provider would receive, copied out of `build_prompt`'s
  output and never re-worded.

And above all of it, the sentence that says nobody has graded anything in it.

**Labels are English, content is Spanish.** The labels name the fields of a
report; the content is the corpus's and the producer's own words. Rewriting the
content here would corrupt the evidence (AGENTS.md §Architecture rules), so the
renderer quotes it.

**Nothing here is a verdict.** The similarity is the number that selected a
fragment, which is evidence; there is no average of them, no count of questions
with fragments, no line that reads as a score. Accuracy, citations and clarity
are the agronomist's judgement (ADR-0007:15) and the pass threshold is an open
owner decision in the E12 feature doc — so this module renders the inputs to that
judgement and stops.
"""

from __future__ import annotations

from pathlib import Path

from assistant.eval.corpus_pin import CorpusPin, CorpusPinEntry, fragment_count
from assistant.eval.eval_set import OWNER_WORK_NOTICE
from assistant.eval.replay import QuestionReplay, ReplayReport
from techcamp.assistant.application.ports import RetrievedFragment
from techcamp.assistant.domain.citations import Citation
from techcamp.assistant.domain.models import ChatMessage

QUESTION_HEADING = "Question"
"""The label each question's section starts with, followed by its id: `Question
q01: ¿…?`. A heading rather than a bare number, so a grader and a reviewer point at
the same question by name."""

_INDENT = "  "
"""One level of the artifact's hierarchy."""

_LABEL_WIDTH = 16
"""The column the header values start at. Fixed, so a diff between two runs shows
a change in content and never a change in column width."""

_TEXT_INDENT = "  "
"""The extra level a quoted body sits under its own marker, so the artifact's
hierarchy is readable: a fragment's text under `[1]`, a prompt message's text
under `[system]`."""


def render(report: ReplayReport) -> str:
    """The report as human-readable text, deterministic in the report's order.

    Pure: no clock, no environment, no iteration over an unordered collection.
    Every value comes from a field of a frozen dataclass, so two equal reports
    render the same bytes.

    The corpus pin comes first and the questions after, because a grader reads a
    question with the corpus it was asked against in front of them — the order a
    paper evaluation is read in.
    """
    licences = _licences(report.corpus)
    lines: list[str] = _header(report)
    lines += _pinned_corpus(report.corpus)
    lines += _notice()
    lines += ["Questions", "=" * len("Questions"), ""]
    for item in report.replays:
        lines += _question_section(item, licences)
    return "\n".join(lines) + "\n"


def write(report: ReplayReport, path: Path) -> Path:
    """Write `render(report)` to `path` as UTF-8, and return `path`.

    UTF-8 because the questions and the corpus are Spanish: a mangled "Córdoba"
    is a corrupted citation. Missing parent directories are created, because the
    directory a run writes into is not always there yet.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(report), encoding="utf-8")
    return path


def _header(report: ReplayReport) -> list[str]:
    """Which set, over which corpus, and how much of it.

    The fragment count is the corpus's, not the questions': a reader should know
    how much evidence there was before any question was asked. It is a count of
    the pin and not of what anything retrieved, so it is not a summary of how
    well the run went.
    """
    corpus = report.corpus
    return [
        "TechCamp assistant - evaluation replay (E12 T8)",
        "",
        f"{_pad('Set:')} {report.eval_set}",
        f"{_pad('Corpus:')} {corpus.directory}",
        f"{_pad('Documents:')} {len(corpus.documents)}",
        f"{_pad('Fragments:')} {fragment_count(corpus)}",
        "",
    ]


def _pinned_corpus(corpus: CorpusPin) -> list[str]:
    """Every document the replay ran over: title, kind, source, licence,
    publication date, department, digest and fragment count.

    The digest is what makes the artifact reproducible, so it is on the face of
    it and not in a side file.
    """
    lines: list[str] = ["Pinned corpus", "-" * len("Pinned corpus")]
    for index, entry in enumerate(corpus.documents, 1):
        lines.append(f"{index}. {entry.title}")
        lines.append(f"{_INDENT}{_pad('kind:')} {entry.kind}")
        lines.append(f"{_INDENT}{_pad('source:')} {entry.source_url}")
        lines.append(f"{_INDENT}{_pad('licence:')} {entry.license}")
        lines.append(f"{_INDENT}{_pad('published on:')} {entry.published_on.isoformat()}")
        lines.append(f"{_INDENT}{_pad('department:')} {_department(entry)}")
        lines.append(f"{_INDENT}{_pad('sha256:')} {entry.sha256}")
        lines.append(f"{_INDENT}{_pad('fragments:')} {entry.chunk_count}")
        lines.append("")
    return lines


def _notice() -> list[str]:
    """The owner-work sentence, so the reader is told before the first question
    that nothing here has been graded.

    The same sentence `eval.eval_set.OWNER_WORK_NOTICE` holds and the same one
    the fixture's `notice` carries, so a change to one is a change to the other or
    a failing test. Rendered on one line and never wrapped: the guarantee is that
    *this exact sentence* is in the artifact, and a wrapped copy would be a
    different string to anything looking for it.
    """
    return [OWNER_WORK_NOTICE, ""]


def _question_section(item: QuestionReplay, licences: dict[str, str]) -> list[str]:
    """One question: the ask, the evidence, and the prompt that would be sent."""
    question = item.question
    return [
        f"{QUESTION_HEADING} {question.id}: {question.question}",
        "",
        _block("Retrieved fragments", _fragments(item.fragments)),
        "",
        _block("Cited sources", _citations(item.citations, licences=licences)),
        "",
        _block("Prompt the provider would receive", _prompt(item.prompt)),
        "",
    ]


def _block(label: str, body: list[str]) -> str:
    """A named block with its count in the label, indented once.

    An absent block says so on the label's own line rather than vanishing: a
    section that is not there is indistinguishable from one that was cut, and
    docs/06 §9's "si no hay información suficiente, decirlo" needs the absence to
    be legible to the person reading as well as to the model.
    """
    head, *rest = body
    lines = [f"{_INDENT}{label} ({head}):"]
    lines += [f"{_INDENT}{_INDENT}{line}" if line else "" for line in rest]
    return "\n".join(lines)


def _fragments(fragments: tuple[RetrievedFragment, ...]) -> list[str]:
    """Each fragment, numbered, with the similarity that selected it.

    The content is quoted verbatim and line by line: a fragment is evidence, and
    a renderer that re-wrapped it would be quoting something the corpus does not
    say.
    """
    if not fragments:
        return ["0", "(none)"]
    body = [str(len(fragments))]
    for index, fragment in enumerate(fragments, 1):
        body.append(f"[{index}] similarity {fragment.similarity:.4f}")
        body += [f"{_TEXT_INDENT}{line}" if line else "" for line in fragment.content.splitlines()]
    return body


def _citations(citations: tuple[Citation, ...], *, licences: dict[str, str]) -> list[str]:
    """Each source cited: title, link, licence and publication date.

    The licence is not on `Citation` — it is a `kb_document` field, and
    ADR-0008:12 records it per document — so it is read from the pinned corpus,
    which is the record of what was replayed. A citation the pin cannot match is
    rendered as licence unknown rather than being dropped: a source a grader
    cannot open is still a source they were told about.
    """
    if not citations:
        return ["0", "(none)"]
    body = [str(len(citations))]
    for citation in citations:
        body.append(f"- {citation.title or citation.source}")
        for line in (
            f"source: {citation.source}",
            f"source url: {citation.source_url or '(none recorded)'}",
            f"licence: {_licence_of(citation, licences=licences)}",
            "published on: "
            + (citation.published_on.isoformat() if citation.published_on else "(none recorded)"),
        ):
            body.append(f"{_TEXT_INDENT}{line}")
    return body


def _licences(corpus: CorpusPin) -> dict[str, str]:
    """Every pinned document's licence, keyed by source url and by title.

    Built once per render and read per citation: the licence is a `kb_document`
    field, and the pin is the record of the corpus the citation came from.
    """
    pinned: dict[str, str] = {}
    for entry in corpus.documents:
        pinned.setdefault(entry.source_url, entry.license)
        pinned.setdefault(entry.title, entry.license)
    return pinned


def _licence_of(citation: Citation, *, licences: dict[str, str]) -> str:
    """The licence of the document behind `citation`.

    Matched on the citation's source url first and its title second, because
    `application.retrieval._citation` uses the url when the document recorded one
    and the title when it did not. A citation the pin cannot match is rendered as
    unknown rather than dropped: a source a grader cannot open is still a source
    they were told about.
    """
    for key in (citation.source_url, citation.title, citation.source):
        if key and key in licences:
            return licences[key]
    return "(unknown: the pin holds no document with this source)"


def _prompt(prompt: tuple[ChatMessage, ...]) -> list[str]:
    """The exact messages a provider would receive, role by role, verbatim.

    Copied out of `build_prompt`'s output and never re-worded: what is graded has
    to be what would have been sent, and a prompt re-typeset here would be a
    different prompt.
    """
    body = [str(len(prompt))]
    for message in prompt:
        body.append(f"[{message.role.value}]")
        body += [f"{_TEXT_INDENT}{line}" if line else "" for line in message.content.splitlines()]
    return body


def _department(entry: CorpusPinEntry) -> str:
    """The department, or a note that the document has none.

    `department = null` is how docs/06 §9 tells the national bulletin from a
    regional one, and a guide simply has no department — neither is a blank line
    worth reading as a missing value. Nothing more is claimed: `(no department)`
    is the absence, and calling it `(national)` would be the renderer asserting
    which of the two nulls it is looking at.
    """
    return entry.department if entry.department else "(no department)"


def _pad(label: str) -> str:
    """`label` padded to the fixed column, so the values line up under each other
    and two runs differ only in content."""
    return label.ljust(_LABEL_WIDTH)
