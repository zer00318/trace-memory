"""Where did I work in January?

`The living cell / Facts that expire / Valid-until field`, its oracle: a job
change leaves the old employer true-for-then and the new one true-for-now,
and "where did I work in Jan" answers correctly.

The trail already kept both values — accrue() joins rather than overwrites.
What was missing was any way to ORDER a question against them: an Attribute
carried `t_ms`, the LAST time it was seen, so it had always recorded the end
of its life and never the start. Two values, both real, and no way to say
which was true then.
"""
from __future__ import annotations

from tracemem.domain import Confidence, Grade
from tracemem.domain.grading import retracted
from tracemem.domain.node import Node, NodeKind
from tracemem.nodes.accrual import accrue, spans, true_at

JAN, FEB, MAR, APR = 1_000, 2_000, 3_000, 4_000


def _person():
    n = Node(id="n1", kind=NodeKind.ENTITY)
    # Acme, seen through January and February.
    accrue(n, "employer", "Acme", Grade.VERBATIM, Confidence(0.9), "ep1", JAN)
    accrue(n, "employer", "Acme", Grade.VERBATIM, Confidence(0.9), "ep2", FEB)
    # Globex from March.
    accrue(n, "employer", "Globex", Grade.VERBATIM, Confidence(0.9), "ep3",
           MAR)
    accrue(n, "employer", "Globex", Grade.VERBATIM, Confidence(0.9), "ep4",
           APR)
    return n


def test_the_oracle():
    """Old employer true-for-then; new one true-for-now."""
    n = _person()
    assert true_at(n, "employer", JAN) == "Acme"
    assert true_at(n, "employer", FEB) == "Acme"
    assert true_at(n, "employer", MAR) == "Globex"
    assert true_at(n, "employer", APR) == "Globex"


def test_a_span_has_a_beginning_now():
    n = _person()
    got = {v: (a, b) for v, a, b, _ in spans(n, "employer")}
    assert got["Acme"] == (JAN, FEB)
    assert got["Globex"] == (MAR, APR)


def test_todays_answer_does_not_rewrite_last_january():
    """A value first seen in March is not the answer for January, however
    confident the machine is about it today. Answering with it would be the
    memory rewriting someone's history to match its present."""
    n = _person()
    assert true_at(n, "employer", JAN) != "Globex"


def test_before_anything_was_known_it_says_so():
    """"I did not know you yet" is true. "You worked at Globex" is an
    invention with a date on it, which is worse than a plain one because it
    looks checkable."""
    assert true_at(_person(), "employer", JAN - 500) is None


def test_a_gap_between_spans_is_not_filled_in():
    """Nothing was observed in the gap, so nothing is claimed about it."""
    n = Node(id="n1", kind=NodeKind.ENTITY)
    accrue(n, "city", "Berlin", Grade.VERBATIM, Confidence(0.9), "e1", JAN)
    accrue(n, "city", "Lisbon", Grade.VERBATIM, Confidence(0.9), "e2", APR)
    assert true_at(n, "city", JAN) == "Berlin"
    assert true_at(n, "city", APR) == "Lisbon"
    assert true_at(n, "city", FEB) is None


def test_a_retracted_value_is_never_the_answer():
    """It stays in the trail to be READ, not to be answered with."""
    n = _person()
    for a in n.attributes:
        if a.value == "Globex":
            a.grade = retracted()
    assert true_at(n, "employer", APR) is None
    assert any(r[0] == "Globex" and r[3] for r in spans(n, "employer")), \
        "the retracted value fell out of the trail"


def test_late_evidence_extends_the_span_backwards():
    """Claims do not always arrive in order — a backfill, a late phone sync,
    a re-read of an old file all deliver the past."""
    n = Node(id="n1", kind=NodeKind.ENTITY)
    accrue(n, "employer", "Acme", Grade.VERBATIM, Confidence(0.9), "e1", MAR)
    assert true_at(n, "employer", JAN) is None
    accrue(n, "employer", "Acme", Grade.VERBATIM, Confidence(0.9), "e0", JAN)
    assert true_at(n, "employer", JAN) == "Acme"


def test_the_better_witnessed_value_wins_an_overlap():
    n = Node(id="n1", kind=NodeKind.ENTITY)
    accrue(n, "name", "Sam", Grade.GIST, Confidence(0.5), "e1", JAN)
    accrue(n, "name", "Samantha", Grade.VERBATIM, Confidence(0.9), "e2", JAN)
    assert true_at(n, "name", JAN) == "Samantha"


def test_an_old_stored_attribute_still_answers_for_its_own_moment():
    """Attributes written before this field existed get first == last: one
    sighting time, and no evidence about any other. That is what was known,
    and it must not become a claim to have been true for ever."""
    from tracemem.domain.node import Attribute

    a = Attribute(key="employer", value="Acme", grade=Grade.VERBATIM,
                  confidence=Confidence(0.9), source_episode_ids=("e1",),
                  t_ms=MAR)
    n = Node(id="n1", kind=NodeKind.ENTITY, attributes=[a])
    a.first_t_ms = a.t_ms                       # what the loader does
    assert true_at(n, "employer", MAR) == "Acme"
    assert true_at(n, "employer", JAN) is None
