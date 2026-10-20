"""Combination's work list must cost one pass over the graph, not one per hot node.

WITNESSED 2026-10-14 on the live daemon (669 nodes, dense co-occurrence):
night slices budgeted at 10s spent 9.7-19.4s inside N3, and every sampled
owner-thread stack was under `select_candidates` -- decrypting neighbour
nodes, re-serialising both nodes' attributes for every pair's fingerprint,
and querying edges per pair.  A two-hop neighbourhood of a dense graph is
most of the graph, so each hot node re-decrypted nearly every node, and the
deadline was only polled between hot nodes.  A question asked while the
night ran waited for all of it.
"""
from __future__ import annotations

from tracemem.domain import Attribute, Confidence, Edge, EdgeType, Grade, Node, NodeKind
from tracemem.domain.activation import activation_now
from tracemem.nodes.combination import candidate_selector as cs
from tracemem.nodes.graph_store import pair_key
from tracemem.nodes.sqlite_graph_store import SqliteGraphStore

N = 24
T = 1000


def _graph():
    g = SqliteGraphStore(":memory:")
    for i in range(N):
        n = Node(id="n%02d" % i, kind=NodeKind.SITE,
                 attributes=[Attribute(key="site", value="site-%d.com" % i,
                                       grade=Grade.VERBATIM,
                                       confidence=Confidence(1.0),
                                       source_episode_ids=("ep-1", "ep-2"),
                                       t_ms=T)])
        n.activation, n.last_active_t_ms = 0.3 + 0.02 * i, T
        n.salience_ema = 0.01 * i
        g.upsert_node(n)
    for i in range(N):
        for j in range(i + 1, N):
            if (i + j) % 3:
                g.upsert_edge(Edge(src="n%02d" % i, dst="n%02d" % j,
                                   type=(EdgeType.PRECEDES if (i * j) % 5 == 0
                                         else EdgeType.CO_OCCURS)))
    g.record_ledger(pair_key("n00", "n05"), "scanned",
                    cs.evidence_fingerprint(g.get_node("n00"),
                                            g.get_node("n05")))
    return g


def _reference(graph_store, t_ms, budget_b, theta_hot=0.3):
    """The algorithm as it stood before 2026-10-14, verbatim in substance."""
    everywhere = cs.ubiquitous(graph_store)
    hot = [h for h in graph_store.nodes_with_activation_ge(theta_hot, t_ms)
           if cs.corroborated(h) and h.id not in everywhere]
    scored = {}
    for h in hot:
        for n in graph_store.neighbors(h.id, hops=2):
            if not cs.corroborated(n) or n.id in everywhere:
                continue
            key = pair_key(h.id, n.id)
            if key in scored:
                continue
            if graph_store.ledger_fresh(key, cs.evidence_fingerprint(h, n)):
                continue
            scored[key] = (h, n, cs.priority(h, n, graph_store, t_ms))
    ranked = sorted(scored.values(), key=lambda t: t[2], reverse=True)
    return ranked[:budget_b]


def _ids(pairs):
    return [(a.id, b.id, round(p, 12)) for a, b, p in pairs]


def test_the_work_list_is_unchanged():
    g = _graph()
    assert _ids(cs.select_candidates(g, T, 1000)) == _ids(_reference(g, T, 1000))
    assert _ids(cs.select_candidates(g, T, 7)) == _ids(_reference(g, T, 7))


def test_each_node_is_decrypted_once_per_selection():
    g = _graph()
    opened = []
    real = g._node_from_row

    def counting(row):
        opened.append(row[0])
        return real(row)

    g._node_from_row = counting
    cs.select_candidates(g, T, 1000)
    # the hot list and ubiquity read the graph once each; neighbours must
    # come from that, not from a fresh decrypt per hot node
    assert len(opened) <= 3 * N, (
        "%d node decrypts for a %d-node graph" % (len(opened), N))


def test_the_deadline_is_polled_inside_a_neighbourhood():
    g = _graph()

    class Lease:
        polls = 0

        def preempted(self):
            Lease.polls += 1
            return Lease.polls > 5

    scored = []
    real = cs.priority

    def counting(*a, **k):
        scored.append(1)
        return real(*a, **k)

    cs.priority, saved = counting, cs.priority
    try:
        cs.select_candidates(g, T, 1000, lease=Lease())
    finally:
        cs.priority = saved
    assert len(scored) <= 5, (
        "%d pairs scored after the deadline passed" % len(scored))


def _old_fingerprint(node_a, node_b):
    import hashlib
    import json

    def evidence(node):
        attrs = [{"key": a.key, "value": a.value, "grade": int(a.grade),
                  "confidence": a.confidence.value,
                  "episodes": sorted(a.source_episode_ids),
                  "first_t_ms": a.first_t_ms, "t_ms": a.t_ms}
                 for a in node.attributes]
        attrs.sort(key=lambda a: (a["key"], a["value"], a["episodes"],
                                  a["first_t_ms"], a["t_ms"]))
        return {"id": node.id, "kind": node.kind.value,
                "depth": node.derivation_depth,
                "builder_version": node.builder_version, "attributes": attrs}

    payload = sorted((evidence(node_a), evidence(node_b)), key=lambda n: n["id"])
    sig = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(sig.encode()).hexdigest()[:16]


def test_the_pair_ledger_on_disk_still_matches():
    """Every pair fingerprint already in the founder's ledger was written by
    the old serialisation. A byte of difference would re-open every pair
    the night has ever judged."""
    g = _graph()
    memo = {}
    nodes = [g.get_node("n%02d" % i) for i in range(N)]
    nodes[3].attributes[0].value = "naïve — ünïcode \"quoted\""
    for a in nodes:
        for b in nodes[:6]:
            assert cs.evidence_fingerprint(a, b, memo) == _old_fingerprint(a, b)
            assert cs.evidence_fingerprint(a, b) == _old_fingerprint(a, b)
