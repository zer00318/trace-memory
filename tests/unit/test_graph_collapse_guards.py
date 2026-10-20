"""ORACLES for the four defects the first production night run exposed.

Context (campaign/SESSION_HANDOFF_20260729.md §4.5): the night was wired and
run against a copy of the founder's real store for the first time in the
product's history. It produced 781 nodes and 265,577 edges — 87% of a complete
graph — of which 727 nodes were hypotheses reading

    "cb450235-… and 94efc3b5-…: both repeatedly present with acaa327f-…"

Each test below pins one of the four mechanisms that produced that, so the
collapse cannot come back quietly.
"""
from __future__ import annotations

from tracemem.domain import (
    Attribute, Confidence, Edge, EdgeType, Grade, Node, NodeKind,
)
from tracemem.nodes.accrual import label_of, name_or_id
from tracemem.nodes.combination.kinds import transitive
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.nodes.marination.background import (
    BIRTH_CONFIDENCE_FLOOR, _confident_enough,
)
from tracemem.nodes.marination.semantic import semantic_pass


def _node(nid, value, kind=NodeKind.SITE, depth=0, parents=()):
    return Node(id=nid, kind=kind, derivation_depth=depth, parent_ids=parents,
                attributes=[Attribute(key="site", value=value,
                                      grade=Grade.VERBATIM,
                                      confidence=Confidence(1.0),
                                      source_episode_ids=("ep-1",), t_ms=1000)])


class OneHotEmbedder:
    """Identical text → identical vector → cosine 1.0. Makes the template
    collision that caused the real explosion reproducible in one line."""

    def embed(self, text):
        import hashlib
        h = hashlib.sha256(text.encode()).digest()
        return [b / 255.0 for b in h[:16]]


# -- D1: a belief must be readable, or it cannot be graded ----------------

def test_derived_claim_text_names_things_instead_of_uuids():
    """F-GRADE makes the founder's grade the highest evidence in the system.
    A belief he cannot read is a belief he cannot grade."""
    g = MemoryGraphStore()
    a, b, via = _node("id-a", "chess.com"), _node("id-b", "lichess.org"), \
        _node("id-via", "Brave Browser", kind=NodeKind.APP)
    for n in (a, b, via):
        g.upsert_node(n)
    for n in (a, b):
        g.upsert_edge(Edge(src=n.id, dst=via.id, type=EdgeType.CO_OCCURS))

    prop = transitive.detect(a, b, g)
    assert not prop.is_none
    assert "chess.com" in prop.claim_text
    assert "lichess.org" in prop.claim_text
    assert "Brave Browser" in prop.claim_text
    # the actual regression: no raw uuid anywhere in a belief
    assert "id-a" not in prop.claim_text
    assert "id-via" not in prop.claim_text


def test_label_of_prefers_the_best_witnessed_value():
    n = _node("n", "chess.com")
    n.attributes.append(Attribute(key="site", value="chesa.com",
                                  grade=Grade.GIST, confidence=Confidence(0.5),
                                  source_episode_ids=("ep-2",), t_ms=9999))
    assert label_of(n) == "chess.com"      # higher grade beats mere recency


def test_name_or_id_never_returns_empty():
    """A belief that names nothing cannot be judged, deduped, or read."""
    ghost = Node(id="abcdef1234", kind=NodeKind.ENTITY)
    assert name_or_id(ghost)
    assert name_or_id(None, "fallback-id")


# -- D3: a ubiquitous via composes everything and distinguishes nothing ---

def test_a_ubiquitous_via_cannot_compose_a_hypothesis():
    """One node neighbouring most of the graph made 727 pairs look related.
    The rule already existed for PLACE; it is now measured, not hardcoded."""
    g = MemoryGraphStore()
    hub = _node("hub", "Brave Browser", kind=NodeKind.APP)
    g.upsert_node(hub)
    others = [_node("n%d" % i, "site%d.com" % i) for i in range(20)]
    for n in others:                       # the hub touches EVERYTHING
        g.upsert_node(n)
        g.upsert_edge(Edge(src=n.id, dst=hub.id, type=EdgeType.CO_OCCURS))

    assert transitive.detect(others[0], others[1], g).is_none


def test_a_discriminating_via_still_composes():
    """The guard must not silence the whole kind — a via that links a FEW
    things is exactly the signal transitive composition exists to find."""
    g = MemoryGraphStore()
    via = _node("via", "the kiln project", kind=NodeKind.TOPIC)
    a, b = _node("a", "Sam"), _node("b", "Priya")
    filler = [_node("f%d" % i, "unrelated%d.com" % i) for i in range(20)]
    for n in [via, a, b] + filler:
        g.upsert_node(n)
    for n in (a, b):                       # via touches only these two
        g.upsert_edge(Edge(src=n.id, dst=via.id, type=EdgeType.CO_OCCURS))

    prop = transitive.detect(a, b, g)
    assert not prop.is_none
    assert "the kiln project" in prop.claim_text


# -- D2: semantic wiring is about things, not about our own prose ---------

def test_semantic_wiring_ignores_derived_nodes():
    """727 hypotheses from one template embedded near-identically and minted
    263,931 edges. Meaning is a property of things, not of sentences we
    wrote about them."""
    g = MemoryGraphStore()
    metas = []
    for i in range(30):
        m = Node(id="m%d" % i, kind=NodeKind.HYPOTHESIS, derivation_depth=1,
                 parent_ids=("p",),
                 attributes=[Attribute(key="claim",
                                       value="X and Y: both repeatedly present with Z",
                                       grade=Grade.DERIVED,
                                       confidence=Confidence(0.5),
                                       source_episode_ids=("ep-1",), t_ms=1)])
        g.upsert_node(m)
        metas.append(m)

    touched = semantic_pass(g, OneHotEmbedder(), metas, t_ms=2000)
    assert touched == 0
    assert not [e for e in g.edges.values() if e.type == EdgeType.SEMANTIC]


def test_semantic_wiring_still_works_on_the_entity_tier():
    g = MemoryGraphStore()
    a, b = _node("a", "croissant"), _node("b", "croissant")
    for n in (a, b):
        g.upsert_node(n)
    assert semantic_pass(g, OneHotEmbedder(), [a, b], t_ms=2000) == 1


def test_semantic_pass_is_capped_so_it_can_never_go_quadratic_again(monkeypatch):
    """The cap is exercised at a small scale on purpose. Running it at the
    real MAX_PAIRWISE_NODES is itself quadratic, which is the very property
    under test — an oracle that takes minutes is an oracle nobody runs."""
    from tracemem.nodes.marination import semantic as sem
    monkeypatch.setattr(sem, "MAX_PAIRWISE_NODES", 10)
    g = MemoryGraphStore()
    nodes = []
    for i in range(40):
        n = _node("n%d" % i, "same text every time")
        g.upsert_node(n)
        nodes.append(n)
    touched = sem.semantic_pass(g, OneHotEmbedder(), nodes, t_ms=2000)
    assert touched == 10 * 9 // 2          # exactly the cap's worth, not 40's


def test_edge_between_is_indexed_not_scanned():
    """Was a linear walk of every edge, called once per PAIR by semantic_pass
    and once per pairing by upkeep's _edge(). See the note in graph_store."""
    g = MemoryGraphStore()
    g.upsert_edge(Edge(src="a", dst="b", type=EdgeType.CO_OCCURS))
    assert g.edge_between("a", "b", EdgeType.CO_OCCURS) is not None
    assert g.edge_between("b", "a", EdgeType.CO_OCCURS) is not None   # undirected
    assert g.edge_between("a", "b") is not None                       # any type
    assert g.edge_between("a", "b", EdgeType.SEMANTIC) is None
    assert g.edge_between("a", "zzz") is None


# -- D4: the perceiver's own doubt must reach the birth decision ----------

def test_a_low_confidence_claim_may_not_mint_a_belief():
    """`chesa.com` (0.5) stood in the graph as a peer of `chess.com` (1.0).
    Grade could not catch it: grade measures transcription fidelity, not
    whether the thing transcribed is real."""
    class _C:
        def __init__(self, v):
            self.confidence = Confidence(v)

    assert _confident_enough(_C(1.0))
    assert not _confident_enough(_C(0.5))
    assert not _confident_enough(_C(0.3))
    assert _confident_enough(_C(BIRTH_CONFIDENCE_FLOOR))


def test_a_missing_source_claim_still_passes():
    """Synthetic and backfilled mentions carry no source claim. Refusing them
    would silently empty the graph — worse than the defect being fixed."""
    assert _confident_enough(None)


def test_the_raw_claim_is_never_discarded_by_the_floor():
    """The napkin's rule, applied at birth: discard the meta, NOT the raw.
    The floor lives in marination and the pruner holds no ClaimStore — so
    there is structurally no path from a birth refusal to a raw deletion."""
    import inspect
    from tracemem.nodes.marination import background
    src = inspect.getsource(background)
    for forbidden in ("claim_log", "ClaimLog", "DELETE", "delete_claim"):
        assert forbidden not in src


# -- D6: a thing seen once is not yet a thing ------------------------------

def _seen_in(nid, value, episodes):
    return Node(id=nid, kind=NodeKind.SITE,
                attributes=[Attribute(key="site", value=value,
                                      grade=Grade.VERBATIM,
                                      confidence=Confidence(1.0),
                                      source_episode_ids=tuple(episodes),
                                      t_ms=1000)])


def test_a_thing_seen_once_may_not_become_the_subject_of_a_belief():
    """From the founder's first real digest: `esa.com`, `youtube.co`,
    `Northside`, `before further edits` — OCR truncations and sentence
    fragments that Vision was CONFIDENT about, so the birth floor could not
    catch them. What separates them from real things is that they never came
    back."""
    from tracemem.nodes.combination.candidate_selector import corroborated
    assert not corroborated(_seen_in("n", "esa.com", ["ep-1"]))
    assert corroborated(_seen_in("n", "chess.com", ["ep-1", "ep-2"]))


def test_derived_nodes_are_exempt_from_corroboration():
    """A meta node's standing comes from its parents and the judge — it was
    never 'seen' at all, so re-sighting is not a coherent test for it."""
    from tracemem.nodes.combination.candidate_selector import corroborated
    meta = Node(id="m", kind=NodeKind.PATTERN, derivation_depth=1,
                parent_ids=("p",),
                attributes=[Attribute(key="claim", value="x",
                                      grade=Grade.DERIVED,
                                      confidence=Confidence(0.7),
                                      source_episode_ids=("ep-1",), t_ms=1)])
    assert corroborated(meta)


def test_uncorroborated_nodes_are_dropped_from_candidate_selection():
    from tracemem.nodes.combination.candidate_selector import select_candidates
    g = MemoryGraphStore()
    once = _seen_in("once", "esa.com", ["ep-1"])
    twice_a = _seen_in("twice_a", "chess.com", ["ep-1", "ep-2"])
    twice_b = _seen_in("twice_b", "youtube.com", ["ep-1", "ep-2"])
    for n in (once, twice_a, twice_b):
        n.activation, n.last_active_t_ms = 0.9, 1000
        g.upsert_node(n)
    for other in (once, twice_b):
        g.upsert_edge(Edge(src=twice_a.id, dst=other.id,
                           type=EdgeType.CO_OCCURS))

    pairs = select_candidates(g, t_ms=1000, budget_b=50)
    named = {frozenset((a.id, b.id)) for a, b, _ in pairs}
    assert frozenset(("twice_a", "twice_b")) in named
    assert not any("once" in p for p in named)


def test_the_raw_claim_and_the_node_both_survive_the_gate():
    """Corroboration gates COMBINATION, not birth. A thing that recurs
    graduates on its own with no special case."""
    from tracemem.nodes.combination.candidate_selector import corroborated
    n = _seen_in("n", "chess.com", ["ep-1"])
    assert not corroborated(n)
    n.attributes[0].source_episode_ids = ("ep-1", "ep-2")   # the world repeats
    assert corroborated(n)
