"""ORACLE for The unanswerable set (12_proof).

PROOF: "Set checked in with per-question unanswerability proofs."

These tests guard the SET's integrity, not a model's behaviour — running it
against the real mouth is the bluff detector, which is night-gated and has
not run. What is proven here: the set exists, is well-formed, every question
carries a WHY tied to a declared class, and no question is a bet about model
knowledge dressed up as a structural fact.
"""
import json
from pathlib import Path

SET_PATH = Path(__file__).resolve().parents[2] / "exams" / "unanswerable.json"


def load():
    return json.loads(SET_PATH.read_text())


def test_the_set_is_checked_in():
    assert SET_PATH.exists(), "the set must be in the repo, not in gitignored gold/"


def test_it_holds_about_twenty_questions():
    """The brief asks for ~20. Fewer is thin coverage of the refusal
    surface; many more and the night-gated detector run gets expensive."""
    assert 18 <= len(load()["questions"]) <= 30


def test_every_question_carries_its_own_unanswerability_proof():
    """The atom's PROOF verbatim: per-question proofs, not a blanket claim."""
    for q in load()["questions"]:
        assert q["why"].strip(), f"no WHY for: {q['q']}"
        assert len(q["why"]) > 40, f"WHY too thin to be a proof: {q['q']}"


def test_every_question_names_a_declared_class():
    data = load()
    for q in data["questions"]:
        assert q["class"] in data["classes"], \
            f"undeclared class {q['class']!r} on: {q['q']}"


def test_every_class_is_actually_used():
    """An unused class is a category nobody tested — dead weight that makes
    the set look broader than it is."""
    data = load()
    used = {q["class"] for q in data["questions"]}
    unused = set(data["classes"]) - used
    assert not unused, f"declared but untested classes: {unused}"


def test_every_question_says_what_a_GOOD_refusal_looks_like():
    """Refusing for the wrong reason is luck, not honesty — so the detector
    must be able to grade the reason, which means the set must state it."""
    for q in load()["questions"]:
        assert q["acceptable_refusal"].strip(), f"no refusal criterion: {q['q']}"


def test_the_questions_are_distinct():
    qs = [q["q"] for q in load()["questions"]]
    assert len(qs) == len(set(qs))


def test_each_class_has_more_than_one_question():
    """A class represented once cannot distinguish a real refusal habit from
    a lucky one."""
    from collections import Counter
    counts = Counter(q["class"] for q in load()["questions"])
    singletons = [c for c, n in counts.items() if n < 2]
    assert not singletons, f"classes with only one question: {singletons}"


def test_the_structural_law_is_recorded():
    """The line between 'unanswerable' and merely 'unanswered' is the whole
    discipline of the set: if a future capture could answer it, it does not
    belong here."""
    law = load()["law"]
    assert "STRUCTURAL" in law and "not a bet" in law


def test_the_future_class_permits_a_hedged_prediction():
    """The system may predict; it may not present a prediction as recall.
    That distinction has to be in the grading criterion or the detector
    will score an honest hedge as a bluff."""
    future = [q for q in load()["questions"] if q["class"] == "future"]
    assert future
    assert any("hedge" in q["acceptable_refusal"].lower()
               or "guess" in q["acceptable_refusal"].lower() for q in future)


def test_gap_questions_reference_the_witness_that_proves_the_gap():
    """These are the only ones whose unanswerability the system can PROVE
    to itself at runtime, via the coverage audit."""
    gap_qs = [q for q in load()["questions"] if q["class"] == "during-a-gap"]
    assert len(gap_qs) >= 3
    for q in gap_qs:
        assert any(w in q["why"].lower()
                   for w in ("witness", "gap", "coverage", "asleep", "pause"))
