"""Supplier: cue steering, scratch-only retrieval, budget-capped packing."""
from __future__ import annotations

from tests.fakes import HashEmbedder
from tracemem.domain import Attribute, Confidence, Edge, EdgeType, Grade, Node, NodeKind
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.supplier.cue import Precision, parse_cue
from tracemem.supplier.packer import pack
from tracemem.supplier.retriever import retrieve
from tracemem.supplier.supplier import supply
from tracemem.supplier.vector_index import VectorIndex


def topic(nid, text, activation=0.5):
    return Node(id=nid, kind=NodeKind.TOPIC, activation=activation, attributes=[
        Attribute("topic", text, Grade.GIST, Confidence(0.8), ("e-" + nid,), 0)])


def test_cue_precision_steering():
    assert parse_cue("what did I usually do?").precision == Precision.PATTERN
    assert parse_cue('what were the "exact words"?').precision == Precision.VERBATIM
    assert parse_cue("where did I put the keys?").precision == Precision.FACT
    assert parse_cue("kiln").precision == Precision.HYBRID


def test_retrieval_spreads_to_linked_neighbors():
    g = MemoryGraphStore()
    g.upsert_node(topic("pottery", "kiln firing glaze pottery"))
    g.upsert_node(topic("maya", "Maya ceramics friend"))
    g.upsert_edge(Edge("pottery", "maya", EdgeType.CO_OCCURS, weight=2.0))
    idx = VectorIndex(HashEmbedder())
    idx.rebuild_from(g)
    got = {c.node.id for c in retrieve(parse_cue("kiln firing"), g, idx)}
    assert "pottery" in got and "maya" in got               # entry + 1-hop spread


def test_supply_end_to_end_grounded_blocks():
    g = MemoryGraphStore()
    g.upsert_node(topic("pottery", "kiln firing glaze pottery"))
    idx = VectorIndex(HashEmbedder())
    idx.rebuild_from(g)
    ctx = supply(parse_cue("kiln?"), g, None, idx, budget_tokens=500)
    assert ctx.blocks and all(b.source_ids for b in ctx.blocks)   # S3: sourced
    assert ctx.tokens <= 500
