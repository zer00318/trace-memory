"""What the graph looks like from above, as numbers.

WHY THIS MATTERS MORE THAN IT SOUNDS. On 2026-08-25 the morning surface was
built, and only when it ran against the real graph did anyone discover that
every belief in it read

    recurring: page=/watch observed in 230 distinct moments

— the memory reporting its own index. Nothing was wrong with the detectors.
Nothing raised. The graph had 378 nodes and a perfectly healthy node count,
and the broken thing was invisible from every angle except reading the
beliefs out loud.

`sayable_belief_rate` is that discovery turned into a number. It is
deliberately crude, and a crude number that would have caught it beats a
subtle one nobody computes.
"""
from __future__ import annotations

from tracemem.domain import Confidence, Grade
from tracemem.domain.node import Attribute, Node, NodeKind
from tracemem.nodes.health import render, sayable, vitals

NOW = 1_700_000_000_000


def _attr(value):
    return Attribute(key="label", value=value, grade=Grade.VERBATIM,
                     confidence=Confidence(0.9), source_episode_ids=("ep1",),
                     t_ms=NOW)


def _node(nid, kind=NodeKind.ENTITY, label="a thing", t_ms=NOW):
    emergent = kind in (NodeKind.PATTERN, NodeKind.HYPOTHESIS,
                        NodeKind.QUESTION)
    return Node(id=nid, kind=kind, derivation_depth=1 if emergent else 0,
                last_active_t_ms=t_ms,
                attributes=[_attr(label)] if label else [],
                parent_ids=("p1",) if emergent else ())


class Store:
    def __init__(self, nodes, edges=()):
        self.nodes = {n.id: n for n in nodes}
        self._edges = set(edges)

    def neighbors(self, node_id, hops=1):
        return [self.nodes[o] for a, o in self._edges if a == node_id
                if o in self.nodes]


# -- the sayable test -----------------------------------------------------

def test_a_database_row_is_not_sayable():
    assert sayable("recurring: page=/watch observed in 230 distinct moments") \
        is False
    assert sayable("app=Brave") is False


def test_a_sentence_about_a_life_is_sayable():
    assert sayable("you keep going back to lichess.org") is True
    assert sayable("Juno keeps coming up") is True


def test_nothing_is_not_sayable():
    assert sayable("") is False
    assert sayable(None) is False


def test_the_rate_would_have_caught_the_real_thing():
    """THE WHOLE POINT. A graph of perfectly healthy shape whose every belief
    is a schema row scores zero here, and scored nothing at all before."""
    store = Store([_node("p%d" % i, NodeKind.PATTERN,
                         "recurring: page=/x%d observed in 9 moments" % i)
                   for i in range(5)])
    v = vitals(store, NOW)
    assert v["beliefs"] == 5
    assert v["sayable_beliefs"] == 0
    assert v["sayable_belief_rate"] == 0.0


def test_a_healthy_belief_tier_scores_one():
    store = Store([_node("p1", NodeKind.PATTERN,
                         "you keep going back to lichess.org")])
    assert vitals(store, NOW)["sayable_belief_rate"] == 1.0


def test_no_beliefs_yet_is_not_zero_percent():
    """A fresh graph has not FAILED to make sayable beliefs; it has not made
    any. Reporting 0% would read as broken on day one."""
    v = vitals(Store([_node("e1")]), NOW)
    assert v["beliefs"] == 0
    assert v["sayable_belief_rate"] is None
    assert "no beliefs yet" in render(v)


# -- shape ----------------------------------------------------------------

def test_orphans_are_counted():
    store = Store([_node("a"), _node("b"), _node("c")],
                  edges=[("a", "b"), ("b", "a")])
    v = vitals(store, NOW)
    assert v["orphans"] == 1
    assert v["orphan_rate"] == round(1 / 3, 3)


def test_ghosts_are_nodes_with_nothing_to_call_them():
    store = Store([_node("named", label="Hana"), _node("ghost", label="")])
    assert vitals(store, NOW)["ghosts"] == 1


def test_the_oldest_ghost_has_an_age():
    """A growing pile of unidentified things means the resolver is losing
    ground, and nothing said so before."""
    old = NOW - 30 * 24 * 3600 * 1000
    store = Store([_node("g1", label="", t_ms=old),
                   _node("g2", label="", t_ms=NOW)])
    v = vitals(store, NOW)
    assert v["oldest_ghost_age_ms"] == NOW - old


def test_no_ghosts_means_no_age_not_zero():
    assert vitals(Store([_node("a")]), NOW)["oldest_ghost_age_ms"] is None


def test_nodes_are_counted_by_kind():
    store = Store([_node("a"), _node("b"),
                   _node("q", NodeKind.QUESTION, "why?")])
    by_kind = vitals(store, NOW)["by_kind"]
    assert by_kind["ENTITY"] == 2
    assert by_kind["QUESTION"] == 1


def test_an_empty_graph_reports_zeros_not_an_error():
    v = vitals(Store([]), NOW)
    assert v["nodes"] == 0 and v["orphan_rate"] == 0.0


def test_a_broken_store_does_not_take_the_vitals_down():
    class Angry:
        @property
        def nodes(self):
            raise RuntimeError("graph on fire")

    assert vitals(Angry(), NOW)["nodes"] == 0


def test_the_numbers_carry_when_they_were_computed():
    """Numbers with dates, no interpretation — the brief's own words."""
    v = vitals(Store([_node("a")]), NOW)
    assert v["computed_at_ms"] == NOW
    assert "computed 20" in render(v)


def test_the_night_calls_these_with_a_clock_it_actually_has():
    """CAUGHT BY A SHADOW RUN, NOT BY A UNIT TEST.

    Both new night passes called `self._now()`. NightWatchman has `_now_ms`.
    Every unit test around them built the object with `__new__` and hand-set
    attributes, so none ever executed the real call — and the whole suite
    passed while the night logged

        relabel pass failed: AttributeError: 'NightWatchman' object has no
        attribute '_now'

    on real data. This asserts the attribute the code reaches for is one the
    class defines, which is the cheap half of what the shadow proved.
    """
    import inspect

    from tracemem.app import night_watchman

    src = inspect.getsource(night_watchman.NightWatchman)
    assert "self._now_ms = now_ms" in inspect.getsource(night_watchman)
    for call in ("relabel_pass(g0,", "vitals(g,"):
        i = src.index(call)
        assert "self._now_ms()" in src[i:i + 120], \
            "%s is called with a clock the class does not have" % call
    assert "self._now()" not in src
