"""A derived belief records its vintage (SCHEMA §3.4 / analysis rec. 2).

The judge stamps every meta node it mints with the code version that built it,
so replay can reproduce a specific past version's output and 'you used to think
X' can name which organ thought it.
"""
from __future__ import annotations

from tracemem import __version__
from tracemem.domain import Confidence, Grade, Node, NodeKind, node_id
from tracemem.domain.node import Attribute
from tracemem.nodes.combination.judge import BUILDER_VERSION, judge
from tracemem.nodes.combination.proposal import Proposal
from tracemem.nodes.graph_store import MemoryGraphStore


def _parent(nid):
    return Node(id=nid, kind=NodeKind.ENTITY, derivation_depth=0,
                attributes=[Attribute(key="seen_as", value=nid, grade=Grade.VERBATIM,
                                      confidence=Confidence(0.9),
                                      source_episode_ids=("e1",), t_ms=1000)])


def test_builder_version_tracks_the_code_version():
    assert BUILDER_VERSION == __version__


def test_a_judged_meta_node_carries_the_code_vintage():
    g = MemoryGraphStore()
    g.upsert_node(_parent("a"))
    g.upsert_node(_parent("b"))
    prop = Proposal(kind="pattern", claim_text="a and b recur together",
                    parent_node_ids=("a", "b"), supported_by=("e1",))
    j = judge(prop, g, t_ms=2000)
    assert j.accepted
    assert j.node.builder_version == __version__
    # and it survives a store round trip
    g.upsert_node(j.node)
    assert g.get_node(j.node.id).builder_version == __version__
