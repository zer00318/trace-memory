"""Three behavioural proofs that used to run inside the cockpit's scoreboard.

They build in-memory objects, so production state could never make them
fail; an independent review (2026-10-15) rightly called them unit tests
counted as production invariants. They live here now, unchanged in
substance, and the cockpit measures only the live system.
"""
from __future__ import annotations

from types import SimpleNamespace

from tracemem.domain import (Attribute, Confidence, Episode, Grade, Node,
                             NodeKind, TimeRange)
from tracemem.nodes.accrual import accrue
from tracemem.nodes.combination.candidate_selector import evidence_fingerprint
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.supplier.moment import find_claims


def _pair():
    def node(nid, value):
        return Node(id=nid, kind=NodeKind.ENTITY, attributes=[Attribute(
            key="name", value=value, grade=Grade.FLOOR,
            confidence=Confidence(0.1), source_episode_ids=("ep-1",), t_ms=1)])
    return node("a", "A"), node("b", "B")


def test_living_evidence_invalidates_combination_work():
    a, b = _pair()
    before = evidence_fingerprint(a, b)
    accrue(a, "name", "A", Grade.VERBATIM, Confidence(0.9), "ep-2", 2)
    assert evidence_fingerprint(a, b) != before


def test_confidence_accrues_with_stronger_evidence():
    a, _ = _pair()
    accrue(a, "name", "A", Grade.VERBATIM, Confidence(0.9), "ep-2", 2)
    assert a.attributes[0].confidence.value >= 0.9


def test_episode_and_node_citations_close_on_raw_evidence():
    evidence = SimpleNamespace(id="claim-proof", coord=SimpleNamespace(t_ms=10))
    log = SimpleNamespace(all_claims=lambda: [evidence])
    graph = MemoryGraphStore()
    graph.persist_episode(0, Episode(id="episode-proof", place_key=None,
                                     t_range=TimeRange(1, 20), claim_ids=()),
                          [evidence.id])
    graph.upsert_node(Node(id="node-proof", kind=NodeKind.ENTITY, attributes=[
        Attribute(key="name", value="proof", grade=Grade.VERBATIM,
                  confidence=Confidence(0.9),
                  source_episode_ids=("episode-proof",), t_ms=10)]))
    assert [c.id for c in find_claims(log, "episode-p", graph_store=graph)] == [evidence.id]
    assert [c.id for c in find_claims(log, "node-proo", graph_store=graph)] == [evidence.id]
