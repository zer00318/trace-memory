"""Heart mechanics: guardrails, kinds, judge, selector, night loop, retention."""
from __future__ import annotations

import pytest

from tests.fakes import FakeClock, FakeLease, ScriptedReasoner
from tracemem.domain import (
    Attribute, Confidence, Edge, EdgeType, Grade, Node, NodeKind,
)
from tracemem.nodes.combination.guardrails import (
    GuardrailViolation, assert_grade_ceiling,
)
from tracemem.nodes.combination.judge import judge
from tracemem.nodes.combination.kinds import causal, contradiction, transitive
from tracemem.nodes.combination.proposal import Proposal
from tracemem.nodes.birth.person_judge import JUDGE_TOKENS
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.nodes.marination.nightly import run_night
from tracemem.nodes.retention.policy import RetentionPolicy
from tracemem.nodes.retention.pruner import prune, reconfirm_scan


def entity(nid, key="seen_as", value="Sam", grade=Grade.VERBATIM,
           episodes=("e1",), kind=NodeKind.ENTITY, activation=0.0):
    return Node(id=nid, kind=kind, activation=activation, attributes=[
        Attribute(key, value, grade, Confidence(0.9), tuple(episodes), 0)])


def test_grade_ceiling_guardrail():
    with pytest.raises(GuardrailViolation):
        assert_grade_ceiling(Grade.VERBATIM, [Grade.GIST])
    assert_grade_ceiling(Grade.DERIVED, [Grade.GIST])          # strictly below: fine


def test_judge_rejects_fabricated_citations():
    g = MemoryGraphStore()
    g.upsert_node(entity("a", episodes=("e1",)))
    g.upsert_node(entity("b", episodes=("e2",)))
    j = judge(Proposal(kind="pattern", claim_text="x", parent_node_ids=("a", "b"),
                       supported_by=("e-not-a-parent",)), g)
    assert not j.accepted and "outside its parents" in j.reason


def test_judge_accepts_and_degrades():
    g = MemoryGraphStore()
    g.upsert_node(entity("a", grade=Grade.VERBATIM, episodes=("e1", "e2", "e3")))
    g.upsert_node(entity("b", grade=Grade.SPECIALIST, episodes=("e1",)))
    j = judge(Proposal(kind="pattern", claim_text="a and b recur",
                       parent_node_ids=("a", "b"), supported_by=("e1",)), g)
    assert j.accepted
    assert j.node.derivation_depth == 1
    assert j.node.attributes[0].grade < Grade.SPECIALIST       # below weakest parent
    j2 = judge(Proposal(kind="pattern", claim_text="a and b recur",
                        parent_node_ids=("a", "b"), supported_by=("e1",)), g)
    assert j2.accepted    # judge is pure; DUPLICATE rejection needs the node written
    g.upsert_node(j.node)
    j3 = judge(Proposal(kind="pattern", claim_text="a and b recur",
                        parent_node_ids=("a", "b"), supported_by=("e1",)), g)
    assert not j3.accepted and j3.reason == "duplicate meta"


def test_transitive_composes_through_shared_neighbor():
    g = MemoryGraphStore()
    a, b, c = entity("a"), entity("b"), entity("c", kind=NodeKind.PLACE)
    for n in (a, b, c):
        g.upsert_node(n)
    g.upsert_edge(Edge("a", "c", EdgeType.LOCATED_AT))
    g.upsert_edge(Edge("b", "c", EdgeType.LOCATED_AT))
    p = transitive.detect(a, b, g)
    assert p.kind == "transitive" and p.extras["via"] == "c"


def test_contradiction_only_fires_verbatim_vs_verbatim():
    g = MemoryGraphStore()
    a = entity("a", key="phone", value="123", grade=Grade.VERBATIM)
    b = entity("b", key="phone", value="456", grade=Grade.VERBATIM)
    assert contradiction.detect(a, b, g).kind == "contradiction"
    b_gist = entity("b2", key="phone", value="456", grade=Grade.GIST)
    assert contradiction.detect(a, b_gist, g).is_none          # R2: defer, don't accuse


def test_causal_abstains_on_malformed_dream():
    g = MemoryGraphStore()
    a, b = entity("a"), entity("b")
    g.upsert_node(a); g.upsert_node(b)
    g.upsert_edge(Edge("a", "b", EdgeType.PRECEDES, evidence_count=3))
    assert causal.propose(a, b, g, ScriptedReasoner(default="not json")).is_none


def test_night_spends_only_through_the_gate():
    g = MemoryGraphStore()
    a = entity("a", episodes=("e1", "e2", "e3"), activation=0.9)
    # e4+e5: combination requires a node to have been seen in more than one
    # moment (candidate_selector.MIN_SIGHTINGS_TO_COMBINE). This test is about
    # the BUDGET gate, not about sighting counts.
    b = entity("b", episodes=("e4", "e5"), activation=0.9, kind=NodeKind.PLACE,
               key="place", value="wifi:x")
    g.upsert_node(a); g.upsert_node(b)
    g.upsert_edge(Edge("a", "b", EdgeType.PRECEDES, evidence_count=5))

    class Budget:
        def __init__(self): self.spent_n = 0
        def may_spend(self, n): return True
        def spend(self, n): self.spent_n += n

    budget = Budget()
    reasoner = ScriptedReasoner(default=(
        '{"kind":"causal","claim":"a precedes b nightly",'
        '"supported_by":["e1"],"rationale":"r"}'))
    report = run_night(g, None, budget, FakeLease(), FakeClock(1000),
                       reasoner=reasoner)
    # THE INVARIANT IS "EVERYTHING SPENT WENT THROUGH THE GATE", not a magic
    # number. When the person judge landed (2026-08-20) it added a second
    # model call — for the entity named "Sam" above — and this test caught it
    # spending OUTSIDE the budget, which is exactly what it exists to catch:
    # a budget with a way around it is not a budget. The judge is now gated
    # and charged like the causal proposer, so the identity below still
    # holds while the total legitimately changes.
    causal = [p for p in reasoner.prompts if "memory consolidation" in p]
    judged = [p for p in reasoner.prompts if "one word: yes or no" in p]
    assert len(causal) == 1
    assert len(causal) + len(judged) == len(reasoner.prompts), \
        "the night made a model call that is neither gated spender"
    assert budget.spent_n == report.tokens_spent
    assert budget.spent_n == 1500 + len(judged) * JUDGE_TOKENS
    assert report.accepted >= 1


def test_night_pauses_when_contended():
    lease = FakeLease()
    with lease.acquire("someone-else"):
        report = run_night(MemoryGraphStore(), None, None, lease, FakeClock(0))
    assert report.status == "PAUSED" and report.attempted == 0


def test_pruner_reaps_only_cold_rederivable_meta():
    g = MemoryGraphStore()
    keeper = entity("keep", activation=0.9)                    # depth-0: untouchable
    g.upsert_node(keeper)
    meta = Node(id="m", kind=NodeKind.PATTERN, derivation_depth=1,
                parent_ids=("keep",), activation=0.0, last_active_t_ms=0,
                attributes=[Attribute("claim", "old habit", Grade.DERIVED,
                                      Confidence(0.5), ("e1",), 0)])
    g.upsert_node(meta)
    week_later = 14 * 24 * 3600 * 1000
    pruned = prune(g, RetentionPolicy(), week_later)
    assert pruned == ["m"] and "keep" in g.nodes


def test_reconfirm_climbs_grade_capped():
    g = MemoryGraphStore()
    g.upsert_node(entity("p", ))
    meta = Node(id="m", kind=NodeKind.PATTERN, derivation_depth=1,
                parent_ids=("p",),
                attributes=[Attribute("claim", "morning espresso ritual daily",
                                      Grade.DERIVED, Confidence(0.6), ("e1",), 0)])
    g.upsert_node(meta)
    from tests.helpers import make_claim
    from tracemem.domain.episode import Episode
    from tracemem.domain.time import TimeRange
    ep = Episode(id="e9", t_range=TimeRange(0, 10), place_key="wifi:x")
    fresh = make_claim(text="another morning espresso ritual observed daily")
    climbed = reconfirm_scan(g, [ep], {"e9": [fresh]})
    assert climbed == ["m"]
    assert g.nodes["m"].attributes[0].grade == Grade.GIST      # climbed exactly one
    assert "e9" in g.nodes["m"].attributes[0].source_episode_ids
