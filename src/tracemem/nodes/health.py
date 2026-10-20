"""OWNS: what the graph looks like from above, as numbers.

`Graph health / The metrics`. Pure queries, no judgment calls — the brief is
explicit about that and it is the right constraint: a health check that
interprets is a health check that can be argued with, and the point is to
have something that cannot.

WHY IT MATTERS MORE THAN IT SOUNDS. On 2026-08-25 the morning surface was
built, and only when it ran against the real graph did anyone discover that
EVERY belief in it read `recurring: page=/watch observed in 230 distinct
moments` — the memory reporting its own index. Nothing was wrong with the
detectors. Nothing raised. The graph had 378 nodes and a healthy-looking
node count, and the thing that was broken was invisible from every angle
except reading the beliefs out loud.

`sayable_belief_rate` is that discovery turned into a number. It is the one
metric here that is about QUALITY rather than shape, and it is deliberately
mechanical: a belief is sayable if a person could read it and recognise it as
being about their life, which in practice means it does not carry a raw
schema key. Crude, and a crude number that would have caught it beats a
subtle one nobody computes.

ORPHANS AND GHOSTS ARE NOT FAILURES. A node with no edges may be something
seen once; a ghost is a thing the machine knows it has not identified. Both
are honest states, and both compound quietly — the numbers exist so a trend
is visible before it is a problem, not so anyone can chase them to zero.
"""
from __future__ import annotations

from typing import Dict, Optional

_MACHINE_KEYS = ("page=", "app=", "host=", "url=", "site=", "path=",
                 "bundle=", "title=", "place=", "device=", "recurring:")


def _nodes(graph_store):
    try:
        return list(graph_store.nodes.values())
    except Exception:                                        # noqa: BLE001
        return []


def _has_edge(graph_store, node_id) -> bool:
    try:
        return bool(list(graph_store.neighbors(node_id, hops=1)))
    except Exception:                                        # noqa: BLE001
        return False


def sayable(text: str) -> bool:
    """Could a person read this and recognise it as being about them?"""
    low = str(text or "").lower()
    return bool(low) and not any(m in low for m in _MACHINE_KEYS)


def vitals(graph_store, now_ms: int = 0) -> Dict[str, object]:
    """Every number, computed from the store. No interpretation."""
    from tracemem.domain.node import EMERGENT_KINDS
    from tracemem.nodes.accrual import label_of

    nodes = _nodes(graph_store)
    total = len(nodes)
    orphans = ghosts = beliefs = sayable_beliefs = 0
    oldest_ghost_ms: Optional[int] = None
    by_kind: Dict[str, int] = {}

    for n in nodes:
        kind = getattr(getattr(n, "kind", None), "name", "?")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        label = label_of(n)
        if not label:
            # A GHOST IS A THING IT KNOWS IT HAS NOT IDENTIFIED. That is an
            # honest state, not a fault — but a growing pile of them means
            # the resolver is losing ground, and nothing said so before.
            ghosts += 1
            seen = int(getattr(n, "last_active_t_ms", 0) or 0)
            if seen and (oldest_ghost_ms is None or seen < oldest_ghost_ms):
                oldest_ghost_ms = seen
        if not _has_edge(graph_store, n.id):
            orphans += 1
        if getattr(n, "kind", None) in EMERGENT_KINDS:
            beliefs += 1
            if sayable(label):
                sayable_beliefs += 1

    return {
        "nodes": total,
        "by_kind": by_kind,
        "orphans": orphans,
        "orphan_rate": round(orphans / total, 3) if total else 0.0,
        "ghosts": ghosts,
        "oldest_ghost_age_ms": ((now_ms - oldest_ghost_ms)
                                if (oldest_ghost_ms and now_ms) else None),
        "beliefs": beliefs,
        "sayable_beliefs": sayable_beliefs,
        # THE ONE THAT WOULD HAVE CAUGHT IT. 378 nodes and a healthy node
        # count, and every belief a database row read aloud.
        "sayable_belief_rate": (round(sayable_beliefs / beliefs, 3)
                                if beliefs else None),
        "computed_at_ms": now_ms or None,
    }


def render(v: Dict[str, object]) -> str:
    """The vitals as a person reads them. Numbers with dates, no verdicts."""
    import time

    when = (time.strftime("%Y-%m-%d %H:%M",
                          time.localtime(v["computed_at_ms"] / 1000.0))
            if v.get("computed_at_ms") else "unknown time")
    out = ["GRAPH HEALTH  (computed %s)" % when,
           "  nodes            : %s" % v["nodes"],
           "  orphans          : %s (%.0f%%)"
           % (v["orphans"], 100.0 * (v["orphan_rate"] or 0)),
           "  ghosts           : %s" % v["ghosts"],
           "  beliefs          : %s" % v["beliefs"]]
    rate = v.get("sayable_belief_rate")
    if rate is None:
        out.append("  sayable beliefs  : no beliefs yet")
    else:
        out.append("  sayable beliefs  : %s of %s (%.0f%%)"
                   % (v["sayable_beliefs"], v["beliefs"], 100.0 * rate))
    return "\n".join(out)
