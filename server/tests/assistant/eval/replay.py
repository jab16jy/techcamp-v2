"""The replay: the deterministic half of the assistant flow, stopped before the
provider (E12 T8; docs/06-diseno-detallado.md §9; ADR-0007:15).

The harness exists to hand a human agronomist exactly what must be graded — the
question, the fragments the corpus offered, the citations they become, and the
exact prompt a provider would receive. So this module composes the two **real**
steps of docs/06 §9 and stops:

    question ──embed──▶ retrieve_fragments ──▶ citations ──▶ build_prompt ──▶ (stop)

`retrieval.retrieve_fragments` and `adapters.api.prompt.build_prompt` are called
as they are, with their own defaults: `top_k` and the similarity floor are
`domain.limits`' (`RETRIEVAL_TOP_K`, `SIMILARITY_FLOOR`), and the harness passes
neither. A second copy of those numbers here would be a second place for
ADR-0008:12's pair to drift.

**No provider is reachable.** `replay_question` takes an embedder and a knowledge
repository and nothing else — no `LLMClient` parameter, no module global holding
one, not even a test double. A structural guard in
`tests/assistant/eval/test_replay.py` iterates the package's namespaces and
asserts it. The answer itself is the provider's, and what the answer was worth is
the agronomist's; the harness's whole job is to make those two things possible
and to stay out of them.

`ReplayReport` carries no score, no grade, no threshold and no verdict. That
absence is asserted over `dataclasses.fields` in
`tests/assistant/eval/test_report.py`, not trusted.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from assistant.eval.corpus_pin import CorpusPin
from assistant.eval.eval_set import EVAL_SET_NAME, EvalQuestion
from techcamp.assistant.adapters.api.prompt import build_prompt
from techcamp.assistant.application.ports import (
    Embedder,
    KnowledgeRepository,
    RetrievedFragment,
)
from techcamp.assistant.application.retrieval import retrieve_fragments
from techcamp.assistant.domain.citations import Citation
from techcamp.assistant.domain.facts import PlotFacts
from techcamp.assistant.domain.models import ChatMessage

__all__ = ["QuestionReplay", "ReplayReport", "replay_all", "replay_question"]


@dataclass(frozen=True, slots=True)
class QuestionReplay:
    """What one question produced, and nothing more than what a grader needs.

    Four fields, each one half of the grading:

    * `question` — the ask, in the producer's own words.
    * `fragments` — the corpus text retrieval resolved, with its similarity, in
      the order the port returned it.
    * `citations` — the sources those fragments became (source, licence,
      publication date), so a grader can open what the answer rested on
      (ADR-0008:12).
    * `prompt` — the exact four messages a provider would receive, built by the
      real `build_prompt` (D-T12.6).

    **No score, no grade, no threshold, no verdict.** Nothing here says whether
    the fragments were *good*: accuracy, citations and clarity are the
    agronomist's judgement (ADR-0007:15), and the pass threshold is an open owner
    decision in the E12 feature doc. A replay that carried one would let a reader
    take the harness's word for it.
    """

    question: EvalQuestion
    fragments: tuple[RetrievedFragment, ...]
    citations: tuple[Citation, ...]
    prompt: tuple[ChatMessage, ...]


@dataclass(frozen=True, slots=True)
class ReplayReport:
    """The whole replay: which set, over which corpus, with one entry per
    question in the set's order.

    `eval_set` names the set that was replayed and `corpus` is the pin that says
    which bytes it was replayed over, so two artifacts can be told apart and one
    can be reproduced. `replays` is in the set's file order, which is the order
    `eval.report` renders.

    Three fields and no fourth. In particular no aggregate of any kind: a
    summary line — "questions with fragments", "mean similarity" — would be one
    step from a score, and the score is the agronomist's to write.
    """

    eval_set: str
    corpus: CorpusPin
    replays: tuple[QuestionReplay, ...] = field(default=())


async def replay_question(
    question: EvalQuestion,
    knowledge: KnowledgeRepository,
    *,
    embedder: Embedder,
    facts: PlotFacts | None = None,
) -> QuestionReplay:
    """Replay one question over `knowledge`: the real retrieval, the real prompt.

    `retrieve_fragments` is called with its own defaults — `top_k` and the
    similarity floor are `domain.limits`' (`RETRIEVAL_TOP_K`, `SIMILARITY_FLOOR`)
    and the harness passes neither, so ADR-0008:12's pair is read from the one
    place that owns it rather than from a copy here.

    `facts` is `None` unless a caller supplies some, because the evaluation
    replays questions and docs/04-api.md:227 makes `plot_id` optional: a question
    that names no plot is replayed without one, and `build_prompt` still emits
    the fact block saying so.

    **Returns and stops.** There is no provider call, no answer and no verdict;
    the answer is U2's, and the verdict is the agronomist's.
    """
    evidence = await retrieve_fragments(
        question=question.question, embedder=embedder, knowledge=knowledge
    )
    return QuestionReplay(
        question=question,
        fragments=evidence.fragments,
        citations=evidence.citations,
        prompt=build_prompt(facts=facts, evidence=evidence, question=question.question),
    )


async def replay_all(
    *,
    questions: Sequence[EvalQuestion],
    knowledge: KnowledgeRepository,
    embedder: Embedder,
    corpus: CorpusPin,
    facts: PlotFacts | None = None,
    eval_set: str = EVAL_SET_NAME,
) -> ReplayReport:
    """Replay every question of `questions` over `knowledge`, in the order given.

    The order is the caller's, which is the set's file order: the artifact has to
    read as a list a human can work down, and a re-sorted one would put a
    different question on the same line of two runs.

    `eval_set` names the set in the report and defaults to `EVAL_SET_NAME`, the
    one set `eval.eval_set.load_eval_set` will load. A producer-collected set
    that replaced the fixture passes its own name so the artifact does not claim
    a set it is not.

    Serial, not concurrent: the repository is an in-memory double and the
    embedder is local, so there is nothing to overlap, and a serial replay is
    the reproducible one.
    """
    return ReplayReport(
        eval_set=eval_set,
        corpus=corpus,
        replays=tuple(
            [
                await replay_question(question, knowledge, embedder=embedder, facts=facts)
                for question in questions
            ]
        ),
    )
