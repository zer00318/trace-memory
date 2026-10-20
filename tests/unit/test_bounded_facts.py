"""Which facts about a life naturally expire.

The machinery landed first — `accrual.true_at` answers true-for-then — and it
is useless without an answer to WHICH keys it applies to.

The distinction that matters: some facts are bounded because the world
changes (an employer, an address); others are bounded only in the sense that
any observation could be superseded (a person's name). Treating the second
kind as bounded makes a memory nervous about things nobody doubts; treating
the first as unbounded makes it confidently wrong about a life that moved on.
"""
from __future__ import annotations

from tracemem.nodes.bounded_facts import (BOUNDED, UNBOUNDED, is_bounded,
                                          render, rule_for)


def test_the_things_that_change_are_bounded():
    for key in ("employer", "address", "price"):
        assert is_bounded(key), key


def test_a_name_is_not_treated_as_expiring():
    """Every hedge spent on something nobody doubts is one a reader stops
    believing when it matters."""
    assert not is_bounded("seen_as")
    assert "seen_as" in UNBOUNDED


def test_every_bound_says_what_opens_and_closes_it():
    """"It expires" is not a rule anyone can implement."""
    for b in BOUNDED.values():
        assert b.opens and b.closes and b.plain


def test_nothing_closes_on_silence():
    """THE ASYMMETRY. A fact does not stop being true because nobody looked
    at it, and expiring on absence would make a laptop left shut for a
    fortnight forget where someone works.

    The worst failure available here is a memory that quietly decides a true
    thing stopped being true — nothing downstream can tell that from the
    thing genuinely having changed.
    """
    # The invariant, not the wording: every rule closes on a POSITIVE
    # observation of a different value, and none closes on time passing.
    SILENCE = ("timer", "timeout", "after ", "days without", "no longer seen",
               "stops being observed", "absence")
    for b in BOUNDED.values():
        assert "different" in b.closes or "another" in b.closes, \
            "%s does not say what value replaces it: %r" % (b.key, b.closes)
        for word in SILENCE:
            assert word not in b.closes.lower(), \
                "%s closes on silence (%r)" % (b.key, word)


def test_the_lists_do_not_overlap():
    assert not (set(BOUNDED) & set(UNBOUNDED))


def test_an_unknown_key_is_not_silently_bounded():
    """A key nobody has ruled on must not start expiring by default."""
    assert not is_bounded("something_new")
    assert rule_for("something_new") is None


def test_it_reads_as_something_a_person_reviews():
    text = render()
    assert "needs your review" in text
    assert "employer" in text and "seen_as" in text
    assert "does not stop being" in text
