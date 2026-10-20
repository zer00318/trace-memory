"""Ruling A5: late boundaries re-key; the graph follows the tombstone map."""
from __future__ import annotations

from tests.helpers import make_claim
from tracemem.binder.reconcile import BoundaryReconciler
from tracemem.domain import Attribute, Confidence, Grade, Node, NodeKind
from tracemem.nodes.graph_store import MemoryGraphStore
from tracemem.nodes.marination.remap import remap


def test_split_then_remap_repairs_graph_sources():
    claims = [make_claim(text="t%d" % t, t_ms=t, frame="f%d" % t)
              for t in (100, 900, 1500)]
    rec = BoundaryReconciler(claim_source=lambda place, day: claims)
    first = rec.apply_late_boundaries("wifi:aaaa", "d0", [2000])
    old_ep = next(iter(first.episodes.values()))

    g = MemoryGraphStore()
    node = Node(id="n", kind=NodeKind.TOPIC, attributes=[
        Attribute("topic", "x", Grade.GIST, Confidence(0.8), (old_ep.id,), 0)])
    g.upsert_node(node)

    second = rec.apply_late_boundaries("wifi:aaaa", "d0", [1000])   # the late split
    affected = remap(g, rec.successor_map)

    assert affected == ["n"]
    new_ids = {e.id for e in second.episodes.values()}
    srcs = set(g.nodes["n"].attributes[0].source_episode_ids)
    assert srcs and srcs <= new_ids                     # every source resolvable
    assert old_ep.id not in srcs
    assert g.nodes["n"].activation >= 0.3               # stale -> hot -> re-examined


def test_idempotent_boundaries_tombstone_nothing():
    claims = [make_claim(text="a", t_ms=100)]
    rec = BoundaryReconciler(claim_source=lambda place, day: claims)
    rec.apply_late_boundaries("w", "d0", [2000])
    again = rec.apply_late_boundaries("w", "d0", [2000])
    assert again.tombstoned == {}
