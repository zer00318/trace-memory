"""OWNS: the temporal succession pass — PRECEDES edges, so causal thought is
REACHABLE (fixing the audited hole: causal fired only on PRECEDES edges and
nothing ever built one — the system's only genuinely-new-thought path was
dead code).

Consecutive episodes within a gap window hand succession from A's dominant
activity nodes to B's. Repetition accrues evidence_count; the causal kind
refuses to spend tokens below MIN_PRECEDENCE_COUNT, so one coincidence never
becomes a hypothesis — only a RHYTHM does. Idempotent per episode pair.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Set, Tuple

from tracemem.domain import Edge, EdgeType, NodeKind

GAP_MAX_MS = 6 * 3_600_000        # activities more than 6h apart don't "precede"
_DOMINANT_KINDS = {NodeKind.TOPIC, NodeKind.OBJECT, NodeKind.APP}


def _pair_ledger(graph_store) -> Set[Tuple[str, str]]:
    if not hasattr(graph_store, "succession_ledger"):
        graph_store.succession_ledger = set()
    return graph_store.succession_ledger


def _nodes_by_episode(graph_store) -> Dict[str, List[str]]:
    idx: Dict[str, List[str]] = defaultdict(list)
    for node in graph_store.nodes.values():
        if node.kind not in _DOMINANT_KINDS or node.derivation_depth != 0:
            continue
        for a in node.attributes:
            for ep in a.source_episode_ids:
                if node.id not in idx[ep]:
                    idx[ep].append(node.id)
    return idx


def succession_pass(episodes, graph_store, gap_max_ms: int = GAP_MAX_MS) -> int:
    """Returns how many PRECEDES observations were recorded this pass."""
    eps = sorted(episodes, key=lambda e: (e.t_range.start_ms, e.id))
    by_ep = _nodes_by_episode(graph_store)
    ledger = _pair_ledger(graph_store)
    recorded = 0
    for a, b in zip(eps, eps[1:]):
        if (a.id, b.id) in ledger:
            continue
        if b.t_range.start_ms - a.t_range.end_ms > gap_max_ms:
            continue
        ledger.add((a.id, b.id))
        t = b.t_range.start_ms
        for na in by_ep.get(a.id, ()):
            for nb in by_ep.get(b.id, ()):
                if na == nb:
                    continue
                edge = graph_store.edge_between(na, nb, EdgeType.PRECEDES)
                if edge is None:
                    graph_store.upsert_edge(Edge(src=na, dst=nb,
                                                 type=EdgeType.PRECEDES,
                                                 weight=1.0, evidence_count=1,
                                                 last_reinforced_t_ms=t))
                else:
                    edge.evidence_count += 1
                    edge.weight += 0.5
                    edge.last_reinforced_t_ms = t
                    graph_store.upsert_edge(edge)
                recorded += 1
    return recorded
