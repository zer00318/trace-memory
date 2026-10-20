"""OWNS: safe forgetting + earned permanence (spec 04 §6, I1).

Prunes only META (depth>=1) that is cold AND (re-derivable OR floor-grade).
Raw is structurally out of reach (the pruner holds no ClaimStore reference).
reconfirm_scan: fresh raw evidence lets meta climb — never above the evidence.
"""
from __future__ import annotations

from typing import List

from tracemem.domain import Grade, NodeKind
from tracemem.domain.activation import activation_now
from tracemem.nodes.retention.policy import RetentionPolicy

_RE_DERIVABLE = {NodeKind.PATTERN, NodeKind.QUESTION}   # deterministic kinds (04 §4)


def prune(graph_store, policy: RetentionPolicy, t_ms: int) -> List[str]:
    pruned = []
    for node in list(graph_store.nodes.values()):
        if node.derivation_depth == 0:
            continue                                     # entities are not the cache
        act = activation_now(node.activation, node.last_active_t_ms, t_ms)
        cold = act < policy.cold_floor
        grade = max((a.grade for a in node.attributes), default=Grade.FLOOR)
        re_derivable = node.kind in _RE_DERIVABLE
        if cold and (re_derivable or grade <= Grade.FLOOR):
            # Retire, don't vaporize: the decay trajectory is the moat, so a
            # forgotten belief is tombstoned (queryable as "you used to think
            # X"), not deleted. Re-derivable kinds can be rebuilt from the log
            # too, but the tombstone makes the history a cheap query.
            graph_store.retire_meta(node.id, t_ms)
            pruned.append(node.id)
    return pruned


def reconfirm_scan(graph_store, new_episodes, claims_by_episode) -> List[str]:
    """A meta claim mentioned again by fresh raw climbs one grade (capped) and
    reactivates. Matching v1: token overlap between claim value and new text."""
    climbed = []
    metas = [n for n in graph_store.nodes.values() if n.derivation_depth >= 1]
    for ep in new_episodes:
        texts = " ".join(c.text.lower() for c in claims_by_episode.get(ep.id, []))
        for m in metas:
            for a in m.attributes:
                if a.key != "claim":
                    continue
                tokens = {t for t in a.value.lower().split() if len(t) > 3}
                if tokens and sum(1 for t in tokens if t in texts) / len(tokens) > 0.5:
                    a.grade = Grade(min(int(Grade.SPECIALIST), int(a.grade) + 1))
                    a.source_episode_ids = tuple(dict.fromkeys(
                        (*a.source_episode_ids, ep.id)))
                    m.activation = min(1.0, m.activation + 0.3)
                    graph_store.upsert_node(m)
                    climbed.append(m.id)
    return climbed
