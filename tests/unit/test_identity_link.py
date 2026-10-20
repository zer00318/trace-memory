"""Cross-hemisphere identity, the conservative half (analysis gap E).

Same name → a weak SAME_AS 'might be' edge, stronger with shared context and
repetition, but never auto-merged. Different names never link.
"""
from __future__ import annotations

from tracemem.domain import (
    Confidence, Edge, EdgeType, Grade, Node, NodeKind,
)
from tracemem.domain.node import Attribute
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.nodes.marination.identity import (
    AUTO_MERGE_THRESHOLD, MERGE_THRESHOLD, auto_merge_pass, merge_identity,
    merge_ready, same_as_pass,
)


def _person(nid, name, episode="e1"):
    return Node(id=nid, kind=NodeKind.ENTITY,
                attributes=[Attribute(key="seen_as", value=name,
                                      grade=Grade.VERBATIM,
                                      confidence=Confidence(0.9),
                                      source_episode_ids=(episode,), t_ms=1000)])


def test_same_name_distinct_nodes_get_a_might_be_link_not_a_merge():
    g = MemoryGraphStore()
    g.upsert_node(_person("p:tessa:owned", "Tessa"))    # e.g. from mail (OWNED)
    g.upsert_node(_person("p:tessa:lived", "Tessa"))    # e.g. from screen (LIVED)
    g.upsert_node(_person("p:sam", "Sam"))

    touched = same_as_pass(g, t_ms=2000)
    assert touched == 1
    link = g.edge_between("p:tessa:owned", "p:tessa:lived", EdgeType.SAME_AS)
    assert link is not None
    assert link.weight < MERGE_THRESHOLD                # conservative: a candidate
    # different names never link
    assert g.edge_between("p:tessa:owned", "p:sam", EdgeType.SAME_AS) is None
    # and nothing was merged — both nodes still live
    assert g.get_node("p:tessa:owned") is not None
    assert g.get_node("p:tessa:lived") is not None
    assert merge_ready(g) == []                         # not yet strong enough


def test_shared_context_strengthens_the_candidate():
    g = MemoryGraphStore()
    g.upsert_node(_person("p:a", "Tessa"))
    g.upsert_node(_person("p:b", "Tessa"))
    g.upsert_node(Node(id="site:chess", kind=NodeKind.SITE))
    # both Tessas are seen at the same site → corroboration
    g.upsert_edge(Edge(src="p:a", dst="site:chess", type=EdgeType.CO_OCCURS))
    g.upsert_edge(Edge(src="p:b", dst="site:chess", type=EdgeType.CO_OCCURS))

    same_as_pass(g, t_ms=2000)
    with_ctx = g.edge_between("p:a", "p:b", EdgeType.SAME_AS).weight

    g2 = MemoryGraphStore()
    g2.upsert_node(_person("p:a", "Tessa"))
    g2.upsert_node(_person("p:b", "Tessa"))
    same_as_pass(g2, t_ms=2000)
    without_ctx = g2.edge_between("p:a", "p:b", EdgeType.SAME_AS).weight

    assert with_ctx > without_ctx


def test_repetition_across_nights_can_reach_the_merge_bar():
    g = MemoryGraphStore()
    g.upsert_node(_person("p:a", "Tessa"))
    g.upsert_node(_person("p:b", "Tessa"))
    g.upsert_node(Node(id="site:chess", kind=NodeKind.SITE))
    g.upsert_edge(Edge(src="p:a", dst="site:chess", type=EdgeType.CO_OCCURS))
    g.upsert_edge(Edge(src="p:b", dst="site:chess", type=EdgeType.CO_OCCURS))

    for night in range(6):                              # corroborated night after night
        same_as_pass(g, t_ms=2000 + night)
    ready = merge_ready(g)
    assert len(ready) == 1                              # now a deliberate merge MAY act
    assert ready[0].weight >= MERGE_THRESHOLD


def test_merge_identity_absorbs_knowledge_and_retires_the_other():
    """The deliberate merge: keep ends up knowing what both knew, and the
    absorbed node is retired (audited), not vaporized."""
    g = MemoryGraphStore()
    g.upsert_node(_person("keep", "Tessa"))
    absorb = _person("absorb", "Tessa")
    absorb.attributes.append(Attribute(key="relationship", value="neighbor",
                                        grade=Grade.VERBATIM,
                                        confidence=Confidence(0.9),
                                        source_episode_ids=("e2",), t_ms=1000))
    g.upsert_node(absorb)

    assert merge_identity(g, "keep", "absorb", t_ms=9) is True
    assert g.get_node("absorb") is None                 # folded away
    keep = g.get_node("keep")
    assert "neighbor" in [a.value for a in keep.attributes]   # absorbed the knowledge
    assert "absorb" in {n.id for n, _t in g.retired()}       # audited in the graveyard


def test_auto_merge_acts_only_on_overwhelming_evidence():
    g = MemoryGraphStore()
    g.upsert_node(_person("keep", "Tessa"))
    g.upsert_node(_person("weak_a", "Sam"))
    g.upsert_node(_person("weak_b", "Sam"))
    # an overwhelming Tessa candidate (auto bar), and a weak Sam candidate (not)
    g.upsert_node(_person("absorb", "Tessa"))
    g.upsert_edge(Edge(src="keep", dst="absorb", type=EdgeType.SAME_AS,
                       weight=0.97))
    g.upsert_edge(Edge(src="weak_a", dst="weak_b", type=EdgeType.SAME_AS,
                       weight=0.5))

    merged = auto_merge_pass(g, t_ms=9)
    assert merged == [("keep", "absorb")]              # only the overwhelming pair
    assert g.get_node("absorb") is None                # fused
    assert g.get_node("weak_a") is not None            # weak pair untouched
    assert g.get_node("weak_b") is not None


def test_one_node_carrying_two_names_does_not_bridge_two_strangers():
    """THE WORST ERROR THIS MEMORY CAN MAKE, found on the live graph
    2026-08-24: ONE node carrying sixteen people —

        Bartholomew · Bart · Rory · Pam · Rosa · Hana · Nora · Fred ·
        Cora · Quentin · Wade · Adele · Vince · Reid · Ivy · Eli

    `same_as_pass` groups nodes by EACH name they carry. A node that
    legitimately holds two — "Bart" and "Bartholomew", a correct prefix match —
    gets a SAME_AS edge to every node named Bart AND every node named
    Bartholomew. Both reinforce +0.1 a night to the 0.95 bar, both merge in, and
    the fused node then carries more names and bridges wider. Four nights a
    hop; six days of capture was enough.

    Merging two people is not a wrong answer, it is a wrong PERSON — silently,
    in every answer afterwards, with both sets of evidence folded together so
    the mistake looks corroborated.
    """
    from tracemem.domain import Confidence, Edge, EdgeType, Grade, Node, NodeKind
    from tracemem.domain.node import Attribute
    from tracemem.nodes.accrual import names_of
    from tracemem.nodes.graph_store import MemoryGraphStore
    from tracemem.nodes.marination.identity import (auto_merge_pass,
                                                    same_as_pass)

    def person(nid, names):
        return Node(id=nid, kind=NodeKind.ENTITY, activation=0.9,
                    last_active_t_ms=1000,
                    attributes=[Attribute("seen_as", nm, Grade.VERBATIM,
                                          Confidence(0.9), ("e1", "e2"), 1000)
                                for nm in names])

    g = MemoryGraphStore()
    g.upsert_node(person("hana", ["Hana"]))
    g.upsert_node(person("nora", ["Nora"]))
    g.upsert_node(person("bridge", ["Hana", "Nora"]))
    g.upsert_node(Node(id="s", kind=NodeKind.SITE, activation=0.9,
                       last_active_t_ms=1000,
                       attributes=[Attribute("site", "youtube.com",
                                             Grade.VERBATIM, Confidence(0.9),
                                             ("e1", "e2"), 1000)]))
    for nid in ("hana", "nora", "bridge"):
        g.upsert_edge(Edge(src=nid, dst="s", type=EdgeType.CO_OCCURS,
                           weight=5.0, evidence_count=5,
                           last_reinforced_t_ms=1000))

    for night in range(1, 10):
        same_as_pass(g, 1000 + night)
        auto_merge_pass(g, 1000 + night)

    people = {n.id for n in g.nodes.values() if n.kind is NodeKind.ENTITY}
    assert {"hana", "nora"} <= people, "two strangers were fused into one"
    assert "Nora" not in names_of(g.get_node("hana"))


def test_a_real_duplicate_still_merges():
    """The guard must not become "never merge". Two nodes both named Hana
    are what same_as_pass is FOR, and prefix variants of one name
    ("Ben"/"Benjamin") remain one person."""
    from tracemem.domain import Confidence, Edge, EdgeType, Grade, Node, NodeKind
    from tracemem.domain.node import Attribute
    from tracemem.nodes.graph_store import MemoryGraphStore
    from tracemem.nodes.marination.identity import (auto_merge_pass,
                                                    one_identity,
                                                    same_as_pass)

    assert one_identity(["Ben", "Benjamin"]) is True
    assert one_identity(["Hana", "Nora"]) is False
    assert one_identity(["Hana", "a person"]) is True

    def person(nid, name, eps):
        return Node(id=nid, kind=NodeKind.ENTITY, activation=0.9,
                    last_active_t_ms=1000,
                    attributes=[Attribute("seen_as", name, Grade.VERBATIM,
                                          Confidence(0.9), tuple(eps), 1000)])

    g = MemoryGraphStore()
    g.upsert_node(person("a", "Hana", ["e1", "e2"]))
    g.upsert_node(person("b", "Hana", ["e3", "e4"]))
    g.upsert_node(Node(id="s", kind=NodeKind.SITE, activation=0.9,
                       last_active_t_ms=1000,
                       attributes=[Attribute("site", "youtube.com",
                                             Grade.VERBATIM, Confidence(0.9),
                                             ("e1", "e3"), 1000)]))
    for nid in ("a", "b"):
        g.upsert_edge(Edge(src=nid, dst="s", type=EdgeType.CO_OCCURS,
                           weight=5.0, evidence_count=5,
                           last_reinforced_t_ms=1000))

    merged = []
    for night in range(1, 10):
        same_as_pass(g, 1000 + night)
        merged += auto_merge_pass(g, 1000 + night)
    assert merged, "two nodes with the same name never merged"
