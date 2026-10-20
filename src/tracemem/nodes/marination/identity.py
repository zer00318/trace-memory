"""OWNS: cross-hemisphere identity — one person, reached CONSERVATIVELY
(analysis gap E; the founder's 'aim for one file, reach it conservatively').

When the same name appears as two distinct entity nodes — the OWNED Tessa in
your mail and the LIVED Tessa you met on screen, or two contexts the resolver
kept apart — the honest move is NOT to fuse them. A wrong merge corrupts
silently and cannot be undone (the graveyard's two-shirts-one-Ross lesson). So
this pass only ever LINKS: a weak SAME_AS 'might be' edge whose weight rises
with corroboration (a shared context) and with repetition across nights. Merge
is a separate, high-threshold act — never the default, and not done here.

Hemisphere-agnostic by construction: identity is identity whatever sensed it,
so the same machinery links OWNED↔LIVED, LIVED↔LIVED, or OWNED↔OWNED.
"""
from __future__ import annotations

from collections import defaultdict
from typing import List

from tracemem.domain import Edge, EdgeType, NodeKind
from tracemem.nodes.accrual import accrue, names_of

SAME_AS_BASE = 0.4              # name match alone: a weak candidate
SHARED_CONTEXT_BOOST = 0.3     # + a corroborating shared neighbour
REINFORCE_DELTA = 0.1          # each night the pairing recurs, a little stronger
MERGE_THRESHOLD = 0.8          # at/above: a merge MAY act (surfaced for review)
AUTO_MERGE_THRESHOLD = 0.95    # OVERWHELMING corroboration: the night merges on
                               # its own. Only many reinforced nights of a
                               # name+shared-context match reach here, and every
                               # merge is audited (absorbed node tombstoned), so
                               # the wrong-merge risk is bounded and inspectable.


def _same_family(a: str, b: str) -> bool:
    """Two names that can be the same person: equal, or one a prefix of the
    other ("Ben"/"Benjamin", "Bart"/"Bartholomew") — the same rule the resolver
    matches on."""
    a, b = str(a).strip().lower(), str(b).strip().lower()
    if not a or not b:
        return False
    return a == b or (min(len(a), len(b)) >= 3
                      and (a.startswith(b) or b.startswith(a)))


def one_identity(names) -> bool:
    """Whether these names can ALL belong to one person.

    THE CASCADE THIS PREVENTS, reproduced 2026-08-24 and found on the
    founder's live graph, where ONE node carried sixteen people:

        Bartholomew · Bart · Rory · Pam · Rosa · Hana · Nora · Fred ·
        Cora · Quentin · Wade · Adele · Vince · Reid · Ivy · Eli

    `same_as_pass` groups nodes by EACH name they carry. A node that
    legitimately holds two names — "Bart" and "Bartholomew", a correct prefix
    match — therefore gets a SAME_AS edge to every node named Bart AND every
    node named Bartholomew. Both reinforce +0.1 a night to the 0.95 auto-merge
    bar, both merge in, and the fused node now carries more names and bridges
    wider. Four nights a hop; six days of capture was enough.

    Merging two people is the worst error this memory can make. It is not a
    wrong answer, it is a wrong PERSON — silently, in every answer afterwards,
    with the evidence of both folded together so the mistake looks corroborated.
    So a merge must leave a node whose names could all be one human.
    """
    names = [n for n in names if str(n).strip().lower() != "a person"]
    return all(_same_family(a, b) for i, a in enumerate(names)
               for b in names[i + 1:])


def _shares_context(graph_store, a_id: str, b_id: str) -> bool:
    na = {n.id for n in graph_store.neighbors(a_id)}
    nb = {n.id for n in graph_store.neighbors(b_id)}
    return bool((na & nb) - {a_id, b_id})


def same_as_pass(graph_store, t_ms: int) -> int:
    """Mint/reinforce SAME_AS 'might be' edges between distinct entity nodes
    sharing a name. Returns edges touched. Never merges."""
    by_name = defaultdict(dict)                     # name -> {id: node} (dedup)
    for n in graph_store.nodes.values():
        if n.kind is not NodeKind.ENTITY:
            continue
        for nm in names_of(n):
            by_name[nm.lower()][n.id] = n

    touched = 0
    for _name, nodes in by_name.items():
        uniq = list(nodes.values())
        if len(uniq) < 2:
            continue
        for i, a in enumerate(uniq):
            for b in uniq[i + 1:]:
                # Never build a bridge between two people: if the union of
                # their names cannot be one human, no amount of shared
                # context makes them the same one.
                if not one_identity(names_of(a) + names_of(b)):
                    continue
                existing = graph_store.edge_between(a.id, b.id, EdgeType.SAME_AS)
                if existing is None:
                    weight = SAME_AS_BASE
                    if _shares_context(graph_store, a.id, b.id):
                        weight += SHARED_CONTEXT_BOOST
                    graph_store.upsert_edge(Edge(
                        src=a.id, dst=b.id, type=EdgeType.SAME_AS,
                        weight=min(weight, 0.99),   # a candidate never reaches certainty
                        evidence_count=1, last_reinforced_t_ms=t_ms))
                else:
                    existing.reinforce(t_ms, delta=REINFORCE_DELTA)
                    existing.weight = min(existing.weight, 0.99)
                    graph_store.upsert_edge(existing)
                touched += 1
    return touched


def merge_ready(graph_store) -> List[Edge]:
    """SAME_AS candidates that corroboration has pushed past the merge bar —
    the ONLY ones a (separate, deliberate) merge step may act on. Surfaced, not
    acted upon: fusing two identities is high-threshold and irreversible, so it
    stays a named decision, never a silent side effect of the night."""
    return [e for e in graph_store.edges.values()
            if e.type is EdgeType.SAME_AS and e.weight >= MERGE_THRESHOLD]


def merge_identity(graph_store, keep_id: str, absorb_id: str, t_ms: int) -> bool:
    """The deliberate merge: fold absorb's attributes into keep (evidence, never
    lost), then rewire absorb's edges onto keep and retire absorb to the
    graveyard. Called ONLY on strong, corroborated evidence — never a silent
    side effect of the night. Returns True if a merge happened.

    Attributes fold through accrual, so provenance and citations survive: keep
    ends up knowing everything both nodes knew, and the merge itself is audited
    (absorb is tombstoned, not vaporized) so a wrong merge is inspectable."""
    absorb = graph_store.get_node(absorb_id)
    keep = graph_store.get_node(keep_id)
    if absorb is None or keep is None or keep_id == absorb_id:
        return False
    # Checked AGAIN here, not only where the edge is made: an edge can
    # predate this rule, and a stale edge must not be able to fuse two people
    # merely because it was written before anyone was looking.
    if not one_identity(names_of(keep) + names_of(absorb)):
        return False
    for a in absorb.attributes:
        for ep in a.source_episode_ids:
            accrue(keep, a.key, a.value, a.grade, a.confidence, ep, a.t_ms)
    graph_store.upsert_node(keep)
    graph_store.merge_into(keep_id, absorb_id, t_ms)
    return True


def auto_merge_pass(graph_store, t_ms: int) -> List[tuple]:
    """Autonomously merge ONLY the overwhelmingly-corroborated identity pairs
    (weight >= AUTO_MERGE_THRESHOLD). The node with more accrued knowledge is
    kept; the other is folded in and tombstoned. Below the auto bar, candidates
    stay surfaced (merge_ready) for a human call. Returns (keep, absorb) pairs."""
    done: List[tuple] = []
    for e in list(merge_ready(graph_store)):
        if e.weight < AUTO_MERGE_THRESHOLD:
            continue
        a = graph_store.get_node(e.src)
        b = graph_store.get_node(e.dst)
        if a is None or b is None:
            continue
        # keep the richer node (more evidence); id breaks ties, deterministically
        keep, absorb = ((a, b) if (len(a.attributes), a.id) >= (len(b.attributes), b.id)
                        else (b, a))
        if merge_identity(graph_store, keep.id, absorb.id, t_ms):
            done.append((keep.id, absorb.id))
    return done
