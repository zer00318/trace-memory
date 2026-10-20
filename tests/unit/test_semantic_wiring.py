"""Semantic edges — meaning becomes a force in the wiring (analysis gap #5).

The croissant/bakery/food clustering the vision centers on: things CLOSE IN
MEANING grow an edge and become combination candidates, even if never co-seen —
no hardcoded food ontology, just embedding proximity.
"""
from __future__ import annotations

from typing import Sequence

from tracemem.domain import Confidence, Edge, EdgeType, Grade, Node, NodeKind
from tracemem.domain.node import Attribute
from tracemem.nodes.combination.candidate_selector import select_candidates
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.nodes.marination.semantic import semantic_pass


class CategoryEmbedder:
    """A tiny fake with real category structure: food words point one way,
    electronics another, everything else a third — so 'croissant' and
    'sandwich' are close and 'headphones' is far, deterministically."""
    _FOOD = ("croissant", "sandwich", "bakery", "coffee", "magerquark")
    _ELEC = ("headphones", "laptop", "charger", "safety pin")

    def embed(self, text: str) -> Sequence[float]:
        t = text.lower()
        if any(w in t for w in self._FOOD):
            base = [1.0, 0.0, 0.0]
        elif any(w in t for w in self._ELEC):
            base = [0.0, 1.0, 0.0]
        else:
            base = [0.0, 0.0, 1.0]
        jitter = (sum(ord(c) for c in t) % 5) * 0.01     # same-category ≠ identical
        return [base[0] + jitter, base[1] + jitter * 0.0, base[2]]


def _thing(node_id, label):
    # TWO episodes, not one (2026-07-29). Combination now requires a node to
    # have been seen in more than one moment before it may be the SUBJECT of a
    # derived belief — see candidate_selector.MIN_SIGHTINGS_TO_COMBINE, added
    # after the founder's first real digest showed four in five beliefs
    # relating OCR truncations that appeared exactly once. These fixtures are
    # about MEANING clustering, not about sighting counts; the single episode
    # was incidental detail that happened to encode the old assumption.
    return Node(id=node_id, kind=NodeKind.OBJECT, activation=0.5,
                last_active_t_ms=1000,
                attributes=[Attribute(key="object", value=label,
                                      grade=Grade.VERBATIM,
                                      confidence=Confidence(0.9),
                                      source_episode_ids=("e-1", "e-2"),
                                      t_ms=1000)])


def _graph():
    g = MemoryGraphStore()
    for nid, lab in [("c", "croissant"), ("s", "sandwich"),
                     ("b", "bakery"), ("h", "headphones")]:
        g.upsert_node(_thing(nid, lab))
    return g


def test_meaning_close_nodes_grow_a_semantic_edge_unrelated_ones_do_not():
    g = _graph()
    touched = semantic_pass(g, CategoryEmbedder(), list(g.nodes.values()), t_ms=2000)
    assert touched >= 1
    # food clusters
    assert g.edge_between("c", "s", EdgeType.SEMANTIC) is not None
    assert g.edge_between("c", "b", EdgeType.SEMANTIC) is not None
    # food vs electronics does NOT
    assert g.edge_between("c", "h", EdgeType.SEMANTIC) is None
    assert g.edge_between("b", "h", EdgeType.SEMANTIC) is None


def test_semantic_edge_makes_them_neighbors_and_combination_candidates():
    g = _graph()
    semantic_pass(g, CategoryEmbedder(), list(g.nodes.values()), t_ms=2000)
    # they now "fire together": sandwich is a neighbor of croissant by MEANING
    assert "s" in {n.id for n in g.neighbors("c")}
    # and the pair surfaces as a combination candidate
    pairs = {frozenset((a.id, b.id)) for a, b, _ in select_candidates(g, 2000, budget_b=20)}
    assert frozenset(("c", "s")) in pairs
    assert frozenset(("c", "h")) not in pairs      # unrelated never pair on meaning


def test_semantic_runs_in_the_real_night_via_the_hub():
    """The night wires meaning when the hub carries an embedder (analysis gap #5,
    end to end): two hot food nodes grow a SEMANTIC edge during hub.night()."""
    from tests.fakes import ContextAnswerReasoner, FakeClock
    from tracemem.app.wiring import build

    hub = build({"embedder": CategoryEmbedder(),
                 "reasoner": ContextAnswerReasoner(),
                 "clock": FakeClock(1000)})
    hub.w.graph_store.upsert_node(_thing("c", "croissant"))
    hub.w.graph_store.upsert_node(_thing("s", "sandwich"))

    report = hub.night()
    assert report.semantic_edges >= 1
    assert hub.w.graph_store.edge_between("c", "s", EdgeType.SEMANTIC) is not None


def test_semantic_edge_reinforces_across_nights():
    g = _graph()
    semantic_pass(g, CategoryEmbedder(), list(g.nodes.values()), t_ms=2000)
    w1 = g.edge_between("c", "s", EdgeType.SEMANTIC).weight
    semantic_pass(g, CategoryEmbedder(), list(g.nodes.values()), t_ms=3000)
    e = g.edge_between("c", "s", EdgeType.SEMANTIC)
    assert e.evidence_count == 2                    # meaning that keeps mattering deepens
    assert e.weight > w1


def test_an_unchanged_world_deepens_NOTHING_and_reports_no_progress():
    """THE LIVELOCK'S SECOND COMING, 2026-08-20.

    `semantic_pass` counted every edge it looked at as "touched", and
    reinforced every existing one, on every call. The night watchman slices
    the pass every 30 seconds and reads that count as FORWARD PROGRESS, so
    the stand-down guard added on 2026-07-29 could never fire: 55 consecutive
    slices, `attempted=1641 accepted=0`, and every one of them reporting
    `semantic_edges: 14`.

    The damage was not only wasted CPU. On the founder's live graph the same
    pairs had been reinforced thousands of times — semantic edges at weight
    6,927 and evidence_count 3,130, for a cosine bounded by 1.0. That is the
    "graph that grows more confident the more often you restart it" failure
    this codebase ranks as its most dangerous, running every half minute.

    A recomputed cosine over two nodes that have not moved is not new
    evidence. `since_t_ms` is what says so.
    """
    g = _graph()
    first = semantic_pass(g, CategoryEmbedder(), list(g.nodes.values()),
                          t_ms=2000, since_t_ms=1000)
    assert first >= 1
    e = g.edge_between("c", "s", EdgeType.SEMANTIC)
    weight_after_first = e.weight
    evidence_after_first = e.evidence_count

    # Nothing has been active since t=1000 (the fixtures' last_active is
    # 1000), so a second and third pass are pure recomputation.
    for t in (3000, 4000):
        assert semantic_pass(g, CategoryEmbedder(), list(g.nodes.values()),
                             t_ms=t, since_t_ms=2500) == 0

    e = g.edge_between("c", "s", EdgeType.SEMANTIC)
    assert e.evidence_count == evidence_after_first
    assert e.weight == weight_after_first


def test_a_node_that_HAS_moved_still_deepens_its_meaning_edge():
    """The intent of the original design is kept: meaning that keeps
    mattering deepens. What changed is what counts as "keeps mattering" —
    a node being seen again, not the machine recomputing the same cosine."""
    g = _graph()
    semantic_pass(g, CategoryEmbedder(), list(g.nodes.values()), t_ms=2000,
                  since_t_ms=1000)
    before = g.edge_between("c", "s", EdgeType.SEMANTIC).evidence_count

    moved = g.get_node("c")
    moved.last_active_t_ms = 5000          # seen again in the real world
    g.upsert_node(moved)

    assert semantic_pass(g, CategoryEmbedder(), list(g.nodes.values()),
                         t_ms=6000, since_t_ms=2500) >= 1
    assert g.edge_between("c", "s", EdgeType.SEMANTIC).evidence_count > before
