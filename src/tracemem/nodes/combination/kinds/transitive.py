"""OWNS: shared-neighbor composition — pure graph walk, ZERO LLM (spec 04 §4).

a—c and b—c with composable edge types ⇒ propose a~b through c.
"""
from __future__ import annotations

from tracemem.domain import EdgeType
from tracemem.nodes.accrual import name_or_id
from tracemem.nodes.combination.proposal import NONE, Proposal

_COMPOSABLE = {
    (EdgeType.CO_OCCURS, EdgeType.CO_OCCURS): "both repeatedly present with",
    (EdgeType.LOCATED_AT, EdgeType.LOCATED_AT): "both located at",
}

# THE UBIQUITY GUARD (2026-07-29). A `via` that neighbours a large fraction of
# the graph composes EVERY pair and therefore distinguishes nothing — the
# social-graph IDF that `nodes/birth/resolver._discrimination` already applies
# to person resolution ("the app you use for everything and the wifi you
# always sit on co-occur with the whole graph and therefore identify nobody").
#
# This guard EXISTED here and was hardcoded to `NodeKind.PLACE`, so the lesson
# was learned for places and missed for everything else. The first production
# night run then found one ubiquitous non-place node and composed 727
# hypotheses through it. A fraction, not a fixed degree, so the rule means the
# same thing on day 1 and on day 900.
#
# UNCALIBRATED: 0.25 says "neighbours more than a quarter of everything". No
# probe has run; it is deliberately loose so it removes only the genuinely
# ubiquitous.
UBIQUITY_FRACTION = 0.25


def _is_ubiquitous(graph_store, node_id: str) -> bool:
    try:
        total = len(graph_store.nodes)
    except Exception:                       # a backend without a cheap count
        return False
    if total < 8:                           # too small for "ubiquitous" to mean anything
        return False
    return len(graph_store.edges_of(node_id)) > UBIQUITY_FRACTION * total


def _types_by_neighbour(graph_store, node_id):
    """neighbour id -> EVERY edge type between it and this node.

    A SET, AND THAT IS THE WHOLE POINT (fixed 2026-08-28). This was a dict
    comprehension keeping one type per neighbour, so whichever edge
    `edges_of` yielded last won and the others vanished.

    That is not a rare collision — it is the normal state of the graph, every
    night. `run_night` runs N0 SUCCESSION before N3 COMBINE, and succession
    hands PRECEDES to the nodes of consecutive episodes. Any neighbour a node
    both sits beside and precedes therefore arrived here as PRECEDES only,
    (PRECEDES, PRECEDES) is not composable, and the composition returned
    nothing.

    WITNESSED on a four-day rhythm built through the real upkeep path: two
    topics sharing a co-occurrence neighbour composed correctly before
    `succession_pass` ran and returned NONE immediately after it — so
    co-occurrence composition was switched off for exactly the pairs that
    have a temporal rhythm, which are the pairs most worth thinking about.
    """
    out = {}
    for e in graph_store.edges_of(node_id):
        out.setdefault(_other(e, node_id), set()).add(e.type)
    return out


def detect(node_a, node_b, graph_store) -> Proposal:
    from tracemem.domain import NodeKind

    edges_a = _types_by_neighbour(graph_store, node_a.id)
    edges_b = _types_by_neighbour(graph_store, node_b.id)
    for shared in sorted(set(edges_a) & set(edges_b)):
        if shared in (node_a.id, node_b.id):
            continue
        # Every composable combination these two actually have with this
        # neighbour, in a stable order so the same graph always yields the
        # same belief rather than one that depends on set iteration.
        pairs = sorted(
            ((ta, tb) for ta in edges_a[shared] for tb in edges_b[shared]
             if (ta, tb) in _COMPOSABLE),
            key=lambda p: (p[0].value, p[1].value))
        if not pairs:
            continue
        via = graph_store.get_node(shared)
        # ...and the same lesson as the ubiquity guard above, applied by
        # MEASUREMENT rather than by kind: any via that touches a large
        # fraction of the graph makes every pair look related.
        if _is_ubiquitous(graph_store, shared):
            continue
        for pair in pairs:
            # containers can't be the via of a co-occurrence composition:
            # everything co-occurs at a place, so "A and B are both often at
            # home" is trivia, not a derivation (the context-IDF lesson at
            # combination level). LOCATED_AT∘LOCATED_AT composition still
            # expresses shared location, deliberately and legibly.
            if (pair == (EdgeType.CO_OCCURS, EdgeType.CO_OCCURS)
                    and via is not None and via.kind == NodeKind.PLACE):
                continue
            episodes = _episodes_of(node_a) | _episodes_of(node_b)
            return Proposal(
                kind="transitive",
                # NAMES, not ids. A belief the founder cannot read is a belief
                # he cannot grade, and F-GRADE makes his grade the highest
                # evidence in the system. Two pairs that reduce to the same
                # sentence now also DEDUPE, because judge._duplicate_exists
                # keys on node_id(kind, claim_text) — which is why this one
                # change collapses a flood of near-identical hypotheses.
                claim_text="%s and %s: %s %s" % (
                    name_or_id(node_a), name_or_id(node_b), _COMPOSABLE[pair],
                    name_or_id(via, shared)),
                parent_node_ids=(node_a.id, node_b.id),
                supported_by=tuple(sorted(episodes)),
                extras={"via": shared})
    return NONE


def _other(edge, node_id):
    return edge.dst if edge.src == node_id else edge.src


def _episodes_of(node):
    out = set()
    for a in node.attributes:
        out.update(a.source_episode_ids)
    return out
