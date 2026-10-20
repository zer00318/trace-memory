"""OWNS: the 10^7→10^3 collapse — co-activation candidates, budgeted, dedup'd
(spec 04 §3, ruling A1). Cost scales with the hot set, never with |V|."""
from __future__ import annotations

import hashlib
import json
from typing import List, Tuple

from tracemem.domain import EdgeType
from tracemem.domain.activation import activation_now
from tracemem.nodes.accrual import sightings
from tracemem.nodes.graph_store import pair_key

EDGE_AFFINITY = {EdgeType.PRECEDES: 1.2, EdgeType.CO_OCCURS: 1.0,
                 EdgeType.SEMANTIC: 0.9, EdgeType.LOCATED_AT: 0.6, None: 0.4}

# A THING SEEN ONCE IS NOT YET A THING (2026-07-29).
#
# Read from the founder's own first digest, on his real store: roughly four in
# five belief lines related things that do not exist. `esa.com`, `a.com`,
# `e.com`, `youtube.co`, `chesa.com`, `aura.co` are OCR truncations of real
# domains; `Northside`, `Peru`, `Hub`, `Thinking`, `before further edits`,
# `critical docun` are sentence fragments the person/topic grammars caught. The
# combination engine was behaving correctly and faithfully relating garbage.
#
# The birth confidence floor cannot catch these: Vision reports HIGH confidence
# on a clean misread. What separates them from real things is not how sure the
# perceiver was in one instant — it is whether they ever came back. A domain
# you actually visit appears across many moments; a truncation appears in the
# one frame that clipped it.
#
# So corroboration gates COMBINATION, not birth. The node is still created and
# the raw claim is kept forever, so a thing that recurs graduates on its own
# with no special case — it simply may not become the subject of a derived
# belief until the world has said it twice. This is also the brain-faithful
# rule: consolidation follows repetition.
#
# UNCALIBRATED. 2 is the smallest number that means "more than once", which is
# the actual principle; the founder-grading loop owns moving it.
MIN_SIGHTINGS_TO_COMBINE = 2

# A node present in more than this share of all episodes carries no
# discriminating information, and must never be the SUBJECT of a derived
# belief. Measured on the founder's graph, 2026-08-20, over 168 episodes:
#
#     dev:mac-1        76.8% of episodes   (the device id — in everything)
#     Brave Browser    76.2%               (the only browser he uses)
#     youtube.com      57.7%
#     ...median node:   1 episode
#
# The night, once it finally ran, spent that freedom relating these to
# everything else: "dev:mac-1 and Thriller: both repeatedly present with
# youtube.com". The engine was behaving correctly and faithfully relating a
# constant. This is inverse document frequency, and the combination tier
# never had it: "both repeatedly present with X" is only interesting when X
# is not present with EVERYTHING.
#
# It is a gate on PROMOTION, not on recording: the co-occurrence edges stay
# in the graph, and a question about "what app do I use" still reads them.
UBIQUITY_CEILING = 0.5

# ...and it takes this many episodes before the share means anything. Over
# two episodes "appears in 100% of them" is not ubiquity, it is a sample size
# of two, and gating on it would silently switch combination OFF for every
# new user and every test fixture. A statistic that fires hardest when it
# knows least is worse than no statistic.
MIN_EPISODES_FOR_UBIQUITY = 20


def episode_coverage(graph_store):
    """node id -> the share of all known episodes it appears in.

    Computed from the entity tier only, because derived nodes cite the
    episodes of their parents and would double-count the very ubiquity this
    is trying to measure.
    """
    per_node = {}
    all_eps = set()
    for node in graph_store.nodes.values():
        if getattr(node, "derivation_depth", 0) != 0:
            continue
        eps = set()
        for a in node.attributes:
            eps.update(a.source_episode_ids)
        per_node[node.id] = eps
        all_eps |= eps
    total = len(all_eps)
    if total < MIN_EPISODES_FOR_UBIQUITY:
        return {}
    return {nid: len(eps) / total for nid, eps in per_node.items()}


def ubiquitous(graph_store, ceiling: float = UBIQUITY_CEILING) -> set:
    return {nid for nid, share in episode_coverage(graph_store).items()
            if share > ceiling}


def corroborated(node) -> bool:
    """Has the world shown us this in more than one moment?

    Derived nodes are exempt: a meta node's standing comes from its parents
    (which were themselves gated) and from the judge, not from being re-seen —
    it was never 'seen' at all.
    """
    if getattr(node, "derivation_depth", 0) >= 1:
        return True
    return sightings(node) >= MIN_SIGHTINGS_TO_COMBINE


def _evidence_json(node) -> str:
    attrs = [{
        "key": a.key,
        "value": a.value,
        "grade": int(a.grade),
        "confidence": a.confidence.value,
        "episodes": sorted(a.source_episode_ids),
        "first_t_ms": a.first_t_ms,
        "t_ms": a.t_ms,
    } for a in node.attributes]
    attrs.sort(key=lambda a: (a["key"], a["value"], a["episodes"],
                              a["first_t_ms"], a["t_ms"]))
    return json.dumps({"id": node.id, "kind": node.kind.value,
                       "depth": node.derivation_depth,
                       "builder_version": node.builder_version,
                       "attributes": attrs},
                      sort_keys=True, separators=(",", ":"))


def evidence_fingerprint(node_a, node_b, _memo=None) -> str:
    """``_memo`` (node id -> evidence JSON) lets one selection pass serialise
    each node once instead of once per pair it appears in. With compact
    separators a two-element JSON list is exactly "[" + a + "," + b + "]", so
    the bytes hashed are identical either way."""
    def evidence(node):
        if _memo is None:
            return _evidence_json(node)
        text = _memo.get(node.id)
        if text is None:
            text = _memo[node.id] = _evidence_json(node)
        return text

    first, second = sorted(((node_a.id, evidence(node_a)),
                            (node_b.id, evidence(node_b))),
                           key=lambda pair: pair[0])
    sig = "[" + first[1] + "," + second[1] + "]"
    return hashlib.sha256(sig.encode()).hexdigest()[:16]


def priority(node_a, node_b, graph_store, t_ms: int) -> float:
    act_a = activation_now(node_a.activation, node_a.last_active_t_ms, t_ms)
    act_b = activation_now(node_b.activation, node_b.last_active_t_ms, t_ms)
    joint = act_a * act_b
    edge = graph_store.edge_between(node_a.id, node_b.id)
    linked_by_meta = any(
        e.type == EdgeType.DERIVED_FROM for e in graph_store.edges_of(node_a.id)
        if node_b.id in (e.src, e.dst))
    novelty = 0.0 if linked_by_meta else 1.0
    surprise = max(node_a.salience_ema, node_b.salience_ema)
    affinity = EDGE_AFFINITY.get(edge.type if edge else None, 0.4)
    return joint * (0.5 + 0.5 * novelty) * (0.5 + 0.5 * surprise) * affinity


def select_candidates(graph_store, t_ms: int, budget_b: int,
                      theta_hot: float = 0.3,
                      lease=None) -> List[Tuple[object, object, float]]:
    """Rank the pairs worth thinking about — and be interruptible while doing it.

    `lease` is what stops this from becoming the whole night. MEASURED on the
    founder's graph (851 nodes, ~1,800 edges) this call takes 1.85 SECONDS
    against a 2.0-second slice: two-hop expansion from 137 hot nodes over a
    dense co-occurrence graph. So the pair loop's first `preempted()` check
    fired every single time, not one pair was ever processed, the pair ledger
    never advanced, and the identical 1.85s was spent again 30 seconds later.
    Building the work list WAS the work.

    Stopping early is safe because the result is a PRIORITY ORDER, not a
    correctness requirement: a partial list processed now advances the ledger
    monotonically, and whatever was missed is still hot on the next slice.
    Returning nothing forever is the only outcome that cannot recover."""
    everywhere = ubiquitous(graph_store)
    hot = [h for h in graph_store.nodes_with_activation_ge(theta_hot, t_ms)
           if corroborated(h) and h.id not in everywhere]

    # ONE PASS OVER THE GRAPH, NOT ONE PER HOT NODE (2026-10-14). On the
    # live graph a two-hop neighbourhood is most of the graph, so asking the
    # store for each hot node's neighbours re-decrypted nearly every node
    # per hot node, and every pair re-serialised both nodes' evidence: N3
    # ran 9.7-19.4s against a 10s slice. Each node is now opened and
    # serialised at most once per selection, and the deadline is polled per
    # pair, not per hot node.
    opened = {h.id: h for h in hot}
    ids_of = getattr(graph_store, "neighbor_ids", None)
    prints = {}

    def neighbours(node_id):
        if ids_of is None:
            return graph_store.neighbors(node_id, hops=2)
        out = []
        for other in ids_of(node_id, hops=2):
            if other not in opened:
                opened[other] = graph_store.get_node(other)
            if opened[other] is not None:
                out.append(opened[other])
        return out

    def stopped():
        return lease is not None and lease.preempted()

    scored = {}
    for h in hot:
        if stopped():
            break
        cut = False
        for n in neighbours(h.id):
            if not corroborated(n) or n.id in everywhere:
                continue                      # seen once: not yet a thing
            key = pair_key(h.id, n.id)
            if key in scored:
                continue
            if stopped():
                cut = True
                break
            if graph_store.ledger_fresh(key, evidence_fingerprint(h, n, prints)):
                continue                      # I4: evidence unchanged → skip
            scored[key] = (h, n, priority(h, n, graph_store, t_ms))
        if cut:
            break
    ranked = sorted(scored.values(), key=lambda t: t[2], reverse=True)
    return ranked[:budget_b]
