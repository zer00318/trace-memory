"""The empty morning is the feature.

`The morning surface / The content picker`: at most three items a night, and
NO FILLER, EVER.

Every product that surfaces something daily learns to pad, because a blank
screen feels like a broken one. Padding is how a memory starts telling you
things it does not believe: the third-best item on a quiet Tuesday is not a
finding, it is a sentence generated to fill a slot. Once it does that, a
person cannot tell the day it noticed something from the day it had nothing —
which destroys the only reason to read it.

So the bars here are ABSOLUTE, never "the top 3 of whatever exists". That
one design choice is what these tests mostly exist to protect.
"""
from __future__ import annotations

from tracemem.domain import Confidence, Grade
from tracemem.domain.node import Attribute, Node, NodeKind
from tracemem.supplier.morning import (MAX_ITEMS, MIN_PATTERN_PARENTS,
                                       MIN_SALIENCE, morning, pick, render)

NIGHT = 1_700_000_000_000
LATER = NIGHT + 60_000


class Store:
    def __init__(self, nodes):
        self.nodes = {n.id: n for n in nodes}


def _attr(value="a thing", eps=("ep1",)):
    return Attribute(key="label", value=value, grade=Grade.VERBATIM,
                     confidence=Confidence(0.9),
                     source_episode_ids=tuple(eps), t_ms=LATER)


def _node(kind, nid, *, parents=1, salience=0.0, t_ms=LATER, cites=("ep1",),
          value="a thing"):
    emergent = kind in (NodeKind.PATTERN, NodeKind.HYPOTHESIS,
                        NodeKind.QUESTION)
    return Node(id=nid, kind=kind,
                derivation_depth=1 if emergent else 0,
                salience_ema=salience, last_active_t_ms=t_ms,
                attributes=[_attr(value, cites)] if cites else [],
                parent_ids=tuple("p%d" % i for i in range(parents))
                if emergent else ())


# -- the empty morning ----------------------------------------------------

def test_a_quiet_night_says_nothing_at_all():
    assert pick(Store([]), NIGHT) == []
    assert morning(Store([]), NIGHT) is None


def test_render_returns_none_not_an_empty_page():
    """None rather than "", so a caller cannot accidentally show a blank
    panel that reads as a failure. Nothing to say means nothing shown."""
    assert render([]) is None


def test_a_weak_night_is_not_padded_up_to_three():
    """THE CORE PROPERTY. One strong item and two weak ones must surface ONE
    item, not three. "Top 3 of whatever exists" is the rule that produces
    filler."""
    store = Store([
        _node(NodeKind.QUESTION, "q1"),
        # Weak by MEANING: a machine-shaped label, which no count rescues.
        _node(NodeKind.PATTERN, "p1", value="recurring: page=/x in 9 moments"),
        # Weak by kind: an observation with a name is not a finding.
        _node(NodeKind.ENTITY, "e1", salience=0.99, value="youtube.com"),
    ])
    got = pick(store, NIGHT)
    assert [i.kind for i in got] == ["question"]


def test_recurrence_is_enforced_where_it_can_actually_be_seen():
    """This test used to assert that a pattern with fewer than three PARENTS
    was rejected, on the reasoning that a pattern seen twice is a coincidence
    with ambitions. The reasoning is right and the quantity was wrong:
    `parent_ids` is which nodes contributed the attribute, not how many times
    it recurred.

    The real guarantee lives upstream, where the recurrence is actually
    counted, and it has always been there.
    """
    from tracemem.nodes.combination.kinds.pattern import MIN_EPISODES

    assert MIN_EPISODES >= 3


def test_nothing_from_before_tonight_is_new_news():
    """Yesterday's finding re-served as today's is filler wearing a date."""
    store = Store([_node(NodeKind.QUESTION, "q1", t_ms=NIGHT - 1)])
    assert pick(store, NIGHT) == []


# -- receipts -------------------------------------------------------------

def test_an_item_with_no_receipt_is_never_surfaced():
    """Worse unprompted than in an answer: nobody asked, so nobody is
    checking."""
    store = Store([_node(NodeKind.QUESTION, "q1", cites=())])
    assert pick(store, NIGHT) == []


def test_every_surfaced_item_carries_its_citations():
    store = Store([_node(NodeKind.QUESTION, "q1", cites=("ep7",))])
    got = pick(store, NIGHT)
    assert got and got[0].citations == ("ep7",)
    assert "ep7"[:8] in render(got)


# -- what it picks, and how much ------------------------------------------

def test_it_never_says_more_than_three_things():
    store = Store([_node(NodeKind.QUESTION, "q%d" % i) for i in range(9)])
    assert len(pick(store, NIGHT)) == MAX_ITEMS


def test_questions_come_before_patterns_and_surprises():
    """Answering one is worth more than anything the memory can derive
    alone, so it is the first thing offered."""
    store = Store([
        _node(NodeKind.HYPOTHESIS, "h1", salience=0.9),
        _node(NodeKind.PATTERN, "p1", parents=MIN_PATTERN_PARENTS),
        _node(NodeKind.QUESTION, "q1"),
    ])
    assert [i.kind for i in pick(store, NIGHT)] == \
        ["question", "pattern", "surprise"]


def test_the_biggest_surprise_wins():
    store = Store([
        _node(NodeKind.HYPOTHESIS, "small", salience=MIN_SALIENCE + 0.01,
              value="small thing"),
        _node(NodeKind.HYPOTHESIS, "big", salience=0.95, value="big thing"),
    ])
    got = pick(store, NIGHT, limit=1)
    assert got[0].text == "big thing"


def test_a_broken_graph_does_not_take_the_morning_down():
    class Angry:
        @property
        def nodes(self):
            raise RuntimeError("graph on fire")

    assert pick(Angry(), NIGHT) == []


def test_the_page_says_it_can_be_argued_with():
    store = Store([_node(NodeKind.QUESTION, "q1")])
    page = render(pick(store, NIGHT))
    assert "YOU DIDN'T ASK, BUT" in page
    assert "evidence, not a knob" in page


def test_the_memory_never_reports_its_own_indexing():
    """MEASURED against the founder's real graph. All three of these cleared
    the count bars and every one is worthless to a person:

        recurring: page=/watch observed in 230 distinct moments
        recurring: page=/ observed in 13 distinct moments
        recurring: page=/search observed in 9 distinct moments

    They are true. They are also the memory describing its own index rather
    than anything about a life — "page=/" is a URL path, not a thing that
    happened. The count bars caught weak-by-count and let weak-by-MEANING
    walk straight through, which is the filler this module exists to refuse.
    """
    store = Store([
        _node(NodeKind.PATTERN, "p1", parents=MIN_PATTERN_PARENTS,
              value="recurring: page=/watch observed in 230 distinct moments"),
        _node(NodeKind.PATTERN, "p2", parents=MIN_PATTERN_PARENTS,
              value="recurring: app=Brave observed in 40 distinct moments"),
    ])
    assert pick(store, NIGHT) == []


def test_a_real_pattern_still_gets_through():
    """The filter must not swallow the findings it exists to protect."""
    store = Store([
        _node(NodeKind.PATTERN, "p1", parents=MIN_PATTERN_PARENTS,
              value="you read chess writing most evenings after 9pm"),
    ])
    got = pick(store, NIGHT)
    assert len(got) == 1
    assert "chess" in got[0].text


def test_a_noun_is_not_news():
    """MEASURED on the real graph. The surprise tier produced this,
    unprompted:

        <a site>      (this stood out more than it expected)
        <a site>      (this stood out more than it expected)
        <an app>      (this stood out more than it expected)

    Three of the owner's most-used sites and apps (withheld here), announced
    as insights. A noun is not a finding, and an unprompted list of most-visited domains is
    something a person may very much not want their machine volunteering —
    while telling them nothing they did not already know.

    digest.py states the rule this broke: the entity tier is not a belief, it
    is an observation with a name. A morning item must be DERIVED.
    """
    store = Store([_node(NodeKind.ENTITY, "e1", salience=0.99,
                         value="youtube.com")])
    assert pick(store, NIGHT) == []


def test_the_recurrence_bar_does_not_reject_every_real_pattern():
    """THE BUG THAT LOOKED LIKE THE FEATURE.

    The bar was `len(parent_ids) >= 3`, reasoning that a pattern seen twice
    is a coincidence. But `parent_ids` is WHICH NODES contributed the
    attribute, not how many times it was observed — recurrence is already
    enforced upstream by pattern.MIN_EPISODES, which refuses to mint anything
    seen in fewer than three distinct moments.

    Measured on the real graph: all 162 emergent nodes have one or two
    parents and none has three. The bar rejected every real pattern, and the
    morning surface was permanently empty — which is ALSO what a quiet night
    looks like. A broken filter wearing the costume of honest silence, which
    the module's own "no filler, ever" note would have explained away.
    """
    from tracemem.nodes.combination.kinds.pattern import MIN_EPISODES

    real_shape = _node(NodeKind.PATTERN, "p1", parents=1,
                       value="you keep going back to lichess.org")
    got = pick(Store([real_shape]), NIGHT)
    assert len(got) == 1, "a pattern with the shape real ones have was dropped"

    # And the reason it is safe to accept: recurrence was already checked.
    assert MIN_EPISODES >= 3
