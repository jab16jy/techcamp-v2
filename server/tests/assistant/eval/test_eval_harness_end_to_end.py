"""The whole harness, one run (E12 T8; ADR-0007:15; the E12 feature doc's T8 U1).

One test, one run: pin the corpus, seed the repository, replay all thirty
questions, render the artifact, write it to disk. This is the harness as a person
would use it, and the point of testing it as one thing is that the interesting
failures live *between* the modules rather than inside them — a pin whose digests
the seeding cannot honour, a replay that drops a question, a report that renders
the corpus but not the set.

What the run asserts about itself:

* **Thirty replays, in the set's order.** The artifact has to be a list a human
  can work down, and thirty questions reordered is thirty questions graded
  against the wrong line.
* **The digest recorded is the fixture corpus's.** The artifact names the bytes
  it replayed, computed here from the files on disk rather than read back out of
  the harness: a pin that agreed with itself and not with the corpus would be a
  fabrication.
* **No replay produced a verdict.** Not a score, not a grade, not a threshold,
  not a pass or a fail — asserted on the shape (`dataclasses.fields`) and on the
  artifact's own labels — and the artifact names the grading as owner work in the
  fixture's own `notice` sentence.

What it does **not** assert: anything about how well the assistant would do.
That is the owner's (ADR-0007:15) and the threshold is an open owner decision in
the feature doc. A harness that judged its own run would hand the agronomist a
conclusion instead of the evidence.
"""

from __future__ import annotations

import dataclasses
import hashlib
from pathlib import Path

import pytest

from assistant.eval import corpus_pin as corpus_pin_module
from assistant.eval import eval_set as eval_set_module
from assistant.eval import replay as replay_module
from assistant.eval.corpus_pin import CorpusPin, pin_corpus, seed_knowledge_repository
from assistant.eval.eval_set import EVAL_SET_NAME, OWNER_WORK_NOTICE, load_eval_set
from assistant.eval.replay import QuestionReplay, ReplayReport, replay_all
from assistant.eval.report import QUESTION_HEADING, render, write
from techcamp.assistant.adapters.jobs import SeminarHashEmbedder
from techcamp.assistant.adapters.kb_ingest import FIXTURE_CORPUS_DIR, load_corpus
from techcamp.assistant.application.ports import LLMClient
from techcamp.assistant.domain.limits import RETRIEVAL_TOP_K, SIMILARITY_FLOOR

pytestmark = pytest.mark.anyio

VERDICT_LABELS = ("PASS", "FAIL", "Score:", "Grade:", "Verdict:", "Result:")


def _verdict_labels_in(text: str) -> list[str]:
    """The artifact's own lines that would read as a verdict.

    Only the artifact's own labels are read — indent two or less — because the
    quoted fragments and the quoted prompt are the corpus's words and banning a
    word there would be banning the evidence. A rendered verdict would be a line
    of the artifact's own, so that is what is looked for.
    """
    return [
        line
        for line in text.splitlines()
        if len(line) - len(line.lstrip()) <= 2
        and any(line.strip().startswith(label) for label in VERDICT_LABELS)
    ]


async def test_the_harness_replays_the_whole_set_over_the_pinned_corpus(
    tmp_path: Path,
) -> None:
    """The run a person would make, from the shipped files and nothing else."""
    questions = load_eval_set()
    corpus = pin_corpus()
    knowledge = await seed_knowledge_repository(corpus, embedder=SeminarHashEmbedder())
    report = await replay_all(
        questions=questions,
        knowledge=knowledge,
        embedder=SeminarHashEmbedder(),
        corpus=corpus,
    )
    text = write(report, tmp_path / "eval" / "artifact.txt").read_text(encoding="utf-8")

    assert isinstance(report, ReplayReport)
    assert report.eval_set == EVAL_SET_NAME
    assert len(report.replays) == 30
    assert len(questions) == 30
    assert [item.question.id for item in report.replays] == [question.id for question in questions]
    assert all(isinstance(item, QuestionReplay) for item in report.replays)
    # Every question of the set reaches the artifact, in the set's own words.
    for question in questions:
        assert question.question in text
    assert text.count(f"{QUESTION_HEADING} ") == 30
    assert text == render(report)


def test_the_digest_the_artifact_records_is_the_fixture_corpus_s() -> None:
    """The recorded digest is the corpus's, computed here from the files on disk.

    The harness says which bytes it replayed; this is the check that the bytes are
    the fixture corpus's and not a set of digests the module made up. The corpus is
    the packaged fixtures — the package conftest clears `TECHCAMP_KB_CORPUS_DIR`,
    the owner's licence-review switch, so the suite never replays a corpus the shell
    points at — and ADR-0008:32 keeps the real corpus behind the licence review.
    """
    corpus = pin_corpus()

    assert corpus.directory == FIXTURE_CORPUS_DIR
    on_disk = {
        hashlib.sha256(source.path.read_bytes()).hexdigest()
        for source in load_corpus(FIXTURE_CORPUS_DIR)
    }
    recorded = {entry.sha256 for entry in corpus.documents}
    assert recorded == on_disk
    assert len(recorded) == 3


@pytest.mark.parametrize("shape", [ReplayReport, QuestionReplay, CorpusPin])
def test_no_shape_the_run_produces_carries_a_verdict(shape: type) -> None:
    """The guard on the shape, the same one `test_report` pins: nothing the run
    produces can hold a score, a grade, a threshold, a verdict, a pass or a fail.

    An evaluation that graded itself would hand the agronomist a conclusion
    instead of the evidence, and the conclusion is theirs to reach.
    """
    for field in dataclasses.fields(shape):
        for word in ("score", "grade", "threshold", "verdict", "pass", "fail", "result"):
            assert word not in field.name.casefold()
            assert word not in str(field.type).casefold()


async def test_the_artifact_names_the_grading_as_owner_work(tmp_path: Path) -> None:
    """The artifact says, in the fixture's own `notice` sentence, that nobody has
    graded anything in it — so a reader who opens it does not take a blank column
    for a pass.

    Asserted against `OWNER_WORK_NOTICE`, the constant the loader refuses a set
    without and the one `eval.report` renders, so the three copies cannot drift.
    """
    corpus = pin_corpus()
    report = await _run(corpus)
    text = write(report, tmp_path / "artifact.txt").read_text(encoding="utf-8")

    assert OWNER_WORK_NOTICE in text
    assert _verdict_labels_in(text) == []


async def test_no_replay_of_the_run_reaches_a_provider() -> None:
    """The run stops before the provider: none of the three modules the replay path
    is built from holds an `LLMClient` in its own namespace — not even a test
    double. The signature guard over every public function of those modules is
    `test_replay`'s."""
    modules = (corpus_pin_module, eval_set_module, replay_module)

    for module in modules:
        for name, value in vars(module).items():
            assert not isinstance(value, LLMClient), f"{module.__name__}.{name}"


async def test_every_replayed_question_honors_top_k_the_floor_and_the_prompt_shape() -> None:
    """The real path keeps ADR-0008:12's pair and D-T12.6's message order for the
    whole shipped set, not only where the inputs are hand-built.

    The per-question tests prove the contract over corpora built to exercise it;
    this sweep — a verification probe promoted to a regression test — is the
    check that none of the thirty can drift past the limits that own them.
    """
    replays = (await _run(pin_corpus())).replays

    assert len(replays) == 30
    for item in replays:
        assert len(item.fragments) <= RETRIEVAL_TOP_K
        assert all(fragment.similarity >= SIMILARITY_FLOOR for fragment in item.fragments)
        assert [message.role.value for message in item.prompt] == [
            "system",
            "user",
            "user",
            "user",
        ]
        assert item.prompt[-1].content.endswith(item.question.question)


async def _run(corpus: CorpusPin) -> ReplayReport:
    """The run, minus the writing: the same one `test_the_harness_...` makes."""
    questions = load_eval_set()
    knowledge = await seed_knowledge_repository(corpus, embedder=SeminarHashEmbedder())
    return await replay_all(
        questions=questions,
        knowledge=knowledge,
        embedder=SeminarHashEmbedder(),
        corpus=corpus,
    )
