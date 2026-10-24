"""The closed question set, and the refusals that keep it replayable (E12 T8;
ADR-0007:15).

ADR-0007:15 asks for 30 real Caribbean agronomic questions graded by an
agronomist on accuracy, citations and clarity. The **questions** are this
module's business; the grading is the owner's, and the pass threshold is an open
owner decision in the E12 feature doc. So the set of tests here proves two
opposite things about the same file:

* **It loads, and it loads the same way every time** — 30 questions, in file
  order, unique ids and unique texts. A replay that changed its own questions
  between runs could not produce two artifacts a human could compare.
* **It refuses everything it cannot replay** — wrong size, a repeated id or text,
  a blank value, a value of the wrong type, an undeclared key on a question or at
  the top level, or a `notice` that does not say the grading is owner work.

The last test is the one that matters most for a future maintainer: the permitted
shape of a question is **exactly** `{"id", "question"}`, and none of the
grading-shaped keys (`expected`, `answer`, `grade`, `score`, `threshold`,
`rubric`, `verdict`, `pass`, `fail`) is in it. A fixture that carried one would
let a reader read a verdict out of the harness, and the harness must never carry
one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assistant.eval.eval_set import (
    EVAL_SET_NAME,
    EVAL_SET_SIZE,
    FIXTURES_DIR,
    GRADING_KEYS,
    OWNER_WORK_NOTICE,
    QUESTION_KEYS,
    TOP_LEVEL_KEYS,
    EvalQuestion,
    EvalSetError,
    load_eval_set,
)

# --- the shipped set -----------------------------------------------------


def test_the_shipped_set_holds_the_thirty_questions_of_adr_0007() -> None:
    """ADR-0007:15 fixes the count. A set of 29 or 31 is not the evaluation the
    decision describes, so the harness refuses rather than replaying a different
    one."""
    questions = load_eval_set()

    assert len(questions) == EVAL_SET_SIZE == 30
    assert len({question.id for question in questions}) == len(questions)


def test_the_shipped_set_loads_in_file_order() -> None:
    """The file's order is the replay's order and the artifact's order, so a
    reader comparing two runs reads the same question at the same line. The ids
    are `q01`…`q30`, and the assertion is on the file's own sequence rather than
    on a re-sort: this harness never reorders a question set."""
    questions = load_eval_set()

    assert [question.id for question in questions] == [f"q{index:02d}" for index in range(1, 31)]


def test_the_shipped_set_asks_thirty_different_questions() -> None:
    """A repeated question would grade the same thing twice and let one hard
    question count as two. Unique ids are not enough: the same question under
    two ids is still the same question."""
    questions = load_eval_set()

    assert len({question.question for question in questions}) == len(questions)


def test_loading_the_same_set_twice_gives_equal_tuples() -> None:
    """The replay's determinism starts here: the set is a `tuple` of frozen
    dataclasses, so two loads are equal and the rendered artifact is
    byte-identical for equal reports."""
    first = load_eval_set()
    second = load_eval_set()

    assert first == second
    assert first is not second


def test_every_shipped_question_is_a_id_and_a_question_and_nothing_else() -> None:
    """The shape guard, read off the shipped file rather than off a temporary one.
    Each raw object is checked for the grading-shaped keys too, so a fixture that
    grew one is caught here and not only by the loader's refusal."""
    raw = json.loads((FIXTURES_DIR / "questions.json").read_text(encoding="utf-8"))

    assert raw["eval_set"] == EVAL_SET_NAME
    assert set(raw) == set(TOP_LEVEL_KEYS)
    assert len(raw["questions"]) == EVAL_SET_SIZE
    for entry in raw["questions"]:
        assert set(entry) == set(QUESTION_KEYS) == {"id", "question"}
        assert GRADING_KEYS.isdisjoint(entry)


# --- the temporary sets the refusals are proved with ---------------------


def _payload(**overrides: object) -> dict[str, object]:
    """A minimal valid set, with `overrides` applied on top of it."""
    base: dict[str, object] = {
        "eval_set": EVAL_SET_NAME,
        "notice": f"Fixture questions for E12 T8. {OWNER_WORK_NOTICE}",
        "questions": [{"id": "q01", "question": "¿Qué es el riego por goteo?"}],
    }
    base.update(overrides)
    return base


def _write(directory: Path, payload: object) -> Path:
    path = directory / "questions.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _questions(count: int) -> list[dict[str, object]]:
    """Ids `q01`…`q{count}`, one question each, in file order.

    Typed to hold anything, because the refusal tests below deliberately break a
    value's type and the shape they build is not the shape a valid set has.
    """
    return [
        {"id": f"q{index:02d}", "question": f"¿Pregunta {index}?"} for index in range(1, count + 1)
    ]


def test_a_set_of_the_right_size_loads_from_a_directory_of_its_own(tmp_path: Path) -> None:
    """The loader reads a directory, not a hard-coded path: the owner's
    producer-collected set replaces the fixture by replacing a file."""
    _write(tmp_path, _payload(questions=_questions(EVAL_SET_SIZE)))

    questions = load_eval_set(tmp_path)

    assert [question.id for question in questions] == [f"q{index:02d}" for index in range(1, 31)]
    assert all(isinstance(question, EvalQuestion) for question in questions)


def test_a_missing_set_is_refused_and_not_an_empty_one(tmp_path: Path) -> None:
    """An absent file is a refusal, never a set of no questions: an empty
    artifact is indistinguishable from a run that found nothing, and the harness
    must not produce one by accident."""
    with pytest.raises(EvalSetError):
        load_eval_set(tmp_path / "no-such-directory")


def test_a_file_that_is_not_json_is_refused(tmp_path: Path) -> None:
    (tmp_path / "questions.json").write_text("not json", encoding="utf-8")

    with pytest.raises(EvalSetError):
        load_eval_set(tmp_path)


def test_a_set_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, [{"id": "q01", "question": "¿Qué?"}])

    with pytest.raises(EvalSetError):
        load_eval_set(tmp_path)


# --- size, ids and texts -------------------------------------------------


def test_a_set_with_one_question_too_few_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, _payload(questions=_questions(EVAL_SET_SIZE - 1)))

    with pytest.raises(EvalSetError):
        load_eval_set(tmp_path)


def test_a_set_with_one_question_too_many_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, _payload(questions=_questions(EVAL_SET_SIZE + 1)))

    with pytest.raises(EvalSetError):
        load_eval_set(tmp_path)


def test_a_repeated_id_is_refused(tmp_path: Path) -> None:
    questions = _questions(EVAL_SET_SIZE)
    questions[7]["id"] = questions[3]["id"]
    _write(tmp_path, _payload(questions=questions))

    with pytest.raises(EvalSetError):
        load_eval_set(tmp_path)


def test_a_repeated_question_is_refused(tmp_path: Path) -> None:
    """Two ids asking the same thing is the same question twice: one hard
    question would count as two, and a grader reading the artifact would not
    notice."""
    questions = _questions(EVAL_SET_SIZE)
    questions[5]["question"] = questions[2]["question"]
    _write(tmp_path, _payload(questions=questions))

    with pytest.raises(EvalSetError):
        load_eval_set(tmp_path)


@pytest.mark.parametrize("blank", ["", "   ", "\t\n "])
def test_a_blank_id_is_refused(tmp_path: Path, blank: str) -> None:
    """A blank id is a label nobody can point at. Whitespace counts as blank: an
    id of spaces reads as an id on screen and is empty in every tool."""
    questions = _questions(EVAL_SET_SIZE)
    questions[4]["id"] = blank
    _write(tmp_path, _payload(questions=questions))

    with pytest.raises(EvalSetError):
        load_eval_set(tmp_path)


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_a_blank_question_is_refused(tmp_path: Path, blank: str) -> None:
    questions = _questions(EVAL_SET_SIZE)
    questions[4]["question"] = blank
    _write(tmp_path, _payload(questions=questions))

    with pytest.raises(EvalSetError):
        load_eval_set(tmp_path)


# --- types ---------------------------------------------------------------


def test_a_non_string_id_is_refused(tmp_path: Path) -> None:
    questions = _questions(EVAL_SET_SIZE)
    questions[4]["id"] = 7
    _write(tmp_path, _payload(questions=questions))

    with pytest.raises(EvalSetError):
        load_eval_set(tmp_path)


def test_a_non_string_question_is_refused(tmp_path: Path) -> None:
    questions = _questions(EVAL_SET_SIZE)
    questions[4]["question"] = {"text": "¿Qué?"}
    _write(tmp_path, _payload(questions=questions))

    with pytest.raises(EvalSetError):
        load_eval_set(tmp_path)


def test_a_null_question_is_refused(tmp_path: Path) -> None:
    questions = _questions(EVAL_SET_SIZE)
    questions[4]["question"] = None
    _write(tmp_path, _payload(questions=questions))

    with pytest.raises(EvalSetError):
        load_eval_set(tmp_path)


def test_a_question_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    questions = _questions(EVAL_SET_SIZE)
    broken: list[object] = list(questions)
    broken[4] = "¿Pregunta sin id?"
    _write(tmp_path, _payload(questions=broken))

    with pytest.raises(EvalSetError):
        load_eval_set(tmp_path)


# --- undeclared keys -----------------------------------------------------


def test_an_undeclared_key_on_a_question_is_refused(tmp_path: Path) -> None:
    questions = _questions(EVAL_SET_SIZE)
    questions[9]["topic"] = "riego"
    _write(tmp_path, _payload(questions=questions))

    with pytest.raises(EvalSetError):
        load_eval_set(tmp_path)


def test_an_undeclared_key_at_the_top_level_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, _payload(questions=_questions(EVAL_SET_SIZE), version=2))

    with pytest.raises(EvalSetError):
        load_eval_set(tmp_path)


def test_a_top_level_key_of_the_wrong_type_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, _payload(questions=_questions(EVAL_SET_SIZE), eval_set=12))

    with pytest.raises(EvalSetError):
        load_eval_set(tmp_path)


def test_a_missing_notice_is_refused(tmp_path: Path) -> None:
    payload = _payload(questions=_questions(EVAL_SET_SIZE))
    del payload["notice"]
    _write(tmp_path, payload)

    with pytest.raises(EvalSetError):
        load_eval_set(tmp_path)


def test_a_notice_that_does_not_name_the_grading_as_owner_work_is_refused(
    tmp_path: Path,
) -> None:
    """The artifact the agronomist opens must say, in the file's own words, that
    nobody has graded anything yet. A notice without that sentence would let the
    artifact read as a verdict."""
    _write(tmp_path, _payload(questions=_questions(EVAL_SET_SIZE), notice="Fixture questions."))

    with pytest.raises(EvalSetError):
        load_eval_set(tmp_path)


def test_a_set_this_harness_does_not_own_is_refused(tmp_path: Path) -> None:
    """The report names the set it replayed. A file declaring another name would
    put a set in the artifact that nobody can reproduce."""
    _write(
        tmp_path,
        _payload(eval_set="otro-conjunto", questions=_questions(EVAL_SET_SIZE)),
    )

    with pytest.raises(EvalSetError):
        load_eval_set(tmp_path)


# --- the shape a question may never take ---------------------------------


def test_the_permitted_key_set_of_a_question_is_exactly_id_and_question() -> None:
    """The guard, as a value rather than as a rule: a question is which one it is
    and what it asks, and `QUESTION_KEYS` is the whole vocabulary.

    Nothing here holds an expected answer, a rubric, a score, a threshold or a
    verdict. ADR-0007:15 sends those to an agronomist, and the pass threshold is
    an open owner decision in the E12 feature doc, so the harness cannot carry
    one even honestly."""
    assert QUESTION_KEYS == {"id", "question"}


def test_no_grading_shaped_key_is_in_the_permitted_set() -> None:
    """The keys a future maintainer might reach for, named and excluded. Grep
    reviews find keys; this finds them mechanically."""
    assert GRADING_KEYS.isdisjoint(QUESTION_KEYS)
    assert GRADING_KEYS == {
        "expected",
        "answer",
        "grade",
        "score",
        "threshold",
        "rubric",
        "verdict",
        "pass",
        "fail",
    }


@pytest.mark.parametrize("grading_key", sorted(GRADING_KEYS))
def test_every_grading_shaped_key_on_a_question_is_refused(
    tmp_path: Path, grading_key: str
) -> None:
    """Parametrised over the whole vocabulary: any one of them appearing on a
    question is a refusal, so the guard holds for the key nobody has tried yet."""
    questions = _questions(EVAL_SET_SIZE)
    questions[3][grading_key] = "algo"
    _write(tmp_path, _payload(questions=questions))

    with pytest.raises(EvalSetError):
        load_eval_set(tmp_path)
