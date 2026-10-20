"""'You used to think X' — the moat made answerable (commitment 1).

A retired belief is not gone: recall_retired finds it by the query's terms, so
the decay trajectory the pruner preserved can actually be spoken.
"""
from __future__ import annotations

from tracemem.domain import Confidence, Grade, Node, NodeKind
from tracemem.domain.node import Attribute
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.nodes.retention.recall import recall_retired


def _belief(nid, text, ep="e1"):
    return Node(id=nid, kind=NodeKind.HYPOTHESIS, derivation_depth=1,
                parent_ids=("p",),
                attributes=[Attribute(key="claim", value=text, grade=Grade.DERIVED,
                                      confidence=Confidence(0.6),
                                      source_episode_ids=(ep,), t_ms=1000)])


def _graph_with_retired():
    g = MemoryGraphStore()
    g.upsert_node(Node(id="p", kind=NodeKind.OBJECT))          # a parent to satisfy N1
    for nid, text in [("b1", "you seem to prefer croissants"),
                      ("b2", "you usually sleep late on weekends"),
                      ("b3", "you seem to prefer sandwiches")]:
        g.upsert_node(_belief(nid, text))
    g.retire_meta("b1", t_ms=100)
    g.retire_meta("b2", t_ms=200)
    g.retire_meta("b3", t_ms=300)
    return g


def test_a_retired_belief_can_be_recalled_by_its_terms():
    g = _graph_with_retired()
    hits = recall_retired(g, ["croissants"])
    assert [n.id for n, _t in hits] == ["b1"]
    assert hits[0][1] == 100                                   # when you stopped thinking it


def test_recall_ranks_by_overlap_then_recency():
    g = _graph_with_retired()
    hits = recall_retired(g, ["prefer"])                       # matches b1 and b3
    assert {n.id for n, _t in hits} == {"b1", "b3"}
    assert hits[0][0].id == "b3"                                # equal overlap → newer death first


def test_no_match_and_empty_terms_are_silent():
    g = _graph_with_retired()
    assert recall_retired(g, ["helicopter"]) == []
    assert recall_retired(g, ["a", "of"]) == []                # too-short terms ignored


def test_hub_recall_history_surfaces_retired_beliefs():
    """End to end: the moat is reachable through the hub, distinct from ask()."""
    from tests.fakes import ContextAnswerReasoner, FakeClock, HashEmbedder
    from tracemem.app.wiring import build

    hub = build({"embedder": HashEmbedder(),
                 "reasoner": ContextAnswerReasoner(),
                 "clock": FakeClock(1000)})
    hub.w.graph_store.upsert_node(Node(id="p", kind=NodeKind.OBJECT))
    hub.w.graph_store.upsert_node(_belief("b1", "you seem to prefer croissants"))
    hub.w.graph_store.retire_meta("b1", t_ms=100)

    hits = hub.recall_history("do I prefer croissants?")
    assert [n.id for n, _t in hits] == ["b1"]
