"""OWNS: semantic wiring — nodes close in MEANING grow an edge (analysis gap #5).

The graph's only associations were co-occurrence (seen in the same moment) and
geometry (located-at). That can never cluster croissant with sandwich unless
they happened to be seen together — meaning was not a force in the wiring. This
pass adds it: embed each node's label, and where two labels are close in
embedding space, mint a SEMANTIC edge. Category clustering ('food') then
EMERGES from proximity, with no hardcoded ontology — the lexicon-free path the
doctrine demands.

Bounded by construction: it wires only within a supplied node set (the night's
active set), pairwise — cost scales with the hot set, never with |V|. The
embedder is a PORT (Embedder protocol); production uses sentence-transformers
on CPU, tests use a fake, and this module never imports either.
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence

from tracemem.domain import Edge, EdgeType, Node

DEFAULT_THRESHOLD = 0.6         # cosine floor for "close enough in meaning"


# ONE label function for the whole system (moved to nodes/accrual.py
# 2026-07-29 so the founder, the embedder and the mouth all read a node the
# same way). Re-exported here because this module's callers import it.
from tracemem.nodes.accrual import label_of  # noqa: E402,F401

# How many nodes may be wired in one pass. This loop is O(n²) in the hot set,
# and the first production night run put 727 freshly-born hypotheses into that
# set at once — 263,931 edges, 87% of a complete graph. The depth filter below
# is the real fix; this is the wall that keeps a future mistake from being
# quadratic. UNCALIBRATED.
MAX_PAIRWISE_NODES = 400


def _cosine(u: Sequence[float], v: Sequence[float]) -> float:
    dot = sum(a * b for a, b in zip(u, v))
    nu = math.sqrt(sum(a * a for a in u))
    nv = math.sqrt(sum(b * b for b in v))
    if nu == 0.0 or nv == 0.0:
        return 0.0
    return dot / (nu * nv)


def semantic_pass(graph_store, embedder, nodes: List[Node], t_ms: int,
                  threshold: float = DEFAULT_THRESHOLD,
                  since_t_ms: Optional[int] = None) -> int:
    """Wire nodes that are close in MEANING, and report only real work.

    `since_t_ms` is what stops this from deepening the same edge forever.
    "Meaning that keeps mattering deepens" (test_semantic_wiring) is the
    intended behaviour and it is kept — but it was written assuming this pass
    runs once per NIGHT. The night watchman slices it every 30 seconds, so on
    the founder's live graph the same pairs were reinforced thousands of
    times: semantic edges reached weight 6,927 and evidence_count 3,130, for
    a cosine that is bounded by 1.0. That is the codebase's own most-feared
    failure class — a graph that grows more confident the more often you
    restart it — running every half minute.

    A recomputed cosine over two nodes that have not moved is NOT new
    evidence. So an existing edge deepens only when one of its endpoints has
    been active since the last pass; otherwise its weight is refreshed to the
    current similarity (idempotent) and its evidence count left alone.

    The return value counts only edges CREATED or genuinely deepened, because
    the night's stand-down guard reads it as forward progress — and reporting
    repeated no-op work as progress is precisely what kept the night spinning
    through 55 consecutive barren slices after the 2026-07-29 fix.
    """
    """Mint/reinforce SEMANTIC edges among `nodes`. Returns edges touched.

    Reinforcement (not overwrite) means a pairing that stays semantically
    close across nights deepens, exactly like the Hebbian co-occurrence edge —
    meaning that keeps mattering gets stronger.
    """
    # MEANING IS A PROPERTY OF THINGS, NOT OF SENTENCES WE WROTE ABOUT THEM
    # (2026-07-29). This pass exists to cluster croissant with sandwich — real
    # things observed in the world. Derived nodes are the system's own prose,
    # generated from a handful of templates, so their embeddings collide with
    # each other rather than with anything meaningful: 727 hypotheses sharing
    # one template cleared the 0.6 cosine bar pairwise and minted a
    # near-complete graph, which retrieval cannot rank because spreading
    # activation over it reaches everything equally.
    #
    # Depth-0 only, therefore. Not a cap on ambition — derived beliefs already
    # carry DERIVED_FROM edges to their parents, which is the honest structure
    # for how they relate.
    entity_tier = [n for n in nodes if getattr(n, "derivation_depth", 0) == 0]
    labeled = [(n, label_of(n)) for n in entity_tier]
    labeled = [(n, lab) for n, lab in labeled if lab]
    if len(labeled) > MAX_PAIRWISE_NODES:
        # Deterministic truncation, and SAID OUT LOUD rather than silent: a
        # capped pass that reports a full number is how a coverage claim
        # quietly stops being true.
        labeled.sort(key=lambda t: (-t[0].last_active_t_ms, t[0].id))
        labeled = labeled[:MAX_PAIRWISE_NODES]
    if len(labeled) < 2:
        return 0
    vecs = {n.id: list(embedder.embed(lab)) for n, lab in labeled}

    touched = 0
    for i, (a, _la) in enumerate(labeled):
        for (b, _lb) in labeled[i + 1:]:
            if a.id == b.id:
                continue
            sim = _cosine(vecs[a.id], vecs[b.id])
            if sim < threshold:
                continue
            existing = graph_store.edge_between(a.id, b.id, EdgeType.SEMANTIC)
            if existing is None:
                graph_store.upsert_edge(Edge(
                    src=a.id, dst=b.id, type=EdgeType.SEMANTIC, weight=sim,
                    evidence_count=1, last_reinforced_t_ms=t_ms))
                touched += 1
                continue
            fresh = (since_t_ms is None
                     or a.last_active_t_ms > since_t_ms
                     or b.last_active_t_ms > since_t_ms)
            if fresh:
                existing.reinforce(t_ms, delta=sim)
                graph_store.upsert_edge(existing)
                touched += 1
            elif existing.weight != sim:
                # Neither end moved: this is the SAME similarity, recomputed.
                # Refresh the value so the edge stays true to the current
                # labels, but do not call it evidence.
                existing.weight = sim
                graph_store.upsert_edge(existing)
    return touched
