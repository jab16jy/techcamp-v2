"""The closed question set (E12 T8; ADR-0007:15; the E12 feature doc's T8).

ADR-0007:15 asks for "30 preguntas agronómicas reales del Caribe, calificadas
por un agrónomo según exactitud, citas y claridad". This module owns the
**questions and nothing else**: it loads them, it refuses a set it cannot replay,
and it is silent about what a good answer would be. There is no expected answer,
no rubric and no key anywhere in here, and the guard below pins that as a
property of the shape rather than as a promise.

The file it reads is a fixture: `fixtures/eval/questions.json`. Those questions
were written for E12 T8 from the fixture corpus and the glossary, and they are
placeholders for the producer-collected set. The corpus behind them is the
fixture corpus until the licence review is answered (ADR-0008:32).

**What a refused set means.** A set with the wrong size, a duplicated id, a
duplicated question, a blank value or a key nobody declared is not a set this
harness can replay twice and get the same artifact, which is the one property the
harness exists to have. Every refusal is `EvalSetError`, named by the entry that
refused it, so the fix is in the file and not in the loader.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

EVAL_SET_NAME = "e12-t8-caribe-30"
"""The one set this harness ships and replays.

`questions.json` declares the same name, and the loader refuses a file that
declares another: a set this harness does not own would be replayed into an
artifact that names a set nobody can reproduce."""

EVAL_SET_SIZE = 30
"""How many questions the set holds, the ADR-0007:15 count."""

QUESTION_KEYS: frozenset[str] = frozenset({"id", "question"})
"""The whole shape of one question: which one it is, and what it asks.

The guard in `tests/assistant/eval/test_eval_set.py` asserts this set is exactly
these two keys. **Nothing else may join it** — not an expected answer, not a
rubric, not a hint of what a good answer would say. A question that carried one
would let a future reader read a grade out of the fixture, and grading is the
owner's (ADR-0007:15, and the pass threshold is an open owner decision in the
E12 feature doc)."""

GRADING_KEYS: frozenset[str] = frozenset(
    {"expected", "answer", "grade", "score", "threshold", "rubric", "verdict", "pass", "fail"}
)
"""The shapes a question must never carry, named so a review can grep for them
instead of trusting that nobody wrote one."""

OWNER_WORK_NOTICE = (
    "Grading (accuracy, citations, clarity per ADR-0007:15) and the pass threshold are "
    "owner decisions and are deliberately absent from every file here."
)
"""The sentence that says, in plain English, who owns the verdict.

It belongs in the fixture's `notice` **and** in the artifact `eval.report`
renders, so the agronomist who opens the artifact is told before reading a line
of it that nothing in it is a verdict. The loader refuses a `notice` without it
and the end-to-end test asserts both copies say the same thing."""

TOP_LEVEL_KEYS: frozenset[str] = frozenset({"eval_set", "notice", "questions"})
"""The whole shape of the file: which set it is, what it warns about, and the
questions. An undeclared top-level key is a refusal, for the same reason an
undeclared question key is — the harness replays what it can name."""

FIXTURES_DIR = (Path(__file__).resolve().parent.parent / "fixtures" / "eval").resolve()
"""Where the shipped set lives, resolved from this file rather than named as a
path: a package is imported from wherever it is installed, and an absolute path
in source would only ever work on the workstation that wrote it
(`kb_ingest.FIXTURE_CORPUS_DIR` for the same reason)."""

QUESTIONS_FILE_NAME = "questions.json"


class EvalSetError(RuntimeError):
    """A question set this harness refuses to replay, named by the entry that
    refused it.

    One error for every refusal the loader makes, because they are the same
    failure from the set's side: a file that cannot be replayed twice and get the
    same artifact back. A `RuntimeError` and not a domain error on purpose —
    nothing in `assistant.domain` decides what an evaluation fixture may say."""


@dataclass(frozen=True, slots=True)
class EvalQuestion:
    """One question of the closed set: its id and the producer's own words.

    `id` exists so a human grader and an artifact can point at a question without
    repeating its text, and so the replay order is a property of the file rather
    than of a sort. `question` is the whole ask: a single question, in plain
    Spanish, with the vocabulary of the glossary.

    **No answer, no rubric, no score, no threshold.** The two fields are the
    whole dataclass (see `QUESTION_KEYS`): the harness hands the agronomist what
    the assistant would be asked, and the agronomist judges the answer.
    """

    id: str
    question: str


def questions_file(directory: Path | None = None) -> Path:
    """The set file of `directory`, or the shipped one (`FIXTURES_DIR`)."""
    return (FIXTURES_DIR if directory is None else Path(directory)) / QUESTIONS_FILE_NAME


def load_eval_set(directory: Path | None = None) -> tuple[EvalQuestion, ...]:
    """Every question of the set in `directory`, in file order.

    The order the file declares is the order the harness replays and the order
    the artifact renders, so a reader comparing two runs compares the same
    question at the same line. The return is a `tuple` of frozen dataclasses, so
    two loads are equal and `report.render` is byte-identical for equal reports.

    Refuses — with `EvalSetError`, naming the entry that refused — a set whose
    size is not `EVAL_SET_SIZE`, whose ids or question texts repeat, whose id or
    question is not a non-blank string, which carries a key nobody declared, or
    whose `notice` does not say the grading is owner work.
    """
    path = questions_file(directory)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise EvalSetError(f"eval set: {path} cannot be read: {exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EvalSetError(f"eval set: {path} is not readable JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise EvalSetError(f"eval set: {path} is not a JSON object, so it declares no set")
    _refuse_unknown_keys(path, parsed, TOP_LEVEL_KEYS, where="the set")
    for key in ("eval_set", "notice", "questions"):
        if not isinstance(parsed.get(key), str if key != "questions" else list):
            raise EvalSetError(
                f"eval set: {path} has no {key!r} of the right type, so it declares no set"
            )
    if parsed["eval_set"] != EVAL_SET_NAME:
        raise EvalSetError(
            f"eval set: {path} declares {parsed['eval_set']!r}, and this harness owns "
            f"{EVAL_SET_NAME!r}"
        )
    if OWNER_WORK_NOTICE not in parsed["notice"]:
        raise EvalSetError(
            f"eval set: {path} carries a notice that does not say the grading and the pass "
            "threshold are owner decisions; the artifact must name that work as owner work"
        )

    entries = parsed["questions"]
    if len(entries) != EVAL_SET_SIZE:
        raise EvalSetError(
            f"eval set: {path} holds {len(entries)} questions and ADR-0007:15 fixes {EVAL_SET_SIZE}"
        )

    questions: list[EvalQuestion] = []
    seen_ids: dict[str, int] = {}
    seen_texts: dict[str, str] = {}
    for position, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise EvalSetError(f"eval set: {path} question {position} is not an object")
        _refuse_unknown_keys(path, entry, QUESTION_KEYS, where=f"question {position}")

        identifier = entry["id"]
        if not isinstance(identifier, str) or not identifier.strip():
            raise EvalSetError(f"eval set: {path} question {position} has a blank or non-string id")
        if not isinstance(entry["question"], str) or not entry["question"].strip():
            raise EvalSetError(
                f"eval set: {path} question {identifier!r} has a blank or non-string question"
            )
        if identifier in seen_ids:
            raise EvalSetError(
                f"eval set: {path} repeats the id {identifier!r} at positions "
                f"{seen_ids[identifier]} and {position}"
            )
        if entry["question"] in seen_texts:
            raise EvalSetError(
                f"eval set: {path} asks the same question twice: {identifier!r} and "
                f"{seen_texts[entry['question']]!r}"
            )

        seen_ids[identifier] = position
        seen_texts[entry["question"]] = identifier
        questions.append(EvalQuestion(id=identifier, question=entry["question"]))

    return tuple(questions)


def _refuse_unknown_keys(
    path: Path, mapping: Mapping[str, object], allowed: Iterable[str], *, where: str
) -> None:
    """Refuse `mapping` when it carries a key outside `allowed`.

    The check is on the way in rather than a rule a reviewer enforces, because the
    reason a key must be declared is mechanical: `build_prompt` and
    `retrieve_fragments` read what the set names, and an undeclared key is either
    dead weight nobody replays or a shape nobody validated.
    """
    permitted = frozenset(allowed)
    for key in mapping:
        if key not in permitted:
            raise EvalSetError(f"eval set: {path} {where} carries the undeclared key {key!r}")
