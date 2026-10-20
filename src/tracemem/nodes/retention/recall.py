"""OWNS: recall of RETIRED beliefs — the moat made answerable.

Tombstoning (pruner.retire_meta) keeps a forgotten belief and when it died; this
turns that graveyard into an answer. 'You used to think X' is a real question —
the decay trajectory is the thing a raw model can never reconstruct — so the
store keeps it and this reads it back, matched by the query's terms, most-recent
death first. Read-only over the tombstone graveyard; it never revives anything.
"""
from __future__ import annotations

from typing import List, Tuple

from tracemem.domain import Node


def recall_retired(graph_store, terms, limit: int = 5) -> List[Tuple[Node, int]]:
    """Retired beliefs whose text overlaps `terms`, ranked by overlap then
    recency of retirement. Returns (node, retired_t_ms) — the belief and when
    you stopped holding it."""
    want = {t.lower() for t in terms if len(t) > 2}
    if not want:
        return []
    scored = []
    for node, retired_t_ms in graph_store.retired():
        text = " ".join(a.value.lower() for a in node.attributes)
        overlap = sum(1 for t in want if t in text)
        if overlap:
            scored.append((overlap, retired_t_ms, node))
    scored.sort(key=lambda s: (-s[0], -s[1]))
    return [(node, t) for _overlap, t, node in scored[:limit]]
