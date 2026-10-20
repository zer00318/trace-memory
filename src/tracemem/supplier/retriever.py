"""OWNS: entry by embedding (the door) + spread on a SCRATCH copy (05 §2, S2).

A query must not mutate the graph's real activation — else asking about X
forever self-reinforces X (spec 05 R2 decision: no mutation in v1).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from tracemem.domain.activation import SPREAD_ALPHA

CANDIDATE_CAP = 200


@dataclass(frozen=True)
class Candidate:
    node: object
    entry_sim: float
    scratch_activation: float


def retrieve(cue, graph_store, vector_index, k_entry: int = 12,
             hops: int = 2) -> List[Candidate]:
    entries = vector_index.topk(cue.text, k_entry)
    scratch: Dict[str, float] = {}
    sims: Dict[str, float] = {}
    for nid, sim in entries:
        scratch[nid] = max(scratch.get(nid, 0.0), sim)
        sims[nid] = sim
    # spread on the scratch dict, never on stored nodes
    frontier = list(scratch.keys())
    for _ in range(hops):
        nxt = []
        for nid in frontier:
            base = scratch.get(nid, 0.0)
            for e in graph_store.edges_of(nid):
                other = e.dst if e.src == nid else e.src
                gain = base * SPREAD_ALPHA
                if gain > scratch.get(other, 0.0):
                    scratch[other] = gain
                    nxt.append(other)
        frontier = nxt
    out = []
    for nid, act in sorted(scratch.items(), key=lambda t: t[1], reverse=True)[:CANDIDATE_CAP]:
        node = graph_store.get_node(nid)
        if node is not None:
            out.append(Candidate(node=node, entry_sim=sims.get(nid, 0.0),
                                 scratch_activation=act))
    return out
