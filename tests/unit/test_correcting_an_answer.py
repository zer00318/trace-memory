"""Saying "that's wrong" to the thing you just read.

`Ask/'That's wrong'` was a recorded law with no path to reach it. The grade
machinery resolved a line id against the last DIGEST only, so the founder
could correct what the night reported and NOT the answer in front of him —
the one surface anybody actually uses was the one surface with no way to
push back.

What must hold:

  · an answer that was SHOWN becomes gradeable, and by an id that names it;
  · an abstention does not — "not in memory" is a statement about the store,
    not about a life, and grading it would write "the founder says this is
    WRONG: not in memory" into the store as top-graded evidence;
  · a grade can never name something that was never shown, or it puts words
    in someone's mouth at the highest grade in the store;
  · the registry is bounded, because this daemon is meant to run for years.
"""
from __future__ import annotations

import pytest

from tracemem.app.digest import ANSWER_SECTION, GRADEABLE_SECTIONS, SECTIONS


class Answer:
    def __init__(self, text, citations=(), abstained=False):
        self.text = text
        self.citations = citations
        self.abstained = abstained


class Brainish:
    """The registry half of Brain, without a store or an owner thread."""

    from tracemem.app.brain import Brain
    GRADEABLE_MEMORY = Brain.GRADEABLE_MEMORY
    _remember = Brain._remember
    remember_answer = Brain.remember_answer

    def __init__(self):
        from collections import OrderedDict
        self._gradeable = OrderedDict()


def test_an_answer_becomes_gradeable():
    b = Brainish()
    lid = b.remember_answer("who is Juno?",
                            Answer("Juno is a person.", ("abc123",)))
    assert lid and lid in b._gradeable
    line = b._gradeable[lid]
    assert line.section == ANSWER_SECTION
    assert "Juno is a person." in line.text
    assert line.citations == ("abc123",)


def test_the_question_is_part_of_what_was_said():
    """The same sentence can be a right answer to one question and a wrong
    answer to another. A grade must not migrate between them."""
    b = Brainish()
    a = b.remember_answer("who is Juno?", Answer("A person."))
    c = b.remember_answer("who is Hana?", Answer("A person."))
    assert a != c
    assert "who is Juno?" in b._gradeable[a].text


def test_an_abstention_is_not_gradeable():
    """Marking "not in memory" wrong would write "the founder says this is
    WRONG: not in memory" into the store at VERBATIM — a sentence about
    nothing that then outranks everything."""
    b = Brainish()
    assert b.remember_answer("x?", Answer("not in memory", (), True)) is None
    assert b.remember_answer("x?", Answer("")) is None
    assert b.remember_answer("x?", Answer("   ")) is None
    assert b._gradeable == {}


def test_the_registry_is_bounded():
    b = Brainish()
    for i in range(Brainish.GRADEABLE_MEMORY + 40):
        b.remember_answer("q%d" % i, Answer("answer %d" % i))
    assert len(b._gradeable) == Brainish.GRADEABLE_MEMORY
    # The oldest fell off, the newest survived.
    assert not any("answer 0 " in l.text for l in b._gradeable.values())
    newest = list(b._gradeable.values())[-1]
    assert "answer %d" % (Brainish.GRADEABLE_MEMORY + 39) in newest.text


def test_re_showing_a_statement_keeps_it_fresh():
    """Asked the same thing again, it must not be the next one evicted."""
    b = Brainish()
    first = b.remember_answer("q", Answer("the same answer"))
    for i in range(Brainish.GRADEABLE_MEMORY - 1):
        b.remember_answer("filler%d" % i, Answer("filler %d" % i))
    again = b.remember_answer("q", Answer("the same answer"))
    assert again == first
    b.remember_answer("one more", Answer("one more"))
    assert first in b._gradeable, "re-showing it did not refresh it"


# -- the digest must not grow a section it never renders -------------------

def test_the_digest_does_not_render_the_answer_section():
    """A digest reports what the NIGHT did. If `answered` joined SECTIONS the
    renderer would print an empty ANSWERED heading every morning, which is a
    digest lying about its own shape."""
    assert ANSWER_SECTION in GRADEABLE_SECTIONS
    assert ANSWER_SECTION not in SECTIONS


def test_an_answer_line_is_still_a_legal_claim():
    """It has to survive founder.grade_claim and the store's birth gate, or
    the button records nothing."""
    from tracemem.app.digest import DigestLine
    from tracemem.app.founder import grade_claim
    from tracemem.domain import Grade

    line = DigestLine(section=ANSWER_SECTION, text="You read chess — asked: x")
    c = grade_claim(line, "wrong", "It was checkers.", t_ms=5)
    assert c.grade == Grade.VERBATIM
    assert "It was checkers." in c.text
    assert c.structured["about_line"] == line.id
