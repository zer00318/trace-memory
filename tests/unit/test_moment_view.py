"""A citation you can follow.

`Ask/Tap to source` — until now every answer came back stamped [a3b4115f]
with nothing behind the stamp. A citation nobody can follow asks for trust
rather than earning it, which is the opposite of what this product is for.

TEXT-FIRST, because the pixels are dead and always will be: frames die on the
device that captured them. The claims ARE the memory.
"""
from __future__ import annotations

import time

from tracemem.domain import (CaptureCoord, CoordinateStack, SpanCoord,
                             WorldCoord, Confidence, Grade, Node, NodeKind)
from tracemem.domain.node import Attribute
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.extraction.claim_builder import build_claim
from tracemem.supplier.moment import (MAX_LINES, find_claims, render, resolve,
                                      supply_moment)

T0 = 1_700_000_000_000


def _claim(text, t_ms, app="Brave", claim_id=None):
    coord = CoordinateStack(
        world=WorldCoord(place_key="wifi:home"),
        capture=CaptureCoord(frame_id="f1", device_id="mac-1", app=app),
        span=SpanCoord(), t_ms=t_ms)
    c = build_claim({"text": text}, coord, "ocr", bidder="b", bid_id="i",
                    bid_predicted_value=0.0, actual_cost=0.0, scope="frame")
    if claim_id:
        object.__setattr__(c, "id", claim_id) if hasattr(c, "__setattr__") \
            else None
    return c


class Log:
    def __init__(self, claims):
        self._c = list(claims)

    def all_claims(self):
        return list(self._c)


def _log(n=5, base=T0):
    return Log([_claim("line %d" % i, base + i * 1000) for i in range(n)])


# -- resolving a citation --------------------------------------------------

def test_a_citation_prefix_finds_its_claim():
    log = _log()
    target = log._c[2]
    assert resolve(log, target.id[:8]) is target


def test_an_ambiguous_prefix_returns_nothing_rather_than_guessing():
    """Showing a different moment from the one that was clicked, with no sign
    anything was chosen, is worse than showing nothing."""
    c = _claim("x", T0)
    twin = _claim("y", T0 + 1)
    object.__setattr__(twin, "id", c.id)          # same id, forced
    assert resolve(Log([c, twin]), c.id[:8]) is None


def test_an_unknown_citation_is_not_an_error():
    assert resolve(_log(), "ffffffff") is None
    assert find_claims(_log(), "ffffffff") == []
    assert "Nothing in memory carries that citation" in supply_moment(
        _log(), "ffffffff")


def test_an_empty_citation_resolves_to_nothing():
    assert resolve(_log(), "") is None
    assert resolve(_log(), "   ") is None


def test_episode_citation_resolves_to_its_durable_claim_membership():
    log = _log(3)
    graph = MemoryGraphStore()
    episode_id = "episode123456789"
    from tracemem.domain import Episode, TimeRange
    graph.persist_episode(0, Episode(
        id=episode_id, place_key=None,
        t_range=TimeRange(T0, T0 + 3000), claim_ids=()),
        [log._c[0].id, log._c[2].id])

    got = find_claims(log, episode_id[:8], graph_store=graph)

    assert [c.text for c in got] == ["line 0", "line 2"]


def test_node_citation_traverses_node_to_episode_to_claim():
    log = _log(3)
    graph = MemoryGraphStore()
    episode_id = "episode123456789"
    from tracemem.domain import Episode, TimeRange
    graph.persist_episode(0, Episode(
        id=episode_id, place_key=None,
        t_range=TimeRange(T0, T0 + 3000), claim_ids=()),
        [c.id for c in log._c])
    graph.upsert_node(Node(
        id="node123456789", kind=NodeKind.ENTITY,
        attributes=[Attribute("seen_as", "Maya", Grade.VERBATIM,
                              Confidence(0.9), (episode_id,), T0)]))

    page = supply_moment(log, "node1234", graph)

    assert "line 0" in page and "line 2" in page


def test_prefix_ambiguous_across_claim_and_graph_resolves_to_nothing():
    log = _log(1)
    prefix = log._c[0].id[:8]
    graph = MemoryGraphStore()
    graph.upsert_node(Node(
        id=prefix + "node", kind=NodeKind.ENTITY,
        attributes=[Attribute("seen_as", "Maya", Grade.VERBATIM,
                              Confidence(0.9), ("ep",), T0)]))
    assert find_claims(log, prefix, graph_store=graph) == []


# -- the moment around it --------------------------------------------------

def test_it_gathers_the_claims_around_the_citation():
    log = _log(5)
    got = find_claims(log, log._c[2].id[:8])
    assert len(got) == 5
    assert [c.text for c in got] == ["line %d" % i for i in range(5)]


def test_claims_far_away_in_time_are_not_this_moment():
    near = [_claim("near %d" % i, T0 + i * 1000) for i in range(3)]
    far = [_claim("far", T0 + 60 * 60_000)]
    got = find_claims(Log(near + far), near[0].id[:8])
    assert [c.text for c in got] == ["near 0", "near 1", "near 2"]


def test_the_claims_come_back_in_reading_order():
    out_of_order = [_claim("third", T0 + 3000), _claim("first", T0),
                    _claim("second", T0 + 1500)]
    got = find_claims(Log(out_of_order), out_of_order[1].id[:8])
    assert [c.text for c in got] == ["first", "second", "third"]


# -- what the page says ----------------------------------------------------

def test_the_page_says_when_where_and_what():
    log = _log(3)
    page = supply_moment(log, log._c[1].id[:8])
    assert "THE MOMENT AROUND" in page
    assert "Brave" in page
    assert "wifi:home" in page
    for i in range(3):
        assert "line %d" % i in page


def test_the_cited_line_is_marked():
    """Landing on the exact claim, not merely somewhere near it."""
    log = _log(4)
    cite = log._c[2].id[:8]
    page = supply_moment(log, cite)
    marked = [l for l in page.splitlines() if l.startswith(">>")]
    assert len(marked) == 1
    assert "line 2" in marked[0]


def test_a_long_moment_says_what_it_dropped():
    """A page that stops without saying it stopped is a page claiming the
    moment ended there."""
    log = _log(MAX_LINES + 30)
    page = supply_moment(log, log._c[0].id[:8])
    assert "and 30 more line(s) in this moment, not shown." in page


def test_it_says_how_to_push_back():
    """Reading a moment is how someone decides the machine is wrong about it.
    The page that shows the evidence is the page that must offer the reply."""
    log = _log(2)
    page = supply_moment(log, log._c[0].id[:8])
    assert "That's wrong" in page or "grade" in page


def test_a_broken_store_does_not_take_the_page_down():
    class Angry:
        def all_claims(self):
            raise RuntimeError("store on fire")

    assert "Nothing in memory" in supply_moment(Angry(), "abc12345")


def test_the_cited_line_is_never_the_one_that_gets_cut():
    """WITNESSED on the founder's real store. `--moment 9081b271` rendered
    120 lines of a busy moment and the cited claim was not among them,
    because truncation took the FIRST 120. The one line the page exists to
    show was the one it dropped.

    Invisible on a five-claim fixture; the normal case on a real moment
    holding hundreds. So the window is centred on the anchor.
    """
    log = _log(MAX_LINES * 3)
    cite = log._c[MAX_LINES * 2].id[:8]          # deep in the tail
    page = supply_moment(log, cite)
    marked = [l for l in page.splitlines() if l.startswith(">>")]
    assert len(marked) == 1, "the cited line was cut off its own page"
    assert cite in marked[0]


def test_it_says_what_it_cut_from_the_front_as_well():
    """A page that only apologises at the bottom hides that it also began
    late — the reader thinks they are seeing the start of the moment."""
    log = _log(MAX_LINES * 3)
    page = supply_moment(log, log._c[MAX_LINES * 2].id[:8])
    assert "earlier line(s) in this moment, not shown" in page


def test_a_short_moment_is_shown_whole_with_no_apology():
    log = _log(4)
    page = supply_moment(log, log._c[0].id[:8])
    assert "not shown" not in page
