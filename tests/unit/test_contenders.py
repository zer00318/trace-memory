"""THE CONTENDER GAUNTLET — Designs A/B/C under the common arena contract.

Common traps run against all three engines (the arena's honesty floor);
per-design traps encode each design doc's laws and the seeds of its
KILL CONDITIONS. Design 0 competes via its own adapter at arena time
and is deliberately absent here (bias law).
"""
from __future__ import annotations

import pytest

from tests.helpers import make_claim
from tracemem.contenders import (
    EvidenceBlock, FutureAnswerMemory, UnderstandingMemory, WiringMemory,
)

ENGINES = [UnderstandingMemory, FutureAnswerMemory, WiringMemory]


def week(n=12):
    """A tiny synthetic 'week': a recurring coffee ritual, a person, noise."""
    claims = []
    for i in range(n):
        claims.append(make_claim(text=f"bought coffee at Marco cafe morning {i}",
                                 t_ms=1000 + i * 60000, frame=f"f-cof-{i}"))
    claims.append(make_claim(text="Sam sent the Berlin contract draft",
                             t_ms=900000, frame="f-sam-1"))
    claims.append(make_claim(text="Sam asked about the Berlin flight",
                             t_ms=960000, frame="f-sam-2"))
    claims.append(make_claim(text="Sam confirmed the Berlin meeting",
                             t_ms=980000, frame="f-sam-3"))
    claims.append(make_claim(text="weather widget showed rain",
                             t_ms=990000, frame="f-noise"))
    return claims


# ---------------------------------------------------------------- common
@pytest.mark.parametrize("Engine", ENGINES)
def test_supply_cites_only_ingested_claims(Engine):
    m = Engine(); m.ingest(week()); m.marinate(100)
    pkg = m.supply("what do I know about Sam and Berlin?")
    ingested = set(m._claims)
    assert pkg.blocks, "engine offered nothing for an easy associative question"
    assert pkg.cited_ids() <= ingested, "fabricated receipt — never-lie violated"


@pytest.mark.parametrize("Engine", ENGINES)
def test_incremental_ingest_matches_bulk(Engine):
    w = week()
    a, b = Engine(), Engine()
    a.ingest(w)
    for c in w:
        b.ingest([c])
    a.marinate(1000); b.marinate(1000)
    pa = {blk.text for blk in a.supply("Sam Berlin").blocks}
    pb = {blk.text for blk in b.supply("Sam Berlin").blocks}
    assert pa == pb, "ingest is not honestly incremental"


@pytest.mark.parametrize("Engine", ENGINES)
def test_duplicate_ingest_changes_nothing(Engine):
    w = week()
    m = Engine(); m.ingest(w); m.marinate(1000)
    before = m.storage()
    m.ingest(w)          # same claims again — idempotent by claim id
    m.marinate(1000)
    assert m.storage().object_count == before.object_count


@pytest.mark.parametrize("Engine", ENGINES)
def test_storage_report_is_honest_shape(Engine):
    m = Engine(); m.ingest(week())
    rep = m.storage()
    assert rep.bytes_used > 0 and rep.object_count >= 0


def test_block_without_citation_is_impossible():
    with pytest.raises(ValueError):
        EvidenceBlock(text="trust me", claim_ids=(), source="liar")


# ---------------------------------------------------------------- Design A
def test_a_compression_gate_births_ritual_not_noise():
    m = UnderstandingMemory(); m.ingest(week())
    cells = m._cells
    assert any("coffee" in cid or "marco" in cid for cid in cells), \
        "12 coffee claims should compress into a cell"
    assert not any("rain" in cid or "weather" in cid for cid in cells), \
        "a singleton claim must NOT become a cell — it stays a claim"


def test_a_reconsolidation_keeps_trail_and_pins_citations():
    m = UnderstandingMemory(); m.ingest(week()); m.marinate(50)
    cell = next(iter(m._cells.values()))
    old_gist = cell.gist
    m.ingest([make_claim(text=f"bought coffee at Marco cafe morning 99",
                         t_ms=2_000_000, frame="f-cof-99")])
    m.marinate(50)
    cell2 = m._cells[cell.id]
    if cell2.gist != old_gist:
        assert old_gist in cell2.trail, "rewrite without trail — silent flip"
    assert all(i in m._claims for i in cell2.claim_ids)


def test_a_marinate_idempotent_no_trail_growth():
    m = UnderstandingMemory(); m.ingest(week())
    m.marinate(100); t1 = sum(len(u.trail) for u in m._cells.values())
    m.marinate(100); t2 = sum(len(u.trail) for u in m._cells.values())
    assert t1 == t2, "idle reconsolidation grew trails — not idempotent"


# ---------------------------------------------------------------- Design B
def test_b_prediction_ledger_hits_and_misses():
    m = FutureAnswerMemory(); m.ingest(week())
    m.supply("what do I know about Sam?")          # answers exist but stale → miss
    stale_misses = sum(a.misses for a in m._answers.values())
    m.marinate(100)                                 # night renders drafts
    pkg = m.supply("what do I know about Sam?")
    hits = sum(a.hits for a in m._answers.values())
    assert stale_misses >= 1, "wanting a stale answer must be recorded as a miss"
    assert hits >= 1 and pkg.blocks, "fresh draft should hit and serve"


def test_b_staleness_trigger_fires_on_new_evidence():
    m = FutureAnswerMemory(); m.ingest(week()); m.marinate(100)
    sam = [a for a in m._answers.values() if "sam" in a.id]
    assert sam and not sam[0].stale
    m.ingest([make_claim(text="Sam cancelled the Berlin meeting",
                         t_ms=1_500_000, frame="f-sam-4")])
    assert sam[0].stale, "new claim about Sam MUST invalidate Sam's draft"


def test_b_cap_prevents_infinite_drafts():
    m = FutureAnswerMemory()
    claims = [make_claim(text=f"unique{i} thing{i} happened{i} spot{i}",
                         t_ms=i * 1000, frame=f"f-u{i}") for i in range(3)]
    m.ingest(claims * 3)                            # every cue recurs ≥3
    assert len(m._answers) <= 200, "the FAQ cap is the design's discipline"


# ---------------------------------------------------------------- Design C
def test_c_cooccurrence_strengthens_and_answers_associatively():
    m = WiringMemory(); m.ingest(week())
    pkg = m.supply("Berlin Sam")
    assert pkg.blocks, "wired memory failed an associative question"
    assert all(b.claim_ids for b in pkg.blocks)
    key = ("berlin", "sam")
    assert key in m._edges and m._edges[key].weight >= 3.0


def test_c_aggregates_route_to_store_never_guess():
    m = WiringMemory(); m.ingest(week())
    pkg = m.supply("how many times did I buy coffee?")
    assert pkg.route_to_store and not pkg.blocks, \
        "activation counted — it must route, never guess"


def test_c_replay_decays_unfired_and_prunes_cold():
    m = WiringMemory(); m.ingest(week())
    w0 = m._edges[("berlin", "sam")].weight
    for _ in range(12):                             # many idle nights
        m.marinate(10_000)
    assert ("rain" not in {a for a, _ in m._edges} or
            all(e.weight < w0 for (a, b), e in m._edges.items() if a == "rain")), \
        "noise edges never cooled"
    assert m._edges.get(("berlin", "sam")) is None or \
        m._edges[("berlin", "sam")].weight < w0, "decay never touched unfired edges"


def test_c_hub_cap_bounds_pairs():
    m = WiringMemory()
    long_text = " ".join(f"word{i}" for i in range(30))
    m.ingest([make_claim(text=long_text, t_ms=1, frame="f-hub")])
    assert len(m._edges) <= 15, "hub explosion — pair cap failed"


def test_a_node_present_in_everything_is_never_a_belief_subject():
    """MEASURED on the founder's graph, 2026-08-20, 168 episodes:

        dev:mac-1        76.8% of episodes   (the device id)
        Brave Browser    76.2%               (his only browser)
        median node       1 episode

    Once the night could finally run, it spent that freedom relating those
    constants to everything: "dev:mac-1 and Thriller: both repeatedly present
    with youtube.com". The engine was correct and the material was not —
    "both repeatedly present with X" says nothing when X is present with
    everything. This is inverse document frequency, which the combination
    tier never had.
    """
    from tracemem.domain import Confidence, Grade, Node, NodeKind
    from tracemem.domain.node import Attribute
    from tracemem.nodes.combination.candidate_selector import (
        MIN_EPISODES_FOR_UBIQUITY, select_candidates, ubiquitous)
    from tracemem.nodes.graph_store import MemoryGraphStore
    from tracemem.domain import Edge, EdgeType

    g = MemoryGraphStore()
    episodes = ["e%d" % i for i in range(MIN_EPISODES_FOR_UBIQUITY + 10)]

    def add(nid, kind, eps):
        g.upsert_node(Node(
            id=nid, kind=kind, activation=0.9, last_active_t_ms=1000,
            attributes=[Attribute(key="k", value=nid, grade=Grade.VERBATIM,
                                  confidence=Confidence(0.9),
                                  source_episode_ids=tuple(eps), t_ms=1000)]))

    add("device", NodeKind.PLACE, episodes)              # in ALL of them
    add("thing_a", NodeKind.SITE, episodes[:4])
    add("thing_b", NodeKind.SITE, episodes[2:6])
    for a, b in (("device", "thing_a"), ("device", "thing_b"),
                 ("thing_a", "thing_b")):
        g.upsert_edge(Edge(src=a, dst=b, type=EdgeType.CO_OCCURS,
                           weight=1.0, evidence_count=3,
                           last_reinforced_t_ms=1000))

    assert "device" in ubiquitous(g)
    pairs = {frozenset((a.id, b.id))
             for a, b, _ in select_candidates(g, 1000, budget_b=50)}
    assert frozenset(("thing_a", "thing_b")) in pairs
    assert not any("device" in p for p in pairs), (
        "the device id was offered as the subject of a belief")


def test_ubiquity_does_not_fire_on_a_young_memory():
    """A gate that fires hardest when it knows least is worse than no gate:
    over two episodes, "in 100% of them" is a sample size of two, and this
    would switch combination off for every new user on their first day."""
    from tracemem.domain import Confidence, Grade, Node, NodeKind
    from tracemem.domain.node import Attribute
    from tracemem.nodes.combination.candidate_selector import ubiquitous
    from tracemem.nodes.graph_store import MemoryGraphStore

    g = MemoryGraphStore()
    g.upsert_node(Node(
        id="only", kind=NodeKind.SITE, activation=0.9, last_active_t_ms=1000,
        attributes=[Attribute(key="k", value="only", grade=Grade.VERBATIM,
                              confidence=Confidence(0.9),
                              source_episode_ids=("e1", "e2"), t_ms=1000)]))
    assert ubiquitous(g) == set()
